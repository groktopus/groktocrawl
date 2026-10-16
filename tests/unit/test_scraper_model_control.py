"""Model calls are fixed-target UDS capabilities, not scraper-selected URLs."""

from __future__ import annotations

import asyncio
import gzip
import ipaddress
import json
import os
import socket
import stat
import sys
import zlib
from pathlib import Path
from types import SimpleNamespace

import httpx
from scraper import llm_control
from scraper.source_http import trusted_llm_upstream_client

ROOT = Path(__file__).resolve().parents[2]
CAPTURE_EGRESS = ROOT / "capture-egress-svc"
if str(CAPTURE_EGRESS) not in sys.path:
    sys.path.insert(0, str(CAPTURE_EGRESS))
LLM_FIXTURE = ROOT / "llm-svc"
if str(LLM_FIXTURE) not in sys.path:
    sys.path.insert(0, str(LLM_FIXTURE))

import uvicorn
from capture_egress_svc.model_proxy import make_model_proxy

from common.capture_destination import BoundConnection, parse_authority


def test_model_broker_injects_secret_and_uses_only_configured_endpoint(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "https://model.example/v1")
    monkeypatch.setenv("LLM_API_KEY", "broker-only-test-key")
    observed = []

    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'{"choices":[]}'

        async def aclose(self):
            return None

    async def upstream(request: httpx.Request) -> httpx.Response:
        observed.append(request)
        return httpx.Response(
            200, headers={"content-type": "application/json"}, stream=Stream()
        )

    async def run():
        llm_control.app.state.clients = {
            "recovery": httpx.AsyncClient(transport=httpx.MockTransport(upstream)),
            "captcha": httpx.AsyncClient(transport=httpx.MockTransport(upstream)),
        }
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=llm_control.app),
            base_url="http://control.sock",
        ) as client:
            response = await client.post(
                "/recovery/chat/completions",
                json={"model": "operator-alias", "messages": [{"role": "user", "content": "page"}]},
            )
            denied = await client.post("/recovery/v1/chat/completions", json={})
        for client in llm_control.app.state.clients.values():
            await client.aclose()
        return response, denied

    response, denied = asyncio.run(run())
    assert response.status_code == 200
    assert denied.status_code == 404
    assert len(observed) == 1
    request = observed[0]
    assert request.url == "https://model.example/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer broker-only-test-key"
    assert b"operator-alias" in request.content


def test_upstream_client_routes_all_targets_through_model_gateway(monkeypatch):
    monkeypatch.setenv("MODEL_EGRESS_PROXY_URL", "http://gateway.internal:8080")
    calls = []

    def fake_client(**kwargs):
        calls.append(kwargs)
        return object()

    monkeypatch.setattr(httpx, "AsyncClient", fake_client)
    trusted_llm_upstream_client(
        "http://llm-svc:4001/v1/chat/completions"
    )
    trusted_llm_upstream_client(
        "https://model.example/v1/chat/completions"
    )

    expected = {
        "follow_redirects": False,
        "proxy": "http://gateway.internal:8080",
        "trust_env": False,
    }
    assert calls == [expected, expected]


def test_broker_forwards_through_model_gateway_to_local_http_fixture(monkeypatch):
    async def run():
        observed: list[tuple[str, bytes]] = []

        async def fixture_handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
            request_head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 2)
            lines = request_head.split(b"\r\n")
            _method, path, _version = lines[0].split(b" ", 2)
            headers = {}
            for line in lines[1:]:
                if b":" in line:
                    name, value = line.split(b":", 1)
                    headers[name.lower()] = value.strip()
            body = await asyncio.wait_for(
                reader.readexactly(int(headers.get(b"content-length", b"0"))), 2
            )
            observed.append((path.decode("ascii"), body))
            payload = json.dumps(
                {
                    "choices": [
                        {"message": {"role": "assistant", "content": "fixture-ok"}}
                    ]
                },
                separators=(",", ":"),
            ).encode()
            compressed_payload = gzip.compress(payload)
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                b"Content-Encoding: gzip\r\n"
                + f"Content-Length: {len(compressed_payload)}\r\nConnection: close\r\n\r\n".encode()
                + compressed_payload
            )
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        fixture = await asyncio.start_server(fixture_handler, "127.0.0.1", 0)
        fixture_port = fixture.sockets[0].getsockname()[1]
        authority = f"model-fixture.test:{fixture_port}"

        async def connector(raw_authority: str, *, private_host_grants):
            assert raw_authority == authority
            assert private_host_grants == frozenset()
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setblocking(False)
            await asyncio.get_running_loop().sock_connect(sock, ("127.0.0.1", fixture_port))
            return BoundConnection(
                parse_authority(raw_authority),
                ipaddress.ip_address("127.0.0.1"),
                sock,
            )

        proxy = make_model_proxy(frozenset({authority}), frozenset(), connector=connector)
        gateway = await asyncio.start_server(proxy.handle_client, "127.0.0.1", 0)
        gateway_port = gateway.sockets[0].getsockname()[1]
        monkeypatch.setenv("LLM_BASE_URL", f"http://model-fixture.test:{fixture_port}/v1")
        monkeypatch.setenv("LLM_API_KEY", "local-fixture-key")
        monkeypatch.setenv("MODEL_EGRESS_PROXY_URL", f"http://127.0.0.1:{gateway_port}")
        class SocketPath:
            def lstat(self):
                return SimpleNamespace(
                    st_mode=stat.S_IFSOCK | 0o660,
                    st_uid=os.getuid(),
                    st_gid=20000,
                )

        monkeypatch.setenv("MODEL_CONTROL_SOCKET", "/synthetic/model-control.sock")
        monkeypatch.setattr(llm_control, "Path", lambda _path: SocketPath())
        try:
            async with llm_control.lifespan(llm_control.app):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=llm_control.app),
                    base_url="http://control.sock",
                ) as client:
                    response = await client.post(
                        "/recovery/chat/completions",
                        json={
                            "model": "fixture-model",
                            "messages": [{"role": "user", "content": "fixture prompt"}],
                        },
                    )
            assert response.status_code == 200, response.text
            assert response.json()["choices"][0]["message"]["content"] == "fixture-ok"
            assert len(observed) == 1
            assert observed[0][0] == "/v1/chat/completions"
            assert json.loads(observed[0][1])["model"] == "fixture-model"
        finally:
            gateway.close()
            await gateway.wait_closed()
            fixture.close()
            await fixture.wait_closed()

    asyncio.run(run())


def test_broker_forwards_real_uvicorn_fixture_response_through_gateway(
    monkeypatch, caplog
):
    async def run():
        from llm_svc.app import create_app

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reservation:
            reservation.bind(("127.0.0.1", 0))
            fixture_port = reservation.getsockname()[1]
        fixture = uvicorn.Server(
            uvicorn.Config(
                create_app(),
                host="127.0.0.1",
                port=fixture_port,
                log_level="error",
            )
        )
        fixture_task = asyncio.create_task(fixture.serve())
        fixture_base = f"http://127.0.0.1:{fixture_port}"
        async with httpx.AsyncClient(timeout=1) as readiness:
            for _ in range(80):
                try:
                    if (await readiness.get(f"{fixture_base}/health")).status_code == 200:
                        break
                except httpx.HTTPError:
                    await asyncio.sleep(0.025)
            else:
                fixture.should_exit = True
                await fixture_task
                raise AssertionError("local Uvicorn model fixture did not start")

        authority = f"candidate-llm-fixture.test:{fixture_port}"

        async def connector(raw_authority: str, *, private_host_grants):
            assert raw_authority == authority
            assert private_host_grants == frozenset()
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setblocking(False)
            await asyncio.get_running_loop().sock_connect(
                sock, ("127.0.0.1", fixture_port)
            )
            return BoundConnection(
                parse_authority(raw_authority),
                ipaddress.ip_address("127.0.0.1"),
                sock,
            )

        proxy = make_model_proxy(frozenset({authority}), frozenset(), connector=connector)
        gateway = await asyncio.start_server(proxy.handle_client, "127.0.0.1", 0)
        gateway_port = gateway.sockets[0].getsockname()[1]
        monkeypatch.setenv("LLM_BASE_URL", f"http://candidate-llm-fixture.test:{fixture_port}/v1")
        monkeypatch.setenv("LLM_API_KEY", "local-fixture-key")
        monkeypatch.setenv("MODEL_EGRESS_PROXY_URL", f"http://127.0.0.1:{gateway_port}")

        class SocketPath:
            def lstat(self):
                return SimpleNamespace(
                    st_mode=stat.S_IFSOCK | 0o660,
                    st_uid=os.getuid(),
                    st_gid=20000,
                )

        monkeypatch.setenv("MODEL_CONTROL_SOCKET", "/synthetic/model-control.sock")
        monkeypatch.setattr(llm_control, "Path", lambda _path: SocketPath())
        try:
            async with llm_control.lifespan(llm_control.app):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=llm_control.app),
                    base_url="http://control.sock",
                ) as client:
                    response = await client.post(
                        "/recovery/chat/completions",
                        json={
                            "model": "fixture-model",
                            "messages": [
                                {"role": "user", "content": "fixture prompt"}
                            ],
                        },
                    )
            assert response.status_code == 200, response.text
            assert response.json()["choices"][0]["message"]["content"]
            async with httpx.AsyncClient(timeout=2) as fixture_client:
                diagnostics = (
                    await fixture_client.get(f"{fixture_base}/diagnostics")
                ).json()
            assert len(diagnostics["entries"]) == 1
            assert diagnostics["entries"][0]["model"] == "fixture-model"
            assert diagnostics["entries"][0]["status"] == 200
            assert "model proxy event=origin_connected" in caplog.text
            assert "model proxy event=request_sent" in caplog.text
            assert "model proxy event=response_eof" in caplog.text
            assert "candidate-llm-fixture.test" not in caplog.text
            assert "local-fixture-key" not in caplog.text
            assert "fixture prompt" not in caplog.text
        finally:
            gateway.close()
            await gateway.wait_closed()
            fixture.should_exit = True
            await fixture_task


def test_broker_response_decoder_enforces_raw_and_decoded_caps(monkeypatch):
    async def run():
        monkeypatch.setattr(llm_control, "MAX_RESPONSE_BYTES", 128)

        class Stream(httpx.AsyncByteStream):
            def __init__(self, payload: bytes):
                self.payload = payload

            async def __aiter__(self):
                yield self.payload

            async def aclose(self):
                return None

        def response(content: bytes, headers: dict[str, str] | None = None):
            return httpx.Response(
                200, headers=headers, stream=Stream(content)
            )

        raw_response = response(b"x" * 129)
        try:
            try:
                await llm_control._read_bounded_response(raw_response)
            except ValueError as exc:
                assert "byte limit" in str(exc)
            else:
                raise AssertionError("oversized raw model response was accepted")
        finally:
            await raw_response.aclose()

        compressed = gzip.compress(b"x" * 129)
        decoded_response = response(compressed, {"content-encoding": "gzip"})
        try:
            try:
                await llm_control._read_bounded_response(decoded_response)
            except ValueError as exc:
                assert "byte limit" in str(exc)
            else:
                raise AssertionError("oversized decoded model response was accepted")
        finally:
            await decoded_response.aclose()

        malformed_response = response(
            b"not-gzip", {"content-encoding": "gzip"}
        )
        try:
            try:
                await llm_control._read_bounded_response(malformed_response)
            except (ValueError, zlib.error):
                pass
            else:
                raise AssertionError("malformed compressed response was accepted")
        finally:
            await malformed_response.aclose()

    asyncio.run(run())
