"""Small HTTP-to-UDS bridge for the isolated FlareSolverr controller."""

from __future__ import annotations

import os
import socket
import stat
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import BoundedSemaphore
from urllib.parse import urlsplit

SOCKET_PATH = os.environ.get("FLARE_API_UNIX_SOCKET", "/run/flaresolverr/api.sock")
MAX_REQUEST_BYTES = 16 * 1024 * 1024
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_RESPONSE_HEADER_BYTES = 64 * 1024
MAX_ACTIVE_REQUESTS = 2
CLIENT_IO_TIMEOUT_SECONDS = 15
UPSTREAM_TIMEOUT_SECONDS = 125


def route_status(method: str, target: str) -> int:
    parsed = urlsplit(target)
    if parsed.query or parsed.fragment or parsed.path not in {"/", "/health", "/v1"}:
        return 404
    if (method, parsed.path) not in {
        ("GET", "/"),
        ("GET", "/health"),
        ("POST", "/v1"),
    }:
        return 405
    return 200


def build_upstream_request(
    method: str, path: str, body: bytes, content_type: str | None = None
) -> bytes:
    """Build the narrow request forwarded to the private Waitress socket."""
    headers = [
        f"{method} {path} HTTP/1.1",
        "Host: flaresolverr.local",
    ]
    if method == "POST" and path == "/v1":
        media_type = (content_type or "").partition(";")[0].strip().lower()
        if media_type != "application/json":
            raise ValueError("FlareSolverr control accepts JSON only")
        # Waitress/Bottle relies on this header to populate request.json.
        headers.append("Content-Type: application/json")
    headers.extend((f"Content-Length: {len(body)}", "Connection: close", "", ""))
    return "\r\n".join(headers).encode("ascii") + body


class UnixHTTPConnection:
    def __init__(self, path: str) -> None:
        self.path = path

    def request(self, request: bytes) -> bytes:
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.settimeout(UPSTREAM_TIMEOUT_SECONDS)
        try:
            client.connect(self.path)
            client.sendall(request)
            response = bytearray()
            header_end = -1
            total = 0
            while True:
                block = client.recv(64 * 1024)
                if not block:
                    return bytes(response)
                total += len(block)
                if total > MAX_RESPONSE_BYTES + MAX_RESPONSE_HEADER_BYTES:
                    raise ValueError("FlareSolverr response exceeds the control limit")
                response.extend(block)
                if header_end < 0:
                    header_end = response.find(b"\r\n\r\n")
                    if header_end < 0 and len(response) > MAX_RESPONSE_HEADER_BYTES:
                        raise ValueError("FlareSolverr response headers exceed the control limit")
                    if header_end >= MAX_RESPONSE_HEADER_BYTES:
                        raise ValueError("FlareSolverr response headers exceed the control limit")
                elif len(response) - header_end - 4 > MAX_RESPONSE_BYTES:
                    raise ValueError("FlareSolverr response exceeds the control limit")
        finally:
            client.close()


class BoundedThreadingHTTPServer(ThreadingHTTPServer):
    """Keep slow API clients from creating unbounded request threads."""

    daemon_threads = True

    def __init__(self, server_address, handler_class):
        self._request_slots = BoundedSemaphore(MAX_ACTIVE_REQUESTS)
        super().__init__(server_address, handler_class)

    def process_request(self, request, client_address):
        if not self._request_slots.acquire(blocking=False):
            try:
                request.settimeout(1)
                request.sendall(
                    b"HTTP/1.1 503 Service Unavailable\r\n"
                    b"Content-Length: 0\r\nConnection: close\r\n\r\n"
                )
            except OSError:
                pass
            finally:
                self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._request_slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._request_slots.release()


class ControlHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def setup(self) -> None:
        self.request.settimeout(CLIENT_IO_TIMEOUT_SECONDS)
        super().setup()

    def log_message(self, _format: str, *args: object) -> None:
        # Avoid writing request metadata or URLs into shared logs.
        return

    def do_GET(self) -> None:
        self._forward()

    def do_POST(self) -> None:
        self._forward()

    def _forward(self) -> None:
        parsed = urlsplit(self.path)
        route_result = route_status(self.command, self.path)
        if route_result != 200:
            self.send_error(route_result)
            return

        raw_length = self.headers.get("Content-Length", "0")
        if not raw_length.isdecimal():
            self.send_error(400)
            return
        length = int(raw_length)
        if length > MAX_REQUEST_BYTES:
            self.send_error(413)
            return
        if self.headers.get("Transfer-Encoding") is not None:
            self.send_error(400)
            return
        if self.headers.get("Content-Encoding") is not None:
            self.send_error(400)
            return
        if self.command == "POST" and self.path == "/v1":
            media_type = self.headers.get("Content-Type", "").partition(";")[0].strip().lower()
            if media_type != "application/json":
                self.send_error(415)
                return
        body = self.rfile.read(length)
        if len(body) != length:
            self.send_error(400)
            return

        request_bytes = build_upstream_request(
            self.command,
            parsed.path,
            body,
            self.headers.get("Content-Type"),
        )
        try:
            response = UnixHTTPConnection(SOCKET_PATH).request(request_bytes)
            header_end = response.find(b"\r\n\r\n")
            if header_end < 0:
                raise ValueError("invalid FlareSolverr response")
            status_line, *headers = response[:header_end].split(b"\r\n")
            status_parts = status_line.split(b" ", 2)
            if len(status_parts) < 2 or not status_parts[1].isdigit():
                raise ValueError("invalid FlareSolverr status")
            status = int(status_parts[1])
            response_body = memoryview(response)[header_end + 4 :]
            if len(response_body) > MAX_RESPONSE_BYTES:
                raise ValueError("FlareSolverr response exceeds the control limit")
            self.send_response(status)
            forwarded_length = False
            for header in headers:
                name, separator, value = header.partition(b":")
                if not separator:
                    continue
                lowered = name.strip().lower()
                if lowered in {b"connection", b"transfer-encoding", b"server", b"date"}:
                    continue
                if lowered == b"content-length":
                    forwarded_length = True
                self.send_header(name.decode("latin-1"), value.strip().decode("latin-1"))
            if not forwarded_length:
                self.send_header("Content-Length", str(len(response_body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(response_body)
            self.close_connection = True
        except (OSError, TimeoutError, ValueError):
            self.send_error(502, "FlareSolverr control socket unavailable")


class UnixControlServer(BoundedThreadingHTTPServer):
    """Bind the private control socket with fixed consumer permissions."""

    address_family = socket.AF_UNIX

    def __init__(
        self,
        path: str,
        handler_class,
        *,
        directory_uid: int = 10003,
        directory_gid: int = 20000,
        directory_mode: int = 0o2710,
        socket_gid: int = 20000,
    ):
        self.socket_path = Path(path)
        self.directory_uid = directory_uid
        self.directory_gid = directory_gid
        self.directory_mode = directory_mode
        self.socket_gid = socket_gid
        self._socket_identity: tuple[int, int] | None = None
        super().__init__(path, handler_class)

    def server_bind(self) -> None:
        path = self.socket_path
        if not path.is_absolute():
            raise RuntimeError("Flare control socket path must be absolute")
        parent = path.parent.lstat()
        if (
            stat.S_ISLNK(parent.st_mode)
            or not stat.S_ISDIR(parent.st_mode)
            or parent.st_uid != self.directory_uid
            or parent.st_gid != self.directory_gid
            or stat.S_IMODE(parent.st_mode) != self.directory_mode
        ):
            raise RuntimeError("Flare control socket directory is invalid")
        try:
            existing = path.lstat()
        except FileNotFoundError:
            existing = None
        if existing is not None:
            if (
                stat.S_ISLNK(existing.st_mode)
                or not stat.S_ISSOCK(existing.st_mode)
                or existing.st_uid != os.getuid()
                or existing.st_gid != self.socket_gid
                or stat.S_IMODE(existing.st_mode) != 0o660
            ):
                raise RuntimeError("Flare control socket path is occupied")
            path.unlink()

        super().server_bind()
        created = path.lstat()
        self._socket_identity = (created.st_dev, created.st_ino)
        try:
            os.chown(path, os.getuid(), self.socket_gid)
            os.chmod(path, 0o660)
            final = path.lstat()
            if (
                stat.S_ISLNK(final.st_mode)
                or not stat.S_ISSOCK(final.st_mode)
                or final.st_uid != os.getuid()
                or final.st_gid != self.socket_gid
                or stat.S_IMODE(final.st_mode) != 0o660
                or (final.st_dev, final.st_ino) != self._socket_identity
            ):
                raise RuntimeError("Flare control socket permissions are invalid")
        except BaseException:
            self.server_close()
            raise

    def server_close(self) -> None:
        super().server_close()
        if self._socket_identity is None:
            return
        path = self.socket_path
        try:
            current = path.lstat()
            if (
                stat.S_ISSOCK(current.st_mode)
                and current.st_uid == os.getuid()
                and (current.st_dev, current.st_ino) == self._socket_identity
            ):
                path.unlink()
        except FileNotFoundError:
            pass
        finally:
            self._socket_identity = None


def main() -> None:
    unix_path = os.environ.get("FLARE_CONTROL_UNIX_SOCKET")
    server: BoundedThreadingHTTPServer
    if unix_path:
        server = UnixControlServer(unix_path, ControlHandler)
    else:
        server = BoundedThreadingHTTPServer(("0.0.0.0", 8191), ControlHandler)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
