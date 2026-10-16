"""Protected browser controller access keeps its fixed UDS transport."""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


def _browser_helpers():
    browser_client = importlib.import_module("agent.browser_client")
    package_dir = Path(browser_client.__file__).resolve().parent
    path = package_dir / "routes" / "_helpers.py"
    spec = importlib.util.spec_from_file_location("browser_helpers_under_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeResponse:
    def __init__(self, status_code: int = 200, payload: object | None = None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {"success": True}
        self.text = "response text"

    def json(self):
        return self._payload


def _install_browser_httpx(monkeypatch, *, status_code: int = 200, error=None):
    observed: dict[str, object] = {"requests": []}

    class _Transport:
        def __init__(self, **kwargs):
            observed["transport_kwargs"] = kwargs
            observed["transport"] = self

    class _Client:
        def __init__(self, **kwargs):
            observed["client_kwargs"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, target, **kwargs):
            observed["requests"].append(("GET", target, kwargs))
            if error:
                raise error
            return _FakeResponse(status_code)

        async def post(self, target, **kwargs):
            observed["requests"].append(("POST", target, kwargs))
            if error:
                raise error
            return _FakeResponse(status_code)

        async def delete(self, target, **kwargs):
            observed["requests"].append(("DELETE", target, kwargs))
            if error:
                raise error
            return _FakeResponse(status_code)

    monkeypatch.setattr(httpx, "AsyncHTTPTransport", _Transport)
    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    return observed


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "expected"), [(200, "ok"), (503, "degraded")]
)
async def test_browser_health_uses_configured_uds_and_preserves_readiness(
    monkeypatch, status_code, expected
):
    from agent.health import check_browser

    observed = _install_browser_httpx(monkeypatch, status_code=status_code)
    result = await check_browser(
        "http://browser-svc:8012",
        socket_path="/run/browser-control/controller.sock",
    )

    assert result["status"] == expected
    assert observed["transport_kwargs"] == {
        "uds": "/run/browser-control/controller.sock",
        "retries": 0,
    }
    assert observed["client_kwargs"] == {
        "transport": observed["transport"],
        "base_url": "http://browser-control",
        "trust_env": False,
        "timeout": 10,
    }
    assert observed["requests"] == [("GET", "/health", {"timeout": 10})]


@pytest.mark.asyncio
async def test_configured_browser_uds_failure_is_down_without_tcp_fallback(monkeypatch):
    from agent.health import check_browser

    observed = _install_browser_httpx(
        monkeypatch, error=httpx.ConnectError("UDS unavailable")
    )
    result = await check_browser(
        "http://browser-svc:8012",
        socket_path="/run/browser-control/controller.sock",
    )

    assert result["status"] == "down"
    assert len(observed["requests"]) == 1
    assert observed["requests"][0][:2] == ("GET", "/health")
    assert observed["transport_kwargs"]["uds"] == (
        "/run/browser-control/controller.sock"
    )


@pytest.mark.asyncio
async def test_check_all_passes_the_configured_browser_socket():
    from agent.health import check_all

    healthy = {"status": "ok", "latency_ms": 1.0, "detail": "ok"}
    browser_check = AsyncMock(return_value=healthy)
    with (
        patch("agent.health.check_valkey", return_value=healthy),
        patch("agent.health.check_searxng", return_value=healthy),
        patch("agent.health.check_scraper", return_value=healthy),
        patch("agent.health.check_browser", browser_check),
        patch("agent.health.check_portal", return_value=healthy),
    ):
        result = await check_all(
            browser_socket_path="/run/browser-control/controller.sock"
        )

    assert result["status"] == "ok"
    browser_check.assert_awaited_once_with(
        "http://browser-svc:8012",
        socket_path="/run/browser-control/controller.sock",
    )


@pytest.mark.asyncio
async def test_browser_health_keeps_legacy_http_when_socket_is_unset(monkeypatch):
    from agent.health import check_browser

    observed = _install_browser_httpx(monkeypatch)
    result = await check_browser("http://browser-svc:8012")

    assert result["status"] == "ok"
    assert "transport_kwargs" not in observed
    assert observed["client_kwargs"] == {"timeout": 10}
    assert observed["requests"] == [
        ("GET", "http://browser-svc:8012/health", {"timeout": 10})
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/browsers", None),
        ("POST", "/browsers", {"ttl": 30}),
        ("DELETE", "/browsers/session-1", None),
    ],
)
async def test_browser_proxy_forwards_all_methods_over_fixed_uds(
    monkeypatch, method, path, body
):
    _browser_proxy = _browser_helpers()._browser_proxy

    monkeypatch.setenv(
        "BROWSER_CONTROL_SOCKET", "/run/browser-control/controller.sock"
    )
    observed = _install_browser_httpx(monkeypatch)
    result = await _browser_proxy(path, method=method, json_data=body)

    assert result == {"success": True}
    assert observed["transport_kwargs"] == {
        "uds": "/run/browser-control/controller.sock",
        "retries": 0,
    }
    assert observed["client_kwargs"]["trust_env"] is False
    assert observed["client_kwargs"]["timeout"] == 120
    assert observed["requests"] == [
        (method, path, {"json": body or {}} if method == "POST" else {})
    ]


@pytest.mark.asyncio
async def test_browser_proxy_loads_from_active_package_without_checkout_tree(
    monkeypatch, tmp_path
):
    import sys

    monkeypatch.setattr(sys.modules[__name__], "ROOT", tmp_path / "no-checkout-agent-svc")
    monkeypatch.setenv("BROWSER_CONTROL_SOCKET", "/run/browser-control/controller.sock")
    observed = _install_browser_httpx(monkeypatch)

    result = await _browser_helpers()._browser_proxy("/browsers", method="GET")

    assert result == {"success": True}
    assert not (ROOT / "agent-svc").exists()
    assert observed["transport_kwargs"] == {
        "uds": "/run/browser-control/controller.sock",
        "retries": 0,
    }
    assert observed["requests"] == [("GET", "/browsers", {})]


@pytest.mark.asyncio
async def test_browser_proxy_keeps_legacy_http_when_socket_is_unset(monkeypatch):
    _browser_proxy = _browser_helpers()._browser_proxy

    monkeypatch.delenv("BROWSER_CONTROL_SOCKET", raising=False)
    observed = _install_browser_httpx(monkeypatch)
    result = await _browser_proxy("/browsers", method="GET")

    assert result == {"success": True}
    assert observed["client_kwargs"] == {"timeout": 120}
    assert observed["requests"] == [("GET", "http://browser-svc:8012/browsers", {})]


@pytest.mark.asyncio
async def test_browser_proxy_uds_failure_does_not_retry_over_tcp(monkeypatch):
    _browser_proxy = _browser_helpers()._browser_proxy

    monkeypatch.setenv(
        "BROWSER_CONTROL_SOCKET", "/run/browser-control/controller.sock"
    )
    observed = _install_browser_httpx(
        monkeypatch, error=httpx.ConnectError("UDS unavailable")
    )

    with pytest.raises(httpx.ConnectError, match="UDS unavailable"):
        await _browser_proxy("/browsers", method="GET")

    assert len(observed["requests"]) == 1
    assert observed["requests"][0][:2] == ("GET", "/browsers")
    assert observed["transport_kwargs"]["uds"] == (
        "/run/browser-control/controller.sock"
    )


@pytest.mark.asyncio
async def test_real_uds_roundtrip_covers_health_and_browser_methods(monkeypatch):
    from agent.health import check_browser

    requests: list[tuple[str, str, object]] = []
    with tempfile.TemporaryDirectory(prefix="browser-uds-", dir="/tmp") as directory:
        socket_path = f"{directory}/controller.sock"

        async def serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
            header = await reader.readuntil(b"\r\n\r\n")
            lines = header.decode("latin-1").split("\r\n")
            method, path, _version = lines[0].split(" ", 2)
            headers = {
                key.lower(): value.strip()
                for line in lines[1:]
                if line and ":" in line
                for key, value in [line.split(":", 1)]
            }
            body_bytes = await reader.readexactly(
                int(headers.get("content-length", "0"))
            )
            body = json.loads(body_bytes) if body_bytes else None
            requests.append((method, path, body))
            payload = json.dumps({"method": method, "path": path, "body": body})
            encoded = payload.encode()
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                + f"Content-Length: {len(encoded)}\r\nConnection: close\r\n\r\n".encode()
                + encoded
            )
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_unix_server(serve, path=socket_path)
        try:
            monkeypatch.setenv("BROWSER_CONTROL_SOCKET", socket_path)
            health = await check_browser(
                "http://browser-svc:8012", socket_path=socket_path
            )
            assert health["status"] == "ok"

            _browser_proxy = _browser_helpers()._browser_proxy
            get_result = await _browser_proxy("/browsers", method="GET")
            post_result = await _browser_proxy(
                "/browsers", method="POST", json_data={"ttl": 30}
            )
            delete_result = await _browser_proxy(
                "/browsers/session-1", method="DELETE"
            )

            assert get_result == {"method": "GET", "path": "/browsers", "body": None}
            assert post_result == {
                "method": "POST",
                "path": "/browsers",
                "body": {"ttl": 30},
            }
            assert delete_result == {
                "method": "DELETE",
                "path": "/browsers/session-1",
                "body": None,
            }
            assert requests == [
                ("GET", "/health", None),
                ("GET", "/browsers", None),
                ("POST", "/browsers", {"ttl": 30}),
                ("DELETE", "/browsers/session-1", None),
            ]
        finally:
            server.close()
            await server.wait_closed()


@pytest.mark.asyncio
async def test_missing_configured_socket_does_not_attempt_tcp_or_dns(monkeypatch):
    import socket

    from agent.health import check_browser

    resolver_calls: list[tuple[object, ...]] = []

    def record_getaddrinfo(*args, **kwargs):
        resolver_calls.append(args)
        raise AssertionError("configured UDS request attempted DNS/TCP resolution")

    monkeypatch.setattr(socket, "getaddrinfo", record_getaddrinfo)
    result = await check_browser(
        "http://browser-svc:8012",
        socket_path="/tmp/does-not-exist-browser-controller.sock",
    )

    assert result["status"] == "down"
    assert resolver_calls == []


def test_candidate_agent_receives_only_the_controller_socket_with_group_access():
    compose = yaml.safe_load(
        (ROOT / "compose.experimental-candidate.yml").read_text()
    )
    agent = compose["services"]["candidate-agent"]
    controller = compose["services"]["candidate-browser-controller"]

    assert "20000" in agent["group_add"]
    assert agent["environment"]["BROWSER_CONTROL_SOCKET"] == (
        "/run/browser-control/controller.sock"
    )
    assert agent["volumes"] == [
        "candidate_browser_controller_socket:/run/browser-control:ro"
    ]
    assert agent["depends_on"]["candidate-browser-controller"] == {
        "condition": "service_healthy"
    }
    assert "profiles" not in controller
    assert controller["networks"] == {"candidate_private": {"aliases": ["browser-svc"]}}
    assert not controller.get("ports")
    assert "candidate_browser_renderer_socket:/run/browser" not in agent.get(
        "volumes", []
    )
