import asyncio
import ipaddress
import json
import socket
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import NoReturn

import httpx
import pytest

_service_root = Path(__file__).resolve().parents[2] / "capture-egress-svc"
sys.path.insert(0, str(_service_root))
_scraper_root = Path(__file__).resolve().parents[2] / "scraper-svc"
sys.path.insert(0, str(_scraper_root))

from capture_egress_svc.proxy import (
    CaptureEgressProxy,
    ProxyConfig,
    _proxy_bind_host,
    health_handler,
)
from scraper.fetch_tiers import _is_private_url, fetch_via_content_negotiation
from scraper.source_http import (
    CAPTURE_EGRESS_PROXY_ENV,
    capture_gateway_hostname_is_public,
    source_httpx_client,
)

from common import capture_destination
from common.capture_destination import (
    BoundConnection,
    DestinationResolutionError,
    parse_authority,
)


def test_proxy_bind_host_keeps_default_compatible_and_accepts_numeric_interface(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.delenv("CAPTURE_PROXY_BIND_HOST", raising=False)
    assert _proxy_bind_host() == "0.0.0.0"
    assert _proxy_bind_host("172.31.254.2") == "172.31.254.2"


@pytest.mark.parametrize("value", ["gateway", "", " 172.31.254.2"])
def test_proxy_bind_host_rejects_non_numeric_configuration(value: str):
    with pytest.raises(ValueError, match="numeric IP address"):
        _proxy_bind_host(value)


async def start_proxy(
    proxy: CaptureEgressProxy,
) -> tuple[asyncio.AbstractServer, str, int]:
    server = await asyncio.start_server(
        proxy.handle_client,
        host="127.0.0.1",
        port=0,
        limit=proxy.config.max_header_bytes,
    )
    address = server.sockets[0].getsockname()
    return server, address[0], address[1]


async def connect_local(address: tuple[str, int]) -> socket.socket:
    loop = asyncio.get_running_loop()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setblocking(False)
    await loop.sock_connect(sock, address)
    return sock


def local_connector(
    address: tuple[str, int], seen: list[str] | None = None
) -> Callable[[str], Awaitable[BoundConnection]]:
    async def connector(authority: str) -> BoundConnection:
        if seen is not None:
            seen.append(authority)
        sock = await connect_local(address)
        return BoundConnection(
            parse_authority(authority), ipaddress.ip_address("93.184.216.34"), sock
        )

    return connector


async def stop_server(server: asyncio.AbstractServer) -> None:
    server.close()
    await server.wait_closed()


async def read_to_eof(reader: asyncio.StreamReader) -> bytes:
    chunks: list[bytes] = []
    while chunk := await reader.read(4096):
        chunks.append(chunk)
    return b"".join(chunks)


async def send_dns_policy_request(
    address: tuple[str, int], *, hostname: str = "example.com", headers: bytes = b""
) -> bytes:
    reader, writer = await asyncio.open_connection(*address)
    body = json.dumps({"hostname": hostname}, separators=(",", ":")).encode()
    request_headers = headers or (
        b"Host: capture-egress.internal:8080\r\n"
        b"Content-Type: application/json\r\n"
        b"Connection: close\r\n"
        + b"Content-Length: "
        + str(len(body)).encode()
        + b"\r\n"
    )
    writer.write(
        b"POST /_control/public-dns-check HTTP/1.1\r\n"
        + request_headers
        + b"\r\n"
        + body
    )
    await writer.drain()
    response = await read_to_eof(reader)
    writer.close()
    await writer.wait_closed()
    return response


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("answers", "expected_status", "expected_body"),
    [
        (["93.184.216.34", "2606:4700:4700::1111"], b"200 OK", b'{"status":"public"}'),
        (["93.184.216.34", "10.0.0.1"], b"403 Forbidden", b'{"status":"denied"}'),
        (["10.0.0.1"], b"403 Forbidden", b'{"status":"denied"}'),
    ],
)
async def test_dns_policy_rpc_uses_shared_address_policy_without_connecting(
    answers: list[str], expected_status: bytes, expected_body: bytes
) -> None:
    async def resolver(_host: str, _port: int) -> list[str]:
        return answers

    async def unexpected_connector(_authority: str) -> BoundConnection:
        raise AssertionError("DNS policy oracle must never connect to an origin")

    proxy = CaptureEgressProxy(
        resolver=resolver,
        destination_connector=unexpected_connector,
        enable_dns_policy_rpc=True,
    )
    server, host, port = await start_proxy(proxy)
    try:
        response = await send_dns_policy_request((host, port))
        head, body = response.split(b"\r\n\r\n", 1)
        assert expected_status in head.split(b"\r\n", 1)[0]
        assert body == expected_body
        assert b"93.184.216.34" not in response
        assert b"10.0.0.1" not in response
    finally:
        await stop_server(server)


@pytest.mark.asyncio
async def test_scraper_async_oracle_client_round_trips_through_policy_rpc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def resolver(host: str, port: int) -> list[str]:
        assert host == "example.com"
        assert port in {80, 443}
        return ["93.184.216.34"]

    async def unexpected_connector(_authority: str) -> BoundConnection:
        raise AssertionError("preflight must not connect to the origin")

    proxy = CaptureEgressProxy(
        resolver=resolver,
        destination_connector=unexpected_connector,
        enable_dns_policy_rpc=True,
    )
    server, host, port = await start_proxy(proxy)
    monkeypatch.setenv(CAPTURE_EGRESS_PROXY_ENV, f"http://{host}:{port}")
    monkeypatch.setenv("SCRAPER_CAPTURE_GATEWAY_IP", host)
    monkeypatch.setenv("SCRAPER_CAPTURE_GATEWAY_PORT", str(port))
    try:
        assert await capture_gateway_hostname_is_public("example.com") is True
    finally:
        await stop_server(server)


@pytest.mark.asyncio
async def test_protected_scrape_uses_gateway_dns_when_local_dns_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    origin_requests: list[bytes] = []

    async def origin_handler(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        request = await reader.readuntil(b"\r\n\r\n")
        origin_requests.append(request)
        body = (
            b"# Fixture page\n\n## Protected route\n\n"
            b"- Public content through the gateway.\n"
        )
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Type: text/markdown\r\n"
            + f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
            + body
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    origin = await asyncio.start_server(origin_handler, "127.0.0.1", 0)
    origin_address = origin.sockets[0].getsockname()

    async def resolver(host: str, port: int) -> list[str]:
        assert host == "example.com"
        assert port in {80, 443}
        return ["93.184.216.34"]

    proxy = CaptureEgressProxy(
        resolver=resolver,
        destination_connector=local_connector(origin_address),
        enable_dns_policy_rpc=True,
    )
    gateway, host, port = await start_proxy(proxy)
    monkeypatch.setenv(CAPTURE_EGRESS_PROXY_ENV, f"http://{host}:{port}")
    monkeypatch.setenv("SCRAPER_CAPTURE_GATEWAY_IP", host)
    monkeypatch.setenv("SCRAPER_CAPTURE_GATEWAY_PORT", str(port))
    monkeypatch.setattr(
        "scraper.fetch_tiers.is_private_host",
        lambda _url: (_ for _ in ()).throw(
            socket.gaierror(-3, "temporary failure in name resolution")
        ),
    )
    try:
        # Protected mode must ask the gateway, not the isolated scraper's
        # failing local resolver, before it starts the source request.
        assert await _is_private_url("http://example.com/article") == (False, "")
        async with source_httpx_client(timeout=2) as http_client:

            class CurlCompatibleClient:
                async def get(self, url, **kwargs):
                    allow_redirects = kwargs.pop("allow_redirects", False)
                    return await http_client.get(
                        url, follow_redirects=allow_redirects, **kwargs
                    )

            result = await fetch_via_content_negotiation(
                "http://example.com/article", CurlCompatibleClient()
            )
        assert result is not None
        assert result["markdown"].startswith("# Fixture page")
        assert len(origin_requests) == 1
        assert origin_requests[0].startswith(b"GET /article HTTP/1.1\r\n")
    finally:
        await stop_server(gateway)
        await stop_server(origin)


@pytest.mark.asyncio
async def test_dns_policy_rpc_reports_resolver_outage_as_unavailable() -> None:
    async def resolver(_host: str, _port: int) -> list[str]:
        raise DestinationResolutionError()

    proxy = CaptureEgressProxy(resolver=resolver, enable_dns_policy_rpc=True)
    server, host, port = await start_proxy(proxy)
    try:
        response = await send_dns_policy_request((host, port))
        assert response.startswith(b"HTTP/1.1 503 Service Unavailable\r\n")
        assert response.endswith(b'{"status":"unavailable"}')
    finally:
        await stop_server(server)


@pytest.mark.asyncio
async def test_dns_policy_rpc_requires_fixed_origin_form_and_exact_host() -> None:
    async def resolver(_host: str, _port: int) -> list[str]:
        raise AssertionError("malformed policy request must not query DNS")

    proxy = CaptureEgressProxy(resolver=resolver, enable_dns_policy_rpc=True)
    server, host, port = await start_proxy(proxy)
    try:
        response = await send_dns_policy_request(
            (host, port),
            headers=(
                b"Host: candidate-model-upstream:8080\r\n"
                b"Content-Type: application/json\r\n"
                b"Connection: close\r\n"
                b"Content-Length: 25\r\n"
            ),
        )
        assert response.startswith(b"HTTP/1.1 400 Bad Request\r\n")
    finally:
        await stop_server(server)


@pytest.mark.asyncio
async def test_model_proxy_shape_does_not_enable_dns_policy_rpc() -> None:
    from capture_egress_svc.model_proxy import make_model_proxy

    proxy = make_model_proxy(frozenset({"model.example:443"}), frozenset())
    server, host, port = await start_proxy(proxy)
    try:
        response = await send_dns_policy_request((host, port))
        assert response.startswith(b"HTTP/1.1 400 Bad Request\r\n")
    finally:
        await stop_server(server)


class FakeTransport:
    def __init__(self) -> None:
        self.aborted = False

    def abort(self) -> None:
        self.aborted = True


class FakeWriter:
    def __init__(self, *, block_drain: bool = False, block_close: bool = False) -> None:
        self.transport = FakeTransport()
        self.block_drain = block_drain
        self.block_close = block_close
        self.closed = False
        self.drain_started = asyncio.Event()
        self.unblock = asyncio.Event()
        self.output = bytearray()

    def write(self, data: bytes) -> None:
        self.output.extend(data)

    async def drain(self) -> None:
        if self.block_drain:
            self.drain_started.set()
            await self.unblock.wait()

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        if self.block_close:
            await self.unblock.wait()


def malformed_reader() -> asyncio.StreamReader:
    reader = asyncio.StreamReader()
    reader.feed_data(b"malformed\r\n\r\n")
    return reader


@pytest.mark.asyncio
async def test_connect_tunnel_is_opaque_and_forwards_bytes() -> None:
    async def echo(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        data = await reader.read(4096)
        writer.write(data)
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    origin = await asyncio.start_server(echo, "127.0.0.1", 0)
    origin_address = origin.sockets[0].getsockname()
    calls: list[str] = []
    proxy = CaptureEgressProxy(
        destination_connector=local_connector(origin_address, calls)
    )
    gateway, host, port = await start_proxy(proxy)
    try:
        reader, writer = await asyncio.open_connection(host, port)
        writer.write(
            b"CONNECT origin.example:443 HTTP/1.1\r\nHost: origin.example:443\r\n\r\n"
        )
        await writer.drain()
        assert await reader.readuntil(b"\r\n\r\n") == (
            b"HTTP/1.1 200 Connection Established\r\n\r\n"
        )
        writer.write(b"opaque TLS-like payload")
        await writer.drain()
        writer.write_eof()
        assert await reader.read() == b"opaque TLS-like payload"
        assert calls == ["origin.example:443"]
        writer.close()
        await writer.wait_closed()
    finally:
        await stop_server(gateway)
        await stop_server(origin)


@pytest.mark.asyncio
async def test_absolute_form_http_post_streams_body_and_strips_hop_headers() -> None:
    observed: list[bytes] = []

    async def origin_handler(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        header = await reader.readuntil(b"\r\n\r\n")
        observed.append(header)
        body = await reader.readexactly(4)
        observed.append(body)
        response = (
            b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok"
        )
        writer.write(response)
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    origin = await asyncio.start_server(origin_handler, "127.0.0.1", 0)
    origin_address = origin.sockets[0].getsockname()
    proxy = CaptureEgressProxy(destination_connector=local_connector(origin_address))
    gateway, host, port = await start_proxy(proxy)
    try:
        reader, writer = await asyncio.open_connection(host, port)
        writer.write(
            b"POST http://origin.example/upload?q=1 HTTP/1.1\r\n"
            b"Host: origin.example\r\n"
            b"Content-Length: 4\r\n"
            b"Connection: keep-alive, x-remove\r\n"
            b"X-Remove: secret-hop-value\r\n"
            b"Proxy-Connection: keep-alive\r\n\r\n"
            b"DATA"
        )
        await writer.drain()
        response = await read_to_eof(reader)
        assert response.startswith(b"HTTP/1.1 200 OK")
        assert observed[0].startswith(b"POST /upload?q=1 HTTP/1.1\r\n")
        assert b"host: origin.example:80\r\n" in observed[0]
        assert b"connection: close\r\n" in observed[0]
        assert b"x-remove" not in observed[0]
        assert b"proxy-connection" not in observed[0]
        assert observed[1] == b"DATA"
        writer.close()
        await writer.wait_closed()
    finally:
        await stop_server(gateway)
        await stop_server(origin)


@pytest.mark.asyncio
async def test_absolute_http_preserves_origin_response_after_complete_length_body() -> (
    None
):
    request_body_seen = asyncio.Event()

    async def origin_handler(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        header = await reader.readuntil(b"\r\n\r\n")
        content_length = next(
            int(line.split(b":", 1)[1].strip())
            for line in header.split(b"\r\n")
            if line.lower().startswith(b"content-length:")
        )
        body = await reader.readexactly(content_length)
        assert body == b"fixture-request"
        request_body_seen.set()
        # Some HTTP servers treat client FIN as a disconnected request even
        # after Content-Length has completed. The proxy should rely on that
        # framing and leave its write side open for the origin's response.
        try:
            await asyncio.wait_for(reader.read(1), timeout=0.05)
        except TimeoutError:
            pass
        else:
            writer.close()
            await writer.wait_closed()
            return
        payload = b'{"choices":[]}'
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
            + f"Content-Length: {len(payload)}\r\nConnection: close\r\n\r\n".encode()
            + payload
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    origin = await asyncio.start_server(origin_handler, "127.0.0.1", 0)
    origin_address = origin.sockets[0].getsockname()
    proxy = CaptureEgressProxy(destination_connector=local_connector(origin_address))
    gateway, host, port = await start_proxy(proxy)
    try:
        async with httpx.AsyncClient(
            proxy=f"http://{host}:{port}", trust_env=False, timeout=2
        ) as client:
            response = await client.post(
                "http://origin.example/chat/completions",
                content=b"fixture-request",
                headers={"content-type": "application/json"},
            )
        assert request_body_seen.is_set()
        assert response.status_code == 200
        assert response.json() == {"choices": []}
    finally:
        await stop_server(gateway)
        await stop_server(origin)


@pytest.mark.asyncio
async def test_mixed_dns_answers_are_denied_before_dial() -> None:
    dials: list[str] = []

    async def resolver(host: str, port: int) -> list[str]:
        return ["93.184.216.34", "10.0.0.2"]

    async def dialer(address: object, port: int) -> NoReturn:
        dials.append(str(address))
        raise AssertionError("mixed public/private DNS must deny before dial")

    async def guarded_connector(authority: str) -> BoundConnection:
        return await capture_destination.resolve_and_connect(
            authority, resolver=resolver, dialer=dialer
        )

    proxy = CaptureEgressProxy(destination_connector=guarded_connector)
    gateway, host, port = await start_proxy(proxy)
    try:
        reader, writer = await asyncio.open_connection(host, port)
        writer.write(
            b"CONNECT origin.example:443 HTTP/1.1\r\nHost: origin.example:443\r\n\r\n"
        )
        await writer.drain()
        response = await read_to_eof(reader)
        assert response.startswith(b"HTTP/1.1 403 Forbidden")
        assert b"origin.example" not in response
        assert dials == []
        writer.close()
        await writer.wait_closed()
    finally:
        await stop_server(gateway)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raw_request",
    [
        b"GET http://origin.example/ HTTP/1.1\r\nHost: origin.example\r\nHost: evil.example\r\n\r\n",
        b"GET http://origin.example/ HTTP/1.1\r\nHost: other.example\r\n\r\n",
        b"POST http://origin.example/ HTTP/1.1\r\nHost: origin.example\r\nContent-Length: 0\r\nContent-Length: 0\r\n\r\n",
        b"POST http://origin.example/ HTTP/1.1\r\nHost: origin.example\r\nContent-Length: 1\r\nTransfer-Encoding: chunked\r\n\r\n",
        b"POST http://origin.example/ HTTP/1.1\r\nHost: origin.example\r\nContent-Length: 0\r\nConnection: content-length\r\n\r\n",
        b"GET http://user@origin.example/ HTTP/1.1\r\nHost: origin.example\r\n\r\n",
        b"GET http://origin.example:0/ HTTP/1.1\r\nHost: origin.example\r\n\r\n",
        b"GET http://origin.example:/ HTTP/1.1\r\nHost: origin.example\r\n\r\n",
        b"GET http://origin.example/# HTTP/1.1\r\nHost: origin.example\r\n\r\n",
        b"CONNECT origin.example:444 HTTP/1.1\r\nHost: origin.example:444\r\n\r\n",
    ],
)
async def test_malformed_smuggling_or_disallowed_port_denied_before_connect(
    raw_request: bytes,
) -> None:
    connects: list[str] = []

    async def connector(authority: str) -> BoundConnection:
        connects.append(authority)
        raise AssertionError("invalid request must not connect")

    proxy = CaptureEgressProxy(destination_connector=connector)
    gateway, host, port = await start_proxy(proxy)
    try:
        reader, writer = await asyncio.open_connection(host, port)
        writer.write(raw_request)
        await writer.drain()
        response = await read_to_eof(reader)
        assert response.startswith(b"HTTP/1.1 400 ") or response.startswith(
            b"HTTP/1.1 403 "
        )
        assert connects == []
        writer.close()
        await writer.wait_closed()
    finally:
        await stop_server(gateway)


@pytest.mark.asyncio
async def test_http_declared_body_cap_fails_before_connect() -> None:
    connects: list[str] = []

    async def connector(authority: str) -> BoundConnection:
        connects.append(authority)
        raise AssertionError("oversize declared body must not connect")

    proxy = CaptureEgressProxy(
        ProxyConfig(max_body_bytes=4, max_transfer_bytes=16),
        destination_connector=connector,
    )
    gateway, host, port = await start_proxy(proxy)
    try:
        reader, writer = await asyncio.open_connection(host, port)
        writer.write(
            b"POST http://origin.example/ HTTP/1.1\r\n"
            b"Host: origin.example\r\nContent-Length: 5\r\n\r\n"
        )
        await writer.drain()
        response = await read_to_eof(reader)
        assert response.startswith(b"HTTP/1.1 413 Payload Too Large")
        assert connects == []
        writer.close()
        await writer.wait_closed()
    finally:
        await stop_server(gateway)


@pytest.mark.asyncio
async def test_connect_transfer_cap_closes_tunnel_without_http_error_injection() -> (
    None
):
    async def hold(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.read(4096)
        await asyncio.sleep(1)
        writer.close()
        await writer.wait_closed()

    origin = await asyncio.start_server(hold, "127.0.0.1", 0)
    origin_address = origin.sockets[0].getsockname()
    proxy = CaptureEgressProxy(
        ProxyConfig(max_transfer_bytes=3),
        destination_connector=local_connector(origin_address),
    )
    gateway, host, port = await start_proxy(proxy)
    try:
        reader, writer = await asyncio.open_connection(host, port)
        writer.write(
            b"CONNECT origin.example:443 HTTP/1.1\r\nHost: origin.example:443\r\n\r\n"
        )
        await writer.drain()
        assert await reader.readuntil(b"\r\n\r\n") == (
            b"HTTP/1.1 200 Connection Established\r\n\r\n"
        )
        writer.write(b"four")
        await writer.drain()
        assert await read_to_eof(reader) == b""
        writer.close()
        await writer.wait_closed()
    finally:
        await stop_server(gateway)
        await stop_server(origin)


@pytest.mark.asyncio
async def test_active_connection_cap_rejects_excess_client() -> None:
    async def hold(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.read()
        writer.close()
        await writer.wait_closed()

    origin = await asyncio.start_server(hold, "127.0.0.1", 0)
    origin_address = origin.sockets[0].getsockname()
    connector_started = asyncio.Event()
    release_connector = asyncio.Event()
    local = local_connector(origin_address)

    async def held_connector(authority: str) -> BoundConnection:
        connector_started.set()
        await release_connector.wait()
        return await local(authority)

    proxy = CaptureEgressProxy(
        ProxyConfig(max_connections=1), destination_connector=held_connector
    )
    gateway, host, port = await start_proxy(proxy)
    try:
        first_reader, first_writer = await asyncio.open_connection(host, port)
        first_writer.write(
            b"CONNECT origin.example:443 HTTP/1.1\r\nHost: origin.example:443\r\n\r\n"
        )
        await first_writer.drain()
        await connector_started.wait()

        second_reader, second_writer = await asyncio.open_connection(host, port)
        second_writer.write(
            b"CONNECT other.example:443 HTTP/1.1\r\nHost: other.example:443\r\n\r\n"
        )
        await second_writer.drain()
        second_response = await read_to_eof(second_reader)
        assert second_response.startswith(b"HTTP/1.1 503 Service Unavailable")

        release_connector.set()
        assert await first_reader.readuntil(b"\r\n\r\n") == (
            b"HTTP/1.1 200 Connection Established\r\n\r\n"
        )
        first_writer.close()
        second_writer.close()
        await first_writer.wait_closed()
        await second_writer.wait_closed()
    finally:
        release_connector.set()
        await stop_server(gateway)
        await stop_server(origin)


@pytest.mark.asyncio
async def test_cancelling_tunnel_closes_bound_upstream_socket() -> None:
    origin_eof = asyncio.Event()

    async def hold(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.read()
        origin_eof.set()
        writer.close()
        await writer.wait_closed()

    origin = await asyncio.start_server(hold, "127.0.0.1", 0)
    origin_address = origin.sockets[0].getsockname()
    tunnel_started = asyncio.Event()
    handler_task: asyncio.Task[None] | None = None
    proxy = CaptureEgressProxy(destination_connector=local_connector(origin_address))
    original_tunnel = proxy._tunnel

    async def tracked_tunnel(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        bound: BoundConnection,
    ) -> None:
        nonlocal handler_task
        handler_task = asyncio.current_task()
        tunnel_started.set()
        await original_tunnel(reader, writer, bound)

    proxy._tunnel = tracked_tunnel  # type: ignore[assignment]
    gateway, host, port = await start_proxy(proxy)
    try:
        reader, writer = await asyncio.open_connection(host, port)
        writer.write(
            b"CONNECT origin.example:443 HTTP/1.1\r\nHost: origin.example:443\r\n\r\n"
        )
        await writer.drain()
        assert await reader.readuntil(b"\r\n\r\n") == (
            b"HTTP/1.1 200 Connection Established\r\n\r\n"
        )
        await tunnel_started.wait()
        assert handler_task is not None
        handler_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await handler_task
        await asyncio.wait_for(origin_eof.wait(), timeout=1)
        writer.close()
        await writer.wait_closed()
    finally:
        await stop_server(gateway)
        await stop_server(origin)


@pytest.mark.asyncio
@pytest.mark.parametrize("blocked_stage", ["drain", "close"])
async def test_lifecycle_deadline_bounds_blocked_writer_and_releases_slot(
    blocked_stage: str,
) -> None:
    writer = FakeWriter(
        block_drain=blocked_stage == "drain",
        block_close=blocked_stage == "close",
    )
    proxy = CaptureEgressProxy(ProxyConfig(max_connection_seconds=0.04))
    started = asyncio.get_running_loop().time()

    await proxy.handle_client(malformed_reader(), writer)  # type: ignore[arg-type]

    elapsed = asyncio.get_running_loop().time() - started
    assert elapsed < 0.6
    assert proxy._active == 0
    assert writer.closed
    assert writer.transport.aborted is (blocked_stage == "close")


@pytest.mark.asyncio
async def test_cancellation_aborts_blocked_writer_and_releases_slot() -> None:
    writer = FakeWriter(block_drain=True, block_close=True)
    proxy = CaptureEgressProxy(ProxyConfig(max_connection_seconds=10))
    task = asyncio.create_task(
        proxy.handle_client(malformed_reader(), writer)  # type: ignore[arg-type]
    )
    await writer.drain_started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert proxy._active == 0
    assert writer.closed
    assert writer.transport.aborted


@pytest.mark.parametrize(
    "kwargs",
    [
        {"connect_timeout": 31},
        {"connect_timeout": "2"},
        {"max_connections": 1.5},
        {"max_header_bytes": True},
        {"allowed_ports": frozenset({80, True})},
    ],
)
def test_proxy_config_rejects_invalid_numeric_limits(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        ProxyConfig(**kwargs)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_health_endpoint_is_separate_and_does_not_echo_input() -> None:
    server = await asyncio.start_server(health_handler, "127.0.0.1", 0)
    address = server.sockets[0].getsockname()
    try:
        reader, writer = await asyncio.open_connection(*address[:2])
        writer.write(b"GET /healthz?source=https://secret.example HTTP/1.1\r\n\r\n")
        await writer.drain()
        response = await read_to_eof(reader)
        assert response.startswith(b"HTTP/1.1 404 Not Found")
        assert b"secret.example" not in response
        writer.close()
        await writer.wait_closed()
    finally:
        await stop_server(server)
