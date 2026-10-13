"""Run inside candidate-scraper to test denied direct and allowed brokered traffic."""

from __future__ import annotations

import asyncio
import ctypes
import hashlib
import json
import os
import socket
import stat
import sys
from contextlib import suppress
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from scraper.source_http import (
    capture_gateway_hostname_is_public,
    source_httpx_client,
)
from scraper.valkey_control import RESERVE_SLOT_SCRIPT, ValkeyControlClient

PRIVATE_IP = "172.31.253.250"
CAPTURE_PEER_IP = "172.31.254.3"
CAPTURE_HOST_GATEWAY_IP = "172.31.254.1"
MODEL_GATEWAY_IP = "172.31.254.34"
FIXTURE_ORIGIN = os.environ.get("CAPTURE_FIXTURE_ORIGIN", "http://flare-origin.test").rstrip("/")
FIXTURE_URL = f"{FIXTURE_ORIGIN}/get"
MODEL_PROBE_NAME = os.environ.get("CAPTURE_PROBE_MODEL_NAME", "fixture-model")
# The model proxy permits a 150-second bounded connection; allow five seconds
# for local broker overhead while keeping this diagnostic call finite.
MODEL_PROBE_TIMEOUT_SECONDS = 155.0
MODEL_PROBE_MAX_TOKENS = 8
MODEL_PROBE_PROMPT = "Reply with OK."
STATE_SOCKET = "/run/scraper-state/control.sock"


def _validate_fixture_origin(origin: str) -> str:
    parsed = urlsplit(origin)
    try:
        port = parsed.port
    except ValueError as error:
        raise RuntimeError("capture fixture origin has an invalid port") from error
    if (
        parsed.scheme != "http"
        or not parsed.hostname
        or not parsed.hostname.endswith(".test")
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise RuntimeError("capture fixture origin must be a plain HTTP .test origin")
    return parsed.hostname


def _direct_tcp_denied(host: str, port: int) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(1.5)
    try:
        try:
            sock.connect((host, port))
        except OSError:
            return
        raise RuntimeError(f"direct TCP unexpectedly reached {host}:{port}")
    finally:
        sock.close()


def _navigation_diagnostics(
    payload: object, expected_host: str | None
) -> tuple[bool, bool, int | None]:
    if not isinstance(payload, dict):
        return False, False, None
    result = payload.get("result")
    host_matches = (
        isinstance(result, dict)
        and isinstance(result.get("url"), str)
        and urlsplit(result["url"]).hostname == expected_host
    )
    status = (
        result.get("http_status")
        if isinstance(result, dict)
        and type(result.get("http_status")) is int
        and 100 <= result["http_status"] <= 599
        else None
    )
    return payload.get("success") is True, host_matches, status


def _marker_diagnostics(payload: object) -> tuple[bool, bool, bool, str]:
    result = payload.get("result") if isinstance(payload, dict) else None
    script_result = result.get("script_result") if isinstance(result, dict) else None
    if not isinstance(script_result, dict):
        return False, False, False, "unknown"
    marker_state = script_result.get("markerState")
    if marker_state not in {
        "pending",
        "PRIVATE_TARGET_BLOCKED",
        "PRIVATE_TARGET_REACHABLE",
        "PRIVATE_TARGET_UNEXPECTED_RESPONSE",
        "other",
    }:
        marker_state = "unknown"
    return (
        True,
        script_result.get("fixtureHostMatches") is True,
        script_result.get("markerExists") is True,
        marker_state,
    )


def _assert_process_identity(pid: int, uid: int, gid: int) -> None:
    fields = {}
    with open(f"/proc/{pid}/status", encoding="ascii") as stream:
        for line in stream:
            if line.startswith(("Uid:", "Gid:", "CapEff:", "CapPrm:", "CapBnd:", "NoNewPrivs:")):
                name, value = line.split(":", 1)
                fields[name] = value.split()
    if [int(value) for value in fields.get("Uid", [])] != [uid] * 4:
        raise RuntimeError("protected scraper process has an unexpected UID")
    if [int(value) for value in fields.get("Gid", [])] != [gid] * 4:
        raise RuntimeError("protected scraper process has an unexpected GID")
    if any(int(fields.get(name, ["-1"])[0], 16) != 0 for name in ("CapEff", "CapPrm", "CapBnd")):
        raise RuntimeError("protected scraper process retained bootstrap capabilities")
    if fields.get("NoNewPrivs") != ["1"]:
        raise RuntimeError("protected scraper process lacks no-new-privileges")


def _drop_probe_capabilities() -> None:
    class Header(ctypes.Structure):
        _fields_ = [("version", ctypes.c_uint32), ("pid", ctypes.c_int)]

    class Data(ctypes.Structure):
        _fields_ = [
            ("effective", ctypes.c_uint32),
            ("permitted", ctypes.c_uint32),
            ("inheritable", ctypes.c_uint32),
        ]

    header = Header(0x20080522, 0)
    data = (Data * 2)()
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.capset(ctypes.byref(header), ctypes.byref(data)) != 0:
        raise RuntimeError("boundary probe could not drop its own capabilities")


def _protected_worker_pid() -> int:
    matches = []
    for entry in os.scandir("/proc"):
        if not entry.name.isdecimal():
            continue
        try:
            with open(f"/proc/{entry.name}/cmdline", "rb") as stream:
                command = stream.read(4096)
        except OSError:
            continue
        if command.split(b"\x00") == [
            os.fsencode(sys.executable), b"-m", b"scraper.capture_firewall", b""
        ]:
            matches.append(int(entry.name))
    if len(matches) != 1:
        raise RuntimeError("unique protected scraper worker process was not found")
    return matches[0]


def _assert_control_socket_permissions() -> None:
    bindings = (
        ("/run/scraper/app.sock", 10001),
        ("/run/scraper-state/control.sock", 10002),
        ("/run/scraper-llm/control.sock", 10002),
        ("/run/browser-control/controller.sock", 0),
        ("/run/flare-control/control.sock", 10003),
    )
    for path, owner_uid in bindings:
        info = os.lstat(path)
        parent = os.lstat(os.path.dirname(path))
        if (
            not stat.S_ISSOCK(info.st_mode)
            or info.st_uid != owner_uid
            or info.st_gid != 20000
            or stat.S_IMODE(info.st_mode) != 0o660
            or not stat.S_ISDIR(parent.st_mode)
            or parent.st_uid != owner_uid
            or parent.st_gid != 20000
            or stat.S_IMODE(parent.st_mode) != 0o2710
        ):
            raise RuntimeError(
                "control socket permissions invalid: "
                f"path={path} owner={info.st_uid}:{info.st_gid} "
                f"mode={stat.S_IMODE(info.st_mode):04o}; "
                f"parent={os.path.dirname(path)} "
                f"owner={parent.st_uid}:{parent.st_gid} "
                f"mode={stat.S_IMODE(parent.st_mode):04o}; "
                f"expected socket={owner_uid}:20000/0660 "
                f"directory={owner_uid}:20000/2710"
            )


async def _raw_state_rpc(request: dict) -> dict:
    body = json.dumps(request, separators=(",", ":")).encode()
    if len(body) > 4096:
        raise RuntimeError("state RPC probe request exceeded its bound")
    reader, writer = await asyncio.wait_for(
        asyncio.open_unix_connection(STATE_SOCKET), timeout=2
    )
    try:
        writer.write(body + b"\n")
        await asyncio.wait_for(writer.drain(), timeout=2)
        line = await asyncio.wait_for(reader.readline(), timeout=5)
        if not line or len(line) > 4096 or not line.endswith(b"\n"):
            raise RuntimeError("state RPC probe response exceeded its bound")
        value = json.loads(line)
        if not isinstance(value, dict):
            raise RuntimeError("state RPC returned a non-object response")
        return value
    finally:
        writer.close()
        with suppress(OSError, TimeoutError):
            await asyncio.wait_for(writer.wait_closed(), timeout=1)


async def _exercise_valkey_capability() -> None:
    token = hashlib.sha256(uuid4().bytes).hexdigest()
    client = ValkeyControlClient(STATE_SOCKET)
    if not await client.ping():
        raise RuntimeError("Valkey RPC ping failed")

    keys = {
        "cache": f"scrape_cache:{token}",
        "robots": f"politeness:robots:{token}",
        "rate": f"politeness:rate:{token}",
        "clearance": f"cf:clearance:probe-{token[:16]}.fixture.test",
    }
    for namespace, key in keys.items():
        value = f"rpc-probe:{namespace}:{token}"
        await client.setex(key, 60, value)
        if await client.get(key) != value:
            raise RuntimeError(f"Valkey RPC round-trip failed for {namespace}")

    wait_ms = await client.eval(
        RESERVE_SLOT_SCRIPT,
        1,
        f"politeness:rate:{token}:slots",
        0,
        30000,
    )
    if not 0 <= wait_ms <= 30000:
        raise RuntimeError("Valkey reservation script returned an invalid wait")

    denied_key = await _raw_state_rpc(
        {"operation": "get", "key": f"admin:probe:{token}"}
    )
    denied_operation = await _raw_state_rpc({"operation": "flushall"})
    if denied_key.get("ok") is not False or denied_operation.get("ok") is not False:
        raise RuntimeError("Valkey RPC accepted an unapproved key or operation")
    await client.aclose()


async def _exercise_model_broker() -> None:
    """Make one bounded, output-capped call through the private model broker."""
    model_transport = httpx.AsyncHTTPTransport(
        uds="/run/scraper-llm/control.sock", retries=0
    )
    async with httpx.AsyncClient(
        transport=model_transport,
        base_url="http://llm-control",
        trust_env=False,
        timeout=MODEL_PROBE_TIMEOUT_SECONDS,
    ) as model:
        completion = await asyncio.wait_for(
            model.post(
                "/recovery/chat/completions",
                headers={"content-type": "application/json"},
                json={
                    "model": MODEL_PROBE_NAME,
                    "messages": [{"role": "user", "content": MODEL_PROBE_PROMPT}],
                    "max_tokens": MODEL_PROBE_MAX_TOKENS,
                },
            ),
            timeout=MODEL_PROBE_TIMEOUT_SECONDS,
        )
        if completion.status_code != 200:
            payload = completion.json() if completion.headers.get("content-type", "").startswith("application/json") else {}
            error_code = payload.get("error_code") if isinstance(payload, dict) else None
            if error_code not in {
                "configuration_unavailable",
                "client_unavailable",
                "upstream_transport_unavailable",
                "upstream_response_unavailable",
            }:
                error_code = "unknown"
            raise RuntimeError(
                f"private model broker failed status={completion.status_code} error_code={error_code}"
            )
        if not completion.json().get("choices"):
            raise RuntimeError("private model broker returned no completion")


async def main() -> None:
    fixture_host = _validate_fixture_origin(FIXTURE_ORIGIN)
    if not MODEL_PROBE_NAME or len(MODEL_PROBE_NAME) > 256:
        raise RuntimeError("capture model probe name is invalid")
    _drop_probe_capabilities()
    if (os.getuid(), os.getgid()) != (10001, 20000) or 20000 not in os.getgroups():
        raise RuntimeError("scraper did not run as the designated unprivileged identity")
    _assert_process_identity(_protected_worker_pid(), 10001, 20000)
    _assert_process_identity(1, 10001, 20000)
    _assert_control_socket_permissions()
    try:
        socket.getaddrinfo(fixture_host, None)
    except socket.gaierror:
        pass
    else:
        raise RuntimeError("fixture DNS unexpectedly resolved in isolated scraper")
    if not await capture_gateway_hostname_is_public(fixture_host):
        raise RuntimeError("capture gateway DNS policy rejected the public fixture")
    _direct_tcp_denied(PRIVATE_IP, 19001)
    _direct_tcp_denied(CAPTURE_PEER_IP, 19001)
    _direct_tcp_denied(CAPTURE_HOST_GATEWAY_IP, 19001)
    _direct_tcp_denied("1.1.1.1", 80)
    _direct_tcp_denied(MODEL_GATEWAY_IP, 8080)
    for target in (PRIVATE_IP, CAPTURE_PEER_IP, CAPTURE_HOST_GATEWAY_IP):
        udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            with suppress(OSError):
                udp.sendto(b"private-egress-probe", (target, 19002))
        finally:
            udp.close()

    async with source_httpx_client(timeout=15) as client:
        response = await client.get(FIXTURE_URL)
        private_denial = await client.get(f"http://{PRIVATE_IP}:19001/probe")
    response.raise_for_status()
    if "FLARE_GET_OK" not in response.text:
        raise RuntimeError("source request did not traverse the capture gateway")
    private_denial_type = private_denial.headers.get("content-type", "").split(";", 1)[0]
    if private_denial.status_code != 403 or private_denial_type != "text/plain":
        raise RuntimeError(
            "capture gateway private-target denial invalid "
            f"http_status={private_denial.status_code} content_type_text_plain="
            f"{private_denial_type == 'text/plain'}"
        )

    await _exercise_valkey_capability()

    # Exercise the private operator model through its fixed UDS broker. The
    # scraper has no model URL/key and is not attached to candidate_private.
    await _exercise_model_broker()

    api = "http://candidate-browser-controller:8012"
    transport = httpx.AsyncHTTPTransport(
        uds="/run/browser-control/controller.sock", retries=0
    )
    async with httpx.AsyncClient(
        transport=transport, base_url=api, trust_env=False, timeout=45
    ) as browser:
        created = await browser.post("/browsers", json={"ttl": 60})
        created.raise_for_status()
        session_id = created.json()["id"]
        try:
            navigation = await browser.post(
                f"/browsers/{session_id}/execute",
                json={"action": "navigate", "url": FIXTURE_URL, "timeout": 30000},
            )
            navigation.raise_for_status()
            navigation_success, fixture_host_matches, navigation_status = (
                _navigation_diagnostics(navigation.json(), fixture_host)
            )
            if (
                not navigation_success
                or not fixture_host_matches
                or navigation_status != 200
            ):
                raise RuntimeError(
                    "browser fixture navigation invalid "
                    f"success={navigation_success} "
                    f"fixture_host_matches={fixture_host_matches} "
                    f"http_status={navigation_status}"
                )
            script = await browser.post(
                f"/browsers/{session_id}/execute",
                json={
                    "action": "executeScript",
                    "script": (
                        "async () => { const end = Date.now() + 5000; "
                        "while (document.querySelector('#private-check')?.textContent?.trim() "
                        "=== 'pending' && Date.now() < end) "
                        "await new Promise(resolve => setTimeout(resolve, 50)); "
                        "const marker = document.querySelector('#private-check'); "
                        "const value = marker?.textContent?.trim(); "
                        "return {fixtureHostMatches: location.hostname === "
                        f"{json.dumps(fixture_host)}, "
                        "markerExists: marker !== null, "
                        "markerState: ['pending', 'PRIVATE_TARGET_BLOCKED', "
                        "'PRIVATE_TARGET_REACHABLE'].includes(value) ? value : 'other'}; }"
                    ),
                },
            )
            script.raise_for_status()
            result_valid, script_host_matches, marker_exists, marker_state = (
                _marker_diagnostics(script.json())
            )
            if not (marker_exists and script_host_matches and marker_state == "PRIVATE_TARGET_BLOCKED"):
                raise RuntimeError(
                    "browser private-sentinel marker invalid "
                    f"result_valid={result_valid} "
                    f"fixture_host_matches={script_host_matches} "
                    f"marker_exists={marker_exists} marker_state={marker_state}"
                )
        finally:
            await browser.delete(f"/browsers/{session_id}")

    print("scraper_capture_boundary=pass")


if __name__ == "__main__":
    asyncio.run(main())
