"""Fail-closed HTTP CONNECT and absolute-form forward proxy.

The proxy accepts only HTTP/1.x, one request per client connection, and uses
the shared resolve-and-connect primitive for every origin connection. It does
not terminate TLS or inspect tunnel contents.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import os
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import urlsplit

from common.capture_destination import (
    BoundConnection,
    DestinationAuthority,
    DestinationConnectionError,
    DestinationDeniedError,
    DestinationError,
    DestinationResolutionError,
    DestinationTimeoutError,
    Resolver,
    parse_authority,
    resolve_and_connect,
    resolve_and_validate,
)

_HEADER_END = b"\r\n\r\n"
logger = logging.getLogger(__name__)
_TOKEN = re.compile(rb"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
_STATUS_TEXT = {
    400: "Bad Request",
    403: "Forbidden",
    408: "Request Timeout",
    413: "Payload Too Large",
    431: "Request Header Fields Too Large",
    502: "Bad Gateway",
    503: "Service Unavailable",
    504: "Gateway Timeout",
}
_HOP_HEADERS = {
    b"connection",
    b"keep-alive",
    b"proxy-connection",
    b"proxy-authenticate",
    b"proxy-authorization",
    b"te",
    b"trailer",
    b"transfer-encoding",
    b"upgrade",
}
_DNS_POLICY_PATH = b"/_control/public-dns-check"
_DNS_POLICY_HOST = b"capture-egress.internal:8080"
_DNS_POLICY_BODY_LIMIT = 512


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


async def _send_policy_result(writer: asyncio.StreamWriter, status: str) -> None:
    status_line = {
        "public": b"200 OK",
        "denied": b"403 Forbidden",
        "unavailable": b"503 Service Unavailable",
    }[status]
    body = json.dumps({"status": status}, separators=(",", ":")).encode("ascii")
    writer.write(
        b"HTTP/1.1 "
        + status_line
        + b"\r\nContent-Type: application/json\r\nConnection: close\r\nContent-Length: "
        + str(len(body)).encode("ascii")
        + b"\r\n\r\n"
        + body
    )
    await writer.drain()


class ProxyProtocolError(Exception):
    def __init__(self, status: int = 400) -> None:
        self.status = status
        super().__init__(_STATUS_TEXT.get(status, "Bad Request"))


class TransferLimitError(Exception):
    pass


@dataclass(frozen=True)
class ProxyConfig:
    allowed_ports: frozenset[int] = frozenset({80, 443})
    connect_timeout: float = 2.0
    request_timeout: float = 5.0
    max_connection_seconds: float = 120.0
    max_connections: int = 64
    max_header_bytes: int = 16 * 1024
    max_header_count: int = 100
    max_body_bytes: int = 2 * 1024 * 1024
    max_transfer_bytes: int = 8 * 1024 * 1024
    relay_chunk_bytes: int = 16 * 1024
    relay_idle_timeout: float = 15.0

    def __post_init__(self) -> None:
        if not self.allowed_ports or any(
            not isinstance(port, int)
            or isinstance(port, bool)
            or not 1 <= port <= 65535
            for port in self.allowed_ports
        ):
            raise ValueError("allowed_ports must contain valid TCP ports")
        for name in (
            "connect_timeout",
            "request_timeout",
            "max_connection_seconds",
            "relay_idle_timeout",
        ):
            value = getattr(self, name)
            maximum = {
                "connect_timeout": 30,
                "request_timeout": 60,
                "max_connection_seconds": 600,
                "relay_idle_timeout": 60,
            }[name]
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not 0 < value <= maximum
            ):
                raise ValueError(f"{name} must be > 0 and <= {maximum} seconds")
        for name in (
            "max_connections",
            "max_header_bytes",
            "max_header_count",
            "max_body_bytes",
            "max_transfer_bytes",
            "relay_chunk_bytes",
        ):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ValueError(f"{name} must be positive")
        if self.max_header_bytes > 128 * 1024:
            raise ValueError("max_header_bytes exceeds hard cap")
        if self.max_transfer_bytes > 64 * 1024 * 1024:
            raise ValueError("max_transfer_bytes exceeds hard cap")
        if self.relay_chunk_bytes > 64 * 1024:
            raise ValueError("relay_chunk_bytes exceeds hard cap")


DestinationConnector = Callable[[str], Awaitable[BoundConnection]]


class _ByteBudget:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.used = 0

    def charge(self, amount: int) -> None:
        if amount < 0 or self.used + amount > self.limit:
            raise TransferLimitError()
        self.used += amount


def _format_authority(host: str, port: int) -> str:
    rendered = f"[{host}]" if ":" in host else host
    return f"{rendered}:{port}"


def _parse_header_block(
    raw: bytes, config: ProxyConfig
) -> tuple[bytes, list[tuple[bytes, bytes]]]:
    if not raw.endswith(_HEADER_END) or len(raw) > config.max_header_bytes:
        raise ProxyProtocolError(431)
    if b"\n" in raw.replace(b"\r\n", b"") or b"\r" in raw.replace(b"\r\n", b""):
        raise ProxyProtocolError()
    lines = raw[:-4].split(b"\r\n")
    if not lines or len(lines) - 1 > config.max_header_count:
        raise ProxyProtocolError(431)
    start = lines[0]
    if not start or any(byte < 0x20 or byte > 0x7E for byte in start):
        raise ProxyProtocolError()
    headers: list[tuple[bytes, bytes]] = []
    for line in lines[1:]:
        if not line or line[:1] in (b" ", b"\t") or b":" not in line:
            raise ProxyProtocolError()
        name, value = line.split(b":", 1)
        if not _TOKEN.fullmatch(name) or any(
            (byte < 0x20 and byte != 0x09) or byte == 0x7F for byte in value
        ):
            raise ProxyProtocolError()
        headers.append((name.lower(), value.strip(b" \t")))
    return start, headers


def _one_header(
    headers: list[tuple[bytes, bytes]], name: bytes, *, required: bool = False
) -> bytes | None:
    values = [value for key, value in headers if key == name]
    if len(values) > 1 or (required and len(values) != 1):
        raise ProxyProtocolError()
    return values[0] if values else None


def _validated_host_header(
    headers: list[tuple[bytes, bytes]], expected: DestinationAuthority
) -> None:
    raw_host = _one_header(headers, b"host", required=True)
    if raw_host is None:
        raise ProxyProtocolError()
    try:
        host_text = raw_host.decode("ascii")
        if ":" not in host_text.rsplit("]", 1)[-1]:
            host_text = _format_authority(host_text.strip("[]"), expected.port)
        supplied = parse_authority(host_text)
    except (UnicodeError, DestinationError) as exc:
        raise ProxyProtocolError() from exc
    if (supplied.host, supplied.port) != (expected.host, expected.port):
        raise ProxyProtocolError()


def _absolute_http_target(target: bytes) -> tuple[DestinationAuthority, bytes]:
    try:
        text = target.decode("ascii")
        parsed = urlsplit(text)
        if (
            parsed.scheme.lower() != "http"
            or not parsed.netloc
            or parsed.netloc.endswith(":")
            or parsed.username is not None
            or parsed.password is not None
            or "#" in text
        ):
            raise ProxyProtocolError()
        host = parsed.hostname
        port = 80 if parsed.port is None else parsed.port
        if host is None:
            raise ProxyProtocolError()
        authority = parse_authority(_format_authority(host, port))
    except (UnicodeError, ValueError, DestinationError) as exc:
        if isinstance(exc, ProxyProtocolError):
            raise
        raise ProxyProtocolError() from exc
    path = parsed.path or "/"
    if not path.startswith("/"):
        raise ProxyProtocolError()
    origin_target = path + (f"?{parsed.query}" if parsed.query else "")
    return authority, origin_target.encode("ascii")


async def _read_header(reader: asyncio.StreamReader, config: ProxyConfig) -> bytes:
    try:
        return await asyncio.wait_for(
            reader.readuntil(_HEADER_END), timeout=config.request_timeout
        )
    except asyncio.LimitOverrunError as exc:
        raise ProxyProtocolError(431) from exc
    except asyncio.IncompleteReadError as exc:
        raise ProxyProtocolError() from exc
    except TimeoutError as exc:
        raise ProxyProtocolError(408) from exc


async def _send_error(writer: asyncio.StreamWriter, status: int) -> None:
    phrase = _STATUS_TEXT.get(status, "Bad Request")
    body = f"{status} {phrase}\n".encode("ascii")
    writer.write(
        f"HTTP/1.1 {status} {phrase}\r\n".encode("ascii")
        + b"Connection: close\r\nContent-Type: text/plain\r\nContent-Length: "
        + str(len(body)).encode("ascii")
        + b"\r\n\r\n"
        + body
    )
    try:
        await writer.drain()
    except (ConnectionError, OSError):
        pass


def configure_model_proxy_logging() -> None:
    """Emit bounded model-proxy diagnostics without changing root logging."""
    if not any(
        getattr(handler, "_model_proxy_handler", False) for handler in logger.handlers
    ):
        handler = logging.StreamHandler()
        handler.setLevel(logging.INFO)
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))
        handler._model_proxy_handler = True  # type: ignore[attr-defined]
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False


class CaptureEgressProxy:
    """One-request-per-connection, bounded HTTP forward proxy."""

    def __init__(
        self,
        config: ProxyConfig | None = None,
        *,
        destination_connector: DestinationConnector | None = None,
        resolver: Resolver | None = None,
        enable_dns_policy_rpc: bool = False,
        trace_http_events: bool = False,
    ) -> None:
        self.config = config or ProxyConfig()
        self._connector = destination_connector or self._connect_destination
        self._resolver = resolver
        self._enable_dns_policy_rpc = enable_dns_policy_rpc
        self._trace_http_events = trace_http_events
        self._active = 0

    def _trace_http(
        self,
        event: str,
        *,
        byte_count: int = 0,
        error_type: str | None = None,
    ) -> None:
        if not self._trace_http_events:
            return
        if error_type is None:
            logger.info("model proxy event=%s bytes=%d", event, byte_count)
        else:
            logger.warning(
                "model proxy event=%s bytes=%d error_type=%s",
                event,
                byte_count,
                error_type,
            )

    async def _connect_destination(self, authority: str) -> BoundConnection:
        return await resolve_and_connect(authority, timeout=self.config.connect_timeout)

    async def handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        active = False
        try:
            async with asyncio.timeout(self.config.max_connection_seconds):
                if self._active >= self.config.max_connections:
                    await _send_error(writer, 503)
                    # Drain bounded already-sent bytes so closing the rejected
                    # TCP stream does not reset the response.
                    try:
                        await asyncio.wait_for(
                            reader.read(self.config.max_header_bytes), timeout=0.1
                        )
                    except (TimeoutError, ConnectionError, OSError):
                        pass
                else:
                    self._active += 1
                    active = True
                    await self._serve_client(reader, writer)
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            # Hard lifetime covers parser, DNS, drain, relay, and teardown.
            pass
        finally:
            if active:
                self._active -= 1
            await self._close_writer(writer)

    async def _serve_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            raw = await _read_header(reader, self.config)
            start, headers = _parse_header_block(raw, self.config)
            parts = start.split(b" ")
            if len(parts) != 3 or parts[2] not in (b"HTTP/1.0", b"HTTP/1.1"):
                raise ProxyProtocolError()
            method, target, version = parts
            if method == b"POST" and target == _DNS_POLICY_PATH:
                if not self._enable_dns_policy_rpc:
                    raise ProxyProtocolError()
                await self._handle_dns_policy(reader, writer, version, headers)
            elif method == b"CONNECT":
                await self._handle_connect(reader, writer, target, headers)
            elif method in (
                b"GET",
                b"HEAD",
                b"POST",
                b"PUT",
                b"PATCH",
                b"DELETE",
                b"OPTIONS",
            ):
                await self._handle_http(
                    reader, writer, method, target, version, headers
                )
            else:
                raise ProxyProtocolError()
        except asyncio.CancelledError:
            raise
        except ProxyProtocolError as exc:
            await _send_error(writer, exc.status)
        except DestinationDeniedError:
            await _send_error(writer, 403)
        except DestinationResolutionError:
            await _send_error(writer, 502)
        except DestinationTimeoutError:
            await _send_error(writer, 504)
        except (DestinationConnectionError, DestinationError, OSError, ConnectionError):
            await _send_error(writer, 502)
        except TransferLimitError:
            await _send_error(writer, 413)
        except TimeoutError:
            await _send_error(writer, 408)

    async def _handle_dns_policy(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        version: bytes,
        headers: list[tuple[bytes, bytes]],
    ) -> None:
        """Answer a hostname policy preflight without connecting to its origin."""
        if version != b"HTTP/1.1":
            raise ProxyProtocolError()
        names = [name for name, _ in headers]
        required_headers = {
            b"host",
            b"content-type",
            b"connection",
            b"content-length",
        }
        allowed_headers = required_headers | {
            b"accept",
            b"accept-encoding",
            b"user-agent",
        }
        if (
            len(names) != len(set(names))
            or not required_headers.issubset(names)
            or not set(names).issubset(allowed_headers)
        ):
            raise ProxyProtocolError()
        if _one_header(headers, b"host", required=True) != _DNS_POLICY_HOST:
            raise ProxyProtocolError()
        if _one_header(headers, b"content-type", required=True) != b"application/json":
            raise ProxyProtocolError()
        if _one_header(headers, b"connection", required=True) != b"close":
            raise ProxyProtocolError()
        if _one_header(headers, b"transfer-encoding") is not None:
            raise ProxyProtocolError()
        if _one_header(headers, b"expect") is not None:
            raise ProxyProtocolError()
        raw_length = _one_header(headers, b"content-length", required=True)
        if raw_length is None or not raw_length.isdigit() or len(raw_length) > 3:
            raise ProxyProtocolError()
        body_length = int(raw_length)
        if not 1 <= body_length <= _DNS_POLICY_BODY_LIMIT:
            raise ProxyProtocolError(413)
        try:
            body = await asyncio.wait_for(reader.readexactly(body_length), timeout=1.0)
        except (asyncio.IncompleteReadError, TimeoutError) as exc:
            raise ProxyProtocolError() from exc
        try:
            payload = json.loads(body, object_pairs_hook=_unique_json_object)
            if not isinstance(payload, dict) or set(payload) != {"hostname"}:
                raise ValueError
            hostname = payload["hostname"]
            if not isinstance(hostname, str) or not hostname or len(hostname) > 253:
                raise ValueError
            hostname_bytes = hostname.encode("ascii", "strict")
            if hostname_bytes.decode("ascii") != hostname:
                raise ValueError
            parsed = parse_authority(f"{hostname}:443")
            if parsed.literal is not None or parsed.host != hostname.lower():
                raise ValueError
        except (ValueError, TypeError, UnicodeError, DestinationError) as exc:
            raise ProxyProtocolError() from exc

        try:
            resolver_kwargs = {"resolver": self._resolver} if self._resolver else {}
            await resolve_and_validate(
                f"{parsed.host}:443",
                timeout=self.config.connect_timeout,
                **resolver_kwargs,
            )
        except DestinationDeniedError:
            await _send_policy_result(writer, "denied")
        except (DestinationError, TimeoutError, OSError):
            await _send_policy_result(writer, "unavailable")
        else:
            await _send_policy_result(writer, "public")

    async def _handle_connect(
        self,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
        target: bytes,
        headers: list[tuple[bytes, bytes]],
    ) -> None:
        try:
            authority_text = target.decode("ascii")
            authority = parse_authority(authority_text)
        except (UnicodeError, DestinationError) as exc:
            raise ProxyProtocolError() from exc
        self._check_port(authority)
        _validated_host_header(headers, authority)
        self._validate_empty_body(headers)
        bound = await self._connector(_format_authority(authority.host, authority.port))
        await self._tunnel(client_reader, client_writer, bound)

    async def _handle_http(
        self,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
        method: bytes,
        target: bytes,
        version: bytes,
        headers: list[tuple[bytes, bytes]],
    ) -> None:
        authority, origin_target = _absolute_http_target(target)
        self._check_port(authority)
        _validated_host_header(headers, authority)
        content_length = self._content_length(headers)
        if content_length > self.config.max_body_bytes:
            raise TransferLimitError()

        connection_tokens: set[bytes] = set()
        for name, value in headers:
            if name == b"connection":
                for token in value.split(b","):
                    token = token.strip().lower()
                    if not _TOKEN.fullmatch(token):
                        raise ProxyProtocolError()
                    connection_tokens.add(token)
        if b"proxy-authorization" in {name for name, _ in headers}:
            raise ProxyProtocolError()
        if b"expect" in {name for name, _ in headers}:
            raise ProxyProtocolError()
        if connection_tokens & {b"host", b"content-length", b"transfer-encoding"}:
            raise ProxyProtocolError()

        safe_headers = [
            (name, value)
            for name, value in headers
            if name not in _HOP_HEADERS
            and name not in connection_tokens
            and name != b"host"
        ]
        host_header = _format_authority(authority.host, authority.port)
        safe_headers.append((b"host", host_header.encode("ascii")))
        safe_headers.append((b"connection", b"close"))
        phase = "origin_connect"
        response_bytes = 0
        request_bytes = 0
        try:
            bound = await self._connector(
                _format_authority(authority.host, authority.port)
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._trace_http(phase, error_type=type(exc).__name__)
            raise
        self._trace_http("origin_connected")
        upstream_reader: asyncio.StreamReader | None = None
        upstream_writer: asyncio.StreamWriter | None = None
        budget = _ByteBudget(self.config.max_transfer_bytes)
        try:
            phase = "origin_socket"
            upstream_reader, upstream_writer = await asyncio.open_connection(
                sock=bound.socket
            )
            request_head = method + b" " + origin_target + b" " + version + b"\r\n"
            request_head += b"".join(
                name + b": " + value + b"\r\n" for name, value in safe_headers
            )
            request_head += b"\r\n"
            budget.charge(len(request_head))
            upstream_writer.write(request_head)
            phase = "request_drain"
            await upstream_writer.drain()
            try:
                async with asyncio.timeout(self.config.max_connection_seconds):
                    phase = "request_body"

                    def record_request_chunk(amount: int) -> None:
                        nonlocal request_bytes
                        request_bytes += amount

                    await self._copy_exact(
                        client_reader,
                        upstream_writer,
                        content_length,
                        budget,
                        on_chunk=record_request_chunk,
                    )
                    self._trace_http("request_sent", byte_count=request_bytes)
                    phase = "response_relay"

                    def record_response_chunk(amount: int) -> None:
                        nonlocal response_bytes
                        response_bytes += amount

                    await self._copy_until_eof(
                        upstream_reader,
                        client_writer,
                        budget,
                        allow_eof=True,
                        on_chunk=record_response_chunk,
                    )
                    self._trace_http("response_eof", byte_count=response_bytes)
            except (
                TransferLimitError,
                TimeoutError,
                ProxyProtocolError,
                OSError,
                ConnectionError,
            ) as exc:
                # Once origin bytes have been forwarded, adding a proxy error
                # response could corrupt the origin response stream.
                self._trace_http(
                    phase,
                    byte_count=(
                        request_bytes if phase == "request_body" else response_bytes
                    ),
                    error_type=type(exc).__name__,
                )
                return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._trace_http(
                phase,
                byte_count=(
                    request_bytes if phase == "request_body" else response_bytes
                ),
                error_type=type(exc).__name__,
            )
            raise
        finally:
            if upstream_writer is not None:
                await self._close_writer(upstream_writer)
            else:
                bound.close()

    async def _tunnel(
        self,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
        bound: BoundConnection,
    ) -> None:
        server_reader: asyncio.StreamReader | None = None
        server_writer: asyncio.StreamWriter | None = None
        budget = _ByteBudget(self.config.max_transfer_bytes)
        try:
            server_reader, server_writer = await asyncio.open_connection(
                sock=bound.socket
            )
            client_writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await client_writer.drain()
            tasks = [
                asyncio.create_task(
                    self._relay_half_close(client_reader, server_writer, budget)
                ),
                asyncio.create_task(
                    self._relay_half_close(server_reader, client_writer, budget)
                ),
            ]
            try:
                await asyncio.wait_for(
                    asyncio.gather(*tasks),
                    timeout=self.config.max_connection_seconds,
                )
            except (TransferLimitError, TimeoutError, OSError, ConnectionError):
                return
            finally:
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            if server_writer is not None:
                await self._close_writer(server_writer)
            else:
                bound.close()

    async def _copy_exact(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        remaining: int,
        budget: _ByteBudget,
        *,
        on_chunk: Callable[[int], None] | None = None,
    ) -> None:
        while remaining:
            chunk = await asyncio.wait_for(
                reader.read(min(self.config.relay_chunk_bytes, remaining)),
                timeout=self.config.relay_idle_timeout,
            )
            if not chunk:
                raise ProxyProtocolError()
            budget.charge(len(chunk))
            writer.write(chunk)
            await writer.drain()
            if on_chunk is not None:
                on_chunk(len(chunk))
            remaining -= len(chunk)

    async def _copy_until_eof(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        budget: _ByteBudget,
        *,
        allow_eof: bool,
        on_chunk: Callable[[int], None] | None = None,
    ) -> None:
        while True:
            chunk = await asyncio.wait_for(
                reader.read(self.config.relay_chunk_bytes),
                timeout=self.config.relay_idle_timeout,
            )
            if not chunk:
                if allow_eof:
                    return
                raise ProxyProtocolError()
            budget.charge(len(chunk))
            writer.write(chunk)
            await writer.drain()
            if on_chunk is not None:
                on_chunk(len(chunk))

    async def _relay_half_close(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        budget: _ByteBudget,
    ) -> None:
        try:
            await self._copy_until_eof(reader, writer, budget, allow_eof=True)
        finally:
            if writer.can_write_eof():
                writer.write_eof()
                try:
                    await asyncio.wait_for(writer.drain(), timeout=0.25)
                except (TimeoutError, ConnectionError, OSError):
                    pass

    def _content_length(self, headers: list[tuple[bytes, bytes]]) -> int:
        if any(name == b"transfer-encoding" for name, _ in headers):
            raise ProxyProtocolError()
        value = _one_header(headers, b"content-length")
        if value is None:
            return 0
        if (
            len(value) > 12
            or not value
            or any(byte < 48 or byte > 57 for byte in value)
        ):
            raise ProxyProtocolError()
        return int(value)

    def _validate_empty_body(self, headers: list[tuple[bytes, bytes]]) -> None:
        length = self._content_length(headers)
        if length != 0:
            raise ProxyProtocolError()

    def _check_port(self, authority: DestinationAuthority) -> None:
        if authority.port not in self.config.allowed_ports:
            raise DestinationDeniedError()

    @staticmethod
    async def _close_writer(writer: asyncio.StreamWriter) -> None:
        writer.close()
        try:
            await asyncio.wait_for(asyncio.shield(writer.wait_closed()), timeout=0.25)
        except asyncio.CancelledError:
            transport = getattr(writer, "transport", None)
            if transport is not None:
                transport.abort()
            raise
        except (TimeoutError, ConnectionError, OSError, RuntimeError):
            transport = getattr(writer, "transport", None)
            if transport is not None:
                transport.abort()


async def health_handler(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter
) -> None:
    """Separate local health endpoint; never accepts or echoes a target URL."""
    try:
        request = await asyncio.wait_for(reader.read(1024), timeout=1.0)
        if request.split(b" ", 2)[:2] == [b"GET", b"/healthz"]:
            body = b'{"status":"ok"}\n'
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                b"Connection: close\r\nContent-Length: "
                + str(len(body)).encode("ascii")
                + b"\r\n\r\n"
                + body
            )
        else:
            writer.write(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n")
        await writer.drain()
    except (TimeoutError, ConnectionError, OSError):
        pass
    finally:
        await CaptureEgressProxy._close_writer(writer)


async def serve() -> None:
    proxy = CaptureEgressProxy(enable_dns_policy_rpc=True)
    bind_host = _proxy_bind_host()
    server = await asyncio.start_server(
        proxy.handle_client,
        host=bind_host,
        port=8080,
        limit=proxy.config.max_header_bytes,
    )
    health = await asyncio.start_server(health_handler, host="127.0.0.1", port=8081)
    async with server, health:
        await asyncio.gather(server.serve_forever(), health.serve_forever())


def _proxy_bind_host(value: str | None = None) -> str:
    """Use an explicit numeric interface address; retain the default behavior."""
    host = (
        value
        if value is not None
        else os.environ.get("CAPTURE_PROXY_BIND_HOST", "0.0.0.0")
    )
    try:
        ipaddress.ip_address(host)
    except ValueError as exc:
        raise ValueError(
            "capture proxy bind host must be a numeric IP address"
        ) from exc
    return host
