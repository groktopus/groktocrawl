"""Probe the real gateway image on its own loopback, without origin traffic."""

import socket
import time
import urllib.request


def main() -> None:
    deadline = time.monotonic() + 10
    while True:
        try:
            with urllib.request.urlopen(
                "http://127.0.0.1:8081/healthz", timeout=1
            ) as response:
                assert response.status == 200
            break
        except (OSError, TimeoutError):
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.1)

    # --network none permits loopback only. A private origin must be refused
    # by policy before a connection, even inside that isolated namespace.
    for authority in ("127.0.0.1:80", "[::1]:443"):
        with socket.create_connection(("127.0.0.1", 8080), timeout=2) as client:
            client.sendall(
                f"CONNECT {authority} HTTP/1.1\r\nHost: {authority}\r\n\r\n".encode()
            )
            response = b""
            while b"\r\n" not in response and len(response) < 1024:
                chunk = client.recv(1024 - len(response))
                if not chunk:
                    break
                response += chunk
            assert response.startswith(b"HTTP/1.1 403 "), response
    print("gateway image startup, liveness and private-origin denial passed")


if __name__ == "__main__":
    main()
