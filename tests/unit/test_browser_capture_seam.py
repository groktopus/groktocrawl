"""Offline tests for the browser controller/renderer boundary."""

from __future__ import annotations

import asyncio
import errno
import runpy
import sys
import uuid
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "browser-svc"))

from browser_svc import controller as controller_module
from browser_svc import renderer_entrypoint
from browser_svc.app import (
    _chromium_launch_options,
    _navigation_response_fields,
    _protected_navigation_allowed,
)
from browser_svc.app import startup as browser_app_startup
from browser_svc.controller import create_app
from browser_svc.cookie_rpc import CookieRPCClient, CookieRPCServer

from tests.outcome_governance import governed_skip


def _skip_if_socket_binding_is_restricted(error: OSError) -> None:
    if error.errno not in {errno.EACCES, errno.EPERM}:
        raise error
    governed_skip(
        "sandbox does not permit local Unix socket binding",
        owner="repository-maintainer",
        issue="#436",
        classification="retained",
        environment="restricted test sandbox denies AF_UNIX bind",
    )


def test_hostile_navigation_probe_requires_gateway_denial_not_successful_health():
    probe = (
        Path(__file__).resolve().parents[1]
        / "integration"
        / "browser_capture_boundary_probe.py"
    )
    verify = runpy.run_path(str(probe))["_is_gateway_denial"]
    target = "http://127.0.0.1:8012/health"
    assert verify(
        {
            "success": True,
            "result": {
                "url": target,
                "http_status": 403,
                "content_type": "text/plain",
            },
        },
        target,
    )
    assert not verify(
        {
            "success": True,
            "result": {
                "url": target,
                "http_status": 200,
                "content_type": "application/json",
                "status": "ok",
                "active_sessions": 0,
            },
        },
        target,
    )
    assert not verify({"success": False, "error": "navigation failed"}, target)


def test_navigation_exposes_only_status_and_media_type():
    class Response:
        status = 403
        headers = {"content-type": "text/plain; charset=ascii", "x-secret": "hidden"}

    assert _navigation_response_fields(Response()) == {
        "http_status": 403,
        "content_type": "text/plain",
    }
    assert _navigation_response_fields(None) == {}

    class InvalidMetadataResponse:
        status = True
        headers = {"content-type": "text/plain\r\nX-Leak: value"}

    assert _navigation_response_fields(InvalidMetadataResponse()) == {}

    class OversizedMetadataResponse:
        status = 700
        headers = {"content-type": "a" * 130}

    assert _navigation_response_fields(OversizedMetadataResponse()) == {}


async def _start_unix_server(handler, *, path):
    try:
        return await asyncio.start_unix_server(handler, path=path)
    except OSError as error:
        _skip_if_socket_binding_is_restricted(error)


@pytest.mark.asyncio
async def test_controller_forwards_only_over_fixed_uds_and_preserves_api_response(
    tmp_path: Path,
):
    socket_path = str(Path("/tmp") / f"br-{uuid.uuid4().hex[:8]}.sock")
    observed: dict[str, bytes | str] = {}

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        header = await reader.readuntil(b"\r\n\r\n")
        lines = header.decode("latin-1").split("\r\n")
        observed["request_line"] = lines[0]
        observed["host"] = next(
            line.split(":", 1)[1].strip()
            for line in lines[1:]
            if line.lower().startswith("host:")
        )
        content_length = next(
            (
                int(line.split(":", 1)[1])
                for line in lines[1:]
                if line.lower().startswith("content-length:")
            ),
            0,
        )
        observed["body"] = await reader.readexactly(content_length)
        payload = b'{"success":true,"id":"session"}'
        writer.write(
            b"HTTP/1.1 201 Created\r\n"
            b"Content-Type: application/json\r\n"
            + f"Content-Length: {len(payload)}\r\n".encode()
            + b"Connection: close\r\n\r\n"
            + payload
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await _start_unix_server(handle, path=socket_path)
    try:
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(socket_path)),
            base_url="http://controller.invalid",
        )
        response = await client.post(
            "/browsers?mode=fixture",
            headers={"Host": "attacker.invalid", "content-type": "application/json"},
            content=b'{"ttl":20}',
        )
        await client.aclose()
    finally:
        server.close()
        await server.wait_closed()

    assert response.status_code == 201
    assert response.json() == {"success": True, "id": "session"}
    assert observed["request_line"] == "POST /browsers?mode=fixture HTTP/1.1"
    assert observed["host"] == "renderer.invalid"
    assert observed["body"] == b'{"ttl":20}'


@pytest.mark.asyncio
async def test_missing_renderer_is_explicit_unavailable_without_url_echo(
    tmp_path: Path,
):
    app = create_app(str(tmp_path / "absent.sock"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://controller.invalid"
    ) as client:
        response = await client.post(
            "/browsers/fixture/execute",
            json={"action": "navigate", "url": "http://source.example/path"},
        )
    assert response.status_code == 503
    assert response.headers["x-browser-error"] == "renderer-unavailable"
    assert response.text == '{"detail":"renderer unavailable"}'
    assert "source.example" not in response.text


def test_controller_rejects_unexpected_routes_without_contacting_renderer(
    tmp_path: Path,
):
    app = create_app(str(tmp_path / "absent.sock"))

    async def request():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://controller.invalid",
        ) as client:
            return await client.get("/internal/metadata")

    response = asyncio.run(request())
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_controller_rejects_oversized_request_before_renderer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(controller_module, "MAX_REQUEST_BYTES", 4)
    app = create_app(str(tmp_path / "absent.sock"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://controller.invalid"
    ) as client:
        response = await client.post("/browsers", content=b"12345")
    assert response.status_code == 413


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({}, {"headless": True}),
        (
            {
                "BROWSER_PROTECTED_RENDERER": "1",
                "BROWSER_CAPTURE_PROXY_URL": "http://capture-gateway:8080",
            },
            {"headless": True, "proxy": {"server": "http://capture-gateway:8080"}},
        ),
    ],
)
def test_chromium_proxy_configuration(env: dict[str, str], expected: dict):
    options = _chromium_launch_options(env)
    assert options["headless"] is expected["headless"]
    assert options.get("proxy") == expected.get("proxy")


def test_protected_renderer_without_proxy_fails_closed():
    with pytest.raises(RuntimeError, match="requires capture proxy"):
        _chromium_launch_options({"BROWSER_PROTECTED_RENDERER": "true"})


def test_malformed_protected_renderer_flag_fails_closed():
    with pytest.raises(RuntimeError, match="setting is invalid"):
        _chromium_launch_options({"BROWSER_PROTECTED_RENDERER": "enabled"})


@pytest.mark.parametrize(
    "url",
    [
        "https://source.example/article",
        "http://source.example/article",
        "data:text/html,fixture",
        "about:blank",
    ],
)
def test_protected_renderer_navigation_keeps_supported_schemes(url: str):
    assert _protected_navigation_allowed(url)


@pytest.mark.parametrize(
    "url",
    [
        "file:///var/lib/browser-cookies/secret",
        "javascript:alert(1)",
        "ftp://source.example",
    ],
)
def test_protected_renderer_blocks_local_or_non_http_navigation(url: str):
    assert not _protected_navigation_allowed(url)


@pytest.mark.parametrize(
    "proxy",
    [
        "https://gateway.example:8080",
        "http://user:password@gateway.example:8080",
        "http://gateway.example",
        "http://gateway.example:8080/path",
        "http://gateway.example:8080?x=1",
        "http://gateway.example:99999",
        "http://gateway%2eexample:8080",
    ],
)
def test_invalid_proxy_config_fails_closed(proxy: str):
    with pytest.raises(RuntimeError, match="capture proxy URL is invalid"):
        _chromium_launch_options({"BROWSER_CAPTURE_PROXY_URL": proxy})


@pytest.mark.asyncio
async def test_cookie_rpc_preserves_domain_keys_ttls_and_concurrent_sessions(
    monkeypatch: pytest.MonkeyPatch,
):
    class FakeRedis:
        def __init__(self):
            self.values: dict[str, tuple[str, int]] = {}

        async def get(self, key: str) -> str | None:
            value = self.values.get(key)
            return value[0] if value else None

        async def setex(self, key: str, ttl: int, value: str) -> None:
            self.values[key] = (value, ttl)

    socket_path = f"/tmp/cr-{uuid.uuid4().hex[:10]}.sock"
    monkeypatch.setattr("browser_svc.cookie_rpc.os.chown", lambda *_args: None)
    redis = FakeRedis()
    server = CookieRPCServer(socket_path, redis)
    try:
        await server.start()
    except OSError as error:
        _skip_if_socket_binding_is_restricted(error)
    first = CookieRPCClient(socket_path)
    second = CookieRPCClient(socket_path)
    try:
        await asyncio.gather(
            first.setex("cf:clearance:example.test", 30, "cookie-a"),
            second.setex("cf:clearance:other.test", 600, "cookie-b"),
        )
        assert await first.get("cf:clearance:example.test") == "cookie-a"
        assert await second.get("cf:clearance:other.test") == "cookie-b"
        assert redis.values == {
            "cf:clearance:example.test": ("cookie-a", 30),
            "cf:clearance:other.test": ("cookie-b", 600),
        }
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_cookie_rpc_rejects_unscoped_keys(monkeypatch: pytest.MonkeyPatch):
    socket_path = f"/tmp/cr-{uuid.uuid4().hex[:10]}.sock"
    monkeypatch.setattr("browser_svc.cookie_rpc.os.chown", lambda *_args: None)
    server = CookieRPCServer(socket_path, None)
    try:
        await server.start()
    except OSError as error:
        _skip_if_socket_binding_is_restricted(error)
    client = CookieRPCClient(socket_path)
    try:
        with pytest.raises(ValueError, match="invalid cookie key"):
            await client.get("admin:settings")
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_protected_app_startup_requires_firewall_marker(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("BROWSER_PROTECTED_RENDERER", "1")
    monkeypatch.delenv("BROWSER_CAPTURE_FIREWALL_READY", raising=False)
    with pytest.raises(RuntimeError, match="firewall is not active"):
        await browser_app_startup()


def test_renderer_firewall_installs_default_deny_rules(
    monkeypatch: pytest.MonkeyPatch,
):
    commands: list[list[str]] = []
    monkeypatch.setenv("BROWSER_PROTECTED_RENDERER", "1")
    monkeypatch.setenv("BROWSER_CAPTURE_PROXY_URL", "http://172.31.254.10:8080")
    monkeypatch.setattr(renderer_entrypoint, "_run_firewall_command", commands.append)

    renderer_entrypoint._install_firewall()

    assert ["iptables", "-w", "-P", "OUTPUT", "DROP"] in commands
    assert [
        "iptables",
        "-w",
        "-A",
        "OUTPUT",
        "-p",
        "tcp",
        "-d",
        "172.31.254.10/32",
        "--dport",
        "8080",
        "-j",
        "ACCEPT",
    ] in commands
    assert ["ip6tables", "-w", "-P", "OUTPUT", "DROP"] in commands
    assert not any("-o" in command and "lo" in command for command in commands)
    assert not any("--dport" in command and "53" in command for command in commands)


def test_renderer_firewall_rejects_dns_gateway(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("BROWSER_PROTECTED_RENDERER", "1")
    monkeypatch.setenv("BROWSER_CAPTURE_PROXY_URL", "http://gateway:8080")
    with pytest.raises(RuntimeError, match="numeric IPv4"):
        renderer_entrypoint._install_firewall()


def test_renderer_drops_network_capabilities_from_bounding_set(
    monkeypatch: pytest.MonkeyPatch,
):
    dropped: list[tuple[int, int]] = []
    monkeypatch.setattr(
        renderer_entrypoint,
        "_prctl",
        lambda option, capability: dropped.append((option, capability)) or 0,
    )
    renderer_entrypoint._drop_bounding_capabilities()
    assert dropped == [(24, 0), (24, 12), (24, 7), (24, 6), (24, 8)]


def test_renderer_sets_directory_mode_as_unprivileged_owner(monkeypatch):
    from types import SimpleNamespace

    calls = []
    identity = {"uid": 0}

    class Directory:
        def mkdir(self, **kwargs):
            calls.append("mkdir")

    directory = Directory()
    monkeypatch.setattr(renderer_entrypoint, "Path", lambda path: directory)

    def setuid(uid):
        identity["uid"] = uid
        calls.append("setuid")

    def chmod(path, mode):
        assert identity["uid"] == 10001
        assert path is directory and mode == 0o770
        calls.append("chmod")

    monkeypatch.setattr(
        renderer_entrypoint,
        "os",
        SimpleNamespace(
            chown=lambda path, uid, gid: calls.append(("chown", uid, gid)),
            setgroups=lambda groups: calls.append(("setgroups", tuple(groups))),
            setgid=lambda gid: calls.append(("setgid", gid)),
            setuid=setuid,
            chmod=chmod,
        ),
    )
    monkeypatch.setattr(
        renderer_entrypoint,
        "_drop_bounding_capabilities",
        lambda: calls.append("drop-caps"),
    )
    renderer_entrypoint._prepare_unprivileged_renderer()
    assert calls == [
        "mkdir",
        ("chown", 10001, 20000),
        "drop-caps",
        ("setgroups", (20000,)),
        ("setgid", 20000),
        "setuid",
        "chmod",
    ]
