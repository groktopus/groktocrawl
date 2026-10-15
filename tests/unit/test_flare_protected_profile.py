from __future__ import annotations

import importlib.util
import io
import os
import shutil
import socket
import stat
import subprocess
import tempfile
from email.message import Message
from pathlib import Path
from threading import BoundedSemaphore

import pytest
import yaml

ROOT = Path(__file__).parents[2]


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PATCHER = _load_module(ROOT / "flare-protected/patch_upstream.py", "flare_patcher")
PROXY = _load_module(ROOT / "flare-control/control_proxy.py", "flare_control_proxy")
RENDERER_PROBE = _load_module(
    ROOT / "tests/integration/protected_flare_renderer_probe.py", "flare_renderer_probe"
)


def test_flare_bootstrap_has_only_required_setup_caps_and_drops_them_before_chmod():
    compose = yaml.safe_load((ROOT / "compose.experimental-candidate.yml").read_text())
    renderer = compose["services"]["candidate-flare-renderer"]
    control = compose["services"]["candidate-flare-control"]
    control_socket_init = compose["services"]["candidate-flare-control-socket-init"]
    assert control["user"] == "10003:10003"
    assert "20000" in {str(group) for group in control["group_add"]}
    assert "20000" in {str(group) for group in renderer["group_add"]}
    assert renderer["cap_drop"] == ["ALL"]
    assert set(renderer["cap_add"]) == {
        "CHOWN",
        "NET_ADMIN",
        "SETUID",
        "SETGID",
        "SETPCAP",
    }
    entrypoint = (ROOT / "flare-protected/entrypoint.sh").read_text()
    assert entrypoint.index('chown "$flare_uid:$control_gid"') < entrypoint.index(
        "exec setpriv"
    )
    setpriv_section = entrypoint[entrypoint.index("exec setpriv") :]
    assert "chmod 2770 /run/flaresolverr" in setpriv_section
    assert entrypoint.count("chmod 2770") == 1
    assert "FOWNER" not in renderer["cap_add"]
    assert control["mem_limit"] == "256m"
    assert PROXY.MAX_ACTIVE_REQUESTS == 2
    assert "chmod 2710 /run/flare-control" in control_socket_init["command"][-1]
    assert "chown 10003:20000 /run/flare-control" in control_socket_init["command"][-1]
    assert "10003:20000:2710" in control_socket_init["command"][-1]
    dockerfile = (ROOT / "flare-control/Dockerfile").read_text()
    assert "USER 10003:10003" in dockerfile
    assert PROXY.MAX_RESPONSE_BYTES == 8 * 1024 * 1024


@pytest.mark.parametrize("field", ["CapEff", "CapPrm", "CapBnd", "CapAmb"])
def test_flare_process_probe_requires_zero_process_capabilities(field):
    status = {
        "CapEff": "0",
        "CapPrm": "0",
        "CapBnd": "0",
        "CapAmb": "0",
        "NoNewPrivs": "1",
    }
    RENDERER_PROBE.validate_process_status(status)
    status[field] = "1"
    with pytest.raises(RuntimeError, match="retained capability"):
        RENDERER_PROBE.validate_process_status(status)


def test_flare_probe_matches_python_process_not_dumb_init_ancestor():
    assert RENDERER_PROBE.is_flare_python_argv(
        b"/usr/local/bin/python\0-u\0/app/flaresolverr.py\0"
    )
    assert not RENDERER_PROBE.is_flare_python_argv(
        b"/usr/bin/dumb-init\0--\0/usr/local/bin/python\0-u\0/app/flaresolverr.py\0"
    )


def test_flare_dumb_init_is_started_inside_the_privilege_drop():
    dockerfile = (ROOT / "flare-protected/Dockerfile").read_text()
    assert 'ENTRYPOINT ["/usr/local/bin/protected-flare-entrypoint"]' in dockerfile
    assert 'CMD ["/usr/bin/dumb-init", "--", "/usr/local/bin/python"' in dockerfile
    assert "ENV HOME=/tmp/groktocrawl-flare-home" in dockerfile
    assert "XDG_CACHE_HOME=/tmp/groktocrawl-flare-home/.cache" in dockerfile
    assert "XDG_DATA_HOME=/tmp/groktocrawl-flare-home/.local/share" in dockerfile
    assert "XDG_CONFIG_HOME=/tmp/groktocrawl-flare-home/.config" in dockerfile


def test_flare_home_is_fixed_private_and_overrides_caller_environment(tmp_path):
    entrypoint = (ROOT / "flare-protected/entrypoint.sh").read_text()
    start = entrypoint.index("        HOME=/tmp/groktocrawl-flare-home")
    end = entrypoint.index('        exec "$@"', start)
    setup = entrypoint[start:end]
    home = tmp_path / "flare-home"
    cache = home / ".cache"
    data = home / ".local" / "share"
    config = home / ".config"
    setup = setup.replace("/tmp/groktocrawl-flare-home/.local/share", str(data))
    setup = setup.replace("/tmp/groktocrawl-flare-home/.config", str(config))
    setup = setup.replace("/tmp/groktocrawl-flare-home/.cache", str(cache))
    setup = setup.replace("/tmp/groktocrawl-flare-home", str(home))
    child = 'printf "%s\\n%s\\n%s\\n%s\\n" "$HOME" "$XDG_CACHE_HOME" "$XDG_DATA_HOME" "$XDG_CONFIG_HOME"'
    completed = subprocess.run(
        [
            "/bin/sh",
            "-c",
            setup + '\nexec "$@"\n',
            "protected-flare",
            "/bin/sh",
            "-c",
            child,
        ],
        check=True,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "HOME": "/tmp/caller-home",
            "XDG_CACHE_HOME": "/tmp/caller-cache",
            "XDG_DATA_HOME": "/tmp/caller-data",
            "XDG_CONFIG_HOME": "/tmp/caller-config",
        },
    )
    assert completed.stdout.splitlines() == [
        str(home),
        str(cache),
        str(data),
        str(config),
    ]
    for path in (home, cache, data, config):
        assert path.stat().st_mode & 0o777 == 0o700


def test_upstream_patch_forces_proxy_validates_scheme_and_moves_api_to_uds():
    source = """import os
req = V1RequestBase(data)
serve(handler, host=self.host, port=self.port, asyncore_use_poll=True)
"""
    patched = PATCHER.patch(source)
    assert 'data["proxy"] = {"url": "http://172.31.254.18:8080"}' in patched
    assert 'command in {"request.get", "request.post"}' in patched
    assert 'parsed_target.scheme not in {"http", "https"}' in patched
    assert "unix_socket=socket_path" in patched
    assert "host=self.host, port=self.port" not in patched


def test_upstream_patch_fails_closed_on_source_drift():
    with pytest.raises(SystemExit, match="source drift"):
        PATCHER.patch("req = V1RequestBase(data)\n")


def test_upstream_chromium_proxy_override_disables_loopback_bypass():
    patched = PATCHER.patch_utils(
        "options.add_argument('--proxy-server=%s' % proxy_url)\n"
    )
    assert "--proxy-bypass-list=<-loopback>" in patched
    with pytest.raises(SystemExit, match="source drift"):
        PATCHER.patch_utils("# changed upstream proxy setup\n")


def test_control_proxy_forwards_existing_api_over_unix_socket(monkeypatch):
    sent: list[bytes] = []
    closed: list[bool] = []
    response = (
        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
        b"Content-Length: 41\r\nConnection: close\r\n\r\n"
        b'{"status":"ok","solution":{"status":200}}'
    )

    class FakeSocket:
        def __init__(self, *_args):
            self.blocks = [response[:20], response[20:], b""]

        def settimeout(self, timeout):
            assert timeout == PROXY.UPSTREAM_TIMEOUT_SECONDS

        def connect(self, path):
            assert path == "/run/flaresolverr/api.sock"

        def sendall(self, payload):
            sent.append(payload)

        def recv(self, _size):
            return self.blocks.pop(0)

        def close(self):
            closed.append(True)

    monkeypatch.setattr(PROXY.socket, "socket", FakeSocket)
    request = (
        b"POST /v1 HTTP/1.1\r\nHost: flaresolverr.local\r\n"
        b"Content-Length: 2\r\nConnection: close\r\n\r\n{}"
    )
    raw = PROXY.UnixHTTPConnection("/run/flaresolverr/api.sock").request(request)
    assert sent == [request]
    assert raw.endswith(b'{"status":"ok","solution":{"status":200}}')
    assert closed == [True]


def test_flare_control_uds_binds_exact_mode_and_closes_only_owned_socket():
    socket_dir = Path(tempfile.mkdtemp(prefix="fc-", dir="/tmp"))
    os.chown(socket_dir, os.getuid(), os.getgid())
    socket_dir.chmod(0o710)
    path = socket_dir / "control.sock"
    server = PROXY.UnixControlServer(
        str(path),
        PROXY.ControlHandler,
        directory_uid=os.getuid(),
        directory_gid=os.getgid(),
        directory_mode=0o710,
        socket_gid=os.getgid(),
    )
    try:
        info = path.lstat()
        assert stat.S_ISSOCK(info.st_mode)
        assert info.st_uid == os.getuid()
        assert info.st_gid == os.getgid()
        assert stat.S_IMODE(info.st_mode) == 0o660
    finally:
        server.server_close()
        shutil.rmtree(socket_dir)


def test_control_handler_forwards_application_json_to_uds(monkeypatch):
    sent: list[bytes] = []
    response = b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}"

    class FakeUnixHTTPConnection:
        def __init__(self, path):
            assert path == PROXY.SOCKET_PATH

        def request(self, request):
            sent.append(request)
            return response

    monkeypatch.setattr(PROXY, "UnixHTTPConnection", FakeUnixHTTPConnection)
    handler = object.__new__(PROXY.ControlHandler)
    handler.command = "POST"
    handler.path = "/v1"
    handler.headers = Message()
    handler.headers["Content-Length"] = "2"
    handler.headers["Content-Type"] = "application/json; charset=utf-8"
    handler.rfile = io.BytesIO(b"{}")
    handler.wfile = io.BytesIO()
    handler.send_response = lambda _status: None
    handler.send_header = lambda *_args: None
    handler.end_headers = lambda: None
    handler._headers_buffer = []
    handler.close_connection = False

    handler._forward()

    assert len(sent) == 1
    assert b"POST /v1 HTTP/1.1\r\n" in sent[0]
    assert b"Content-Type: application/json\r\n" in sent[0]
    assert sent[0].endswith(b"\r\n\r\n{}")


def test_control_handler_rejects_non_json_v1_before_uds(monkeypatch):
    calls: list[bytes] = []
    monkeypatch.setattr(
        PROXY.UnixHTTPConnection,
        "request",
        lambda _self, request: calls.append(request) or b"",
    )
    handler = object.__new__(PROXY.ControlHandler)
    handler.command = "POST"
    handler.path = "/v1"
    handler.headers = Message()
    handler.headers["Content-Length"] = "2"
    handler.headers["Content-Type"] = "text/plain"
    handler.rfile = io.BytesIO(b"{}")
    handler.send_error = lambda status: setattr(handler, "error_status", status)

    handler._forward()

    assert handler.error_status == 415
    assert calls == []


def test_control_handler_rejects_oversized_request_before_body_read(monkeypatch):
    calls: list[bytes] = []
    monkeypatch.setattr(
        PROXY.UnixHTTPConnection,
        "request",
        lambda _self, request: calls.append(request) or b"",
    )

    class UnreadableBody:
        def read(self, _length):
            raise AssertionError("oversized request body must not be read")

    handler = object.__new__(PROXY.ControlHandler)
    handler.command = "POST"
    handler.path = "/v1"
    handler.headers = Message()
    handler.headers["Content-Length"] = str(PROXY.MAX_REQUEST_BYTES + 1)
    handler.headers["Content-Type"] = "application/json"
    handler.rfile = UnreadableBody()
    handler.send_error = lambda status: setattr(handler, "error_status", status)

    handler._forward()

    assert handler.error_status == 413
    assert calls == []


def test_control_proxy_rejects_outside_api_paths():
    assert PROXY.route_status("CONNECT", "example.invalid:443") == 404
    assert PROXY.route_status("POST", "/health") == 405
    assert PROXY.route_status("GET", "/health") == 200


def test_control_bridge_bounds_archived_response_and_closes_socket(monkeypatch):
    closed: list[bool] = []
    body = b"x" * (PROXY.MAX_RESPONSE_BYTES + 1)
    response = (
        b"HTTP/1.1 200 OK\r\nContent-Length: "
        + str(len(body)).encode()
        + b"\r\n\r\n"
        + body
    )

    class FakeSocket:
        def __init__(self, *_args):
            self.blocks = [response[: 64 * 1024], response[64 * 1024 :]]

        def settimeout(self, timeout):
            assert timeout == PROXY.UPSTREAM_TIMEOUT_SECONDS

        def connect(self, _path):
            return None

        def sendall(self, _payload):
            return None

        def recv(self, _size):
            return self.blocks.pop(0) if self.blocks else b""

        def close(self):
            closed.append(True)

    monkeypatch.setattr(PROXY.socket, "socket", FakeSocket)
    with pytest.raises(ValueError, match="exceeds the control limit"):
        PROXY.UnixHTTPConnection("/run/flaresolverr/api.sock").request(b"request")
    assert closed == [True]


def test_control_handler_client_body_read_has_a_timeout(monkeypatch):
    monkeypatch.setattr(PROXY, "CLIENT_IO_TIMEOUT_SECONDS", 0.01)
    client, peer = socket.socketpair()
    handler = object.__new__(PROXY.ControlHandler)
    handler.request = client
    handler.client_address = None
    handler.server = None
    try:
        handler.setup()
        assert client.gettimeout() == 0.01
        with pytest.raises(TimeoutError):
            handler.rfile.read(1)
    finally:
        handler.rfile.close()
        handler.wfile.close()
        peer.close()
        client.close()


def test_control_server_bounds_concurrency_and_releases_slots(monkeypatch):
    accepted: list[object] = []
    closed: list[object] = []

    class FakeRequest:
        def __init__(self):
            self.responses: list[bytes] = []

        def settimeout(self, _timeout):
            return None

        def sendall(self, response):
            self.responses.append(response)

    server = object.__new__(PROXY.BoundedThreadingHTTPServer)
    server._request_slots = BoundedSemaphore(PROXY.MAX_ACTIVE_REQUESTS)
    server.shutdown_request = lambda request: closed.append(request)
    monkeypatch.setattr(
        PROXY.ThreadingHTTPServer,
        "process_request",
        lambda _self, request, _address: accepted.append(request),
    )
    requests = [FakeRequest() for _ in range(4)]
    for request in requests[: PROXY.MAX_ACTIVE_REQUESTS]:
        server.process_request(request, ("local", 1))
    server.process_request(requests[2], ("local", 1))
    assert accepted == requests[: PROXY.MAX_ACTIVE_REQUESTS]
    assert b"503 Service Unavailable" in requests[2].responses[0]
    assert closed == [requests[2]]

    monkeypatch.setattr(
        PROXY.ThreadingHTTPServer, "process_request_thread", lambda *_args: None
    )
    server.process_request_thread(requests[0], ("local", 1))
    server.process_request(requests[3], ("local", 1))
    assert accepted == [requests[0], requests[1], requests[3]]
    server.process_request_thread(requests[1], ("local", 1))
    server.process_request_thread(requests[3], ("local", 1))
