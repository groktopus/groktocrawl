"""The scraper bootstrap allows only its fixed capture gateway and DNS."""

from __future__ import annotations

import asyncio
import os
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path

import httpx
import pytest
import uvicorn
import yaml
from scraper import capture_firewall

from common.private_uds import bind_private_listener

ROOT = Path(__file__).resolve().parents[2]


def test_prebound_uds_survives_actual_uvicorn_startup_with_private_mode(tmp_path):
    async def run_server():
        socket_dir = Path(tempfile.mkdtemp(prefix="sc-", dir="/tmp"))
        os.chown(socket_dir, os.getuid(), os.getgid())
        socket_dir.chmod(0o710)
        path = socket_dir / "app.sock"
        listener = bind_private_listener(
            path,
            directory_uid=os.getuid(),
            directory_gid=os.getgid(),
            directory_mode=0o710,
            socket_uid=os.getuid(),
            socket_gid=os.getgid(),
        )

        async def app(_scope, receive, send):
            await receive()
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ready"})

        server = uvicorn.Server(
            uvicorn.Config(app, fd=listener.fileno(), lifespan="off", access_log=False)
        )
        task = asyncio.create_task(server.serve())
        try:
            for _ in range(200):
                if server.started or task.done():
                    break
                await asyncio.sleep(0.01)
            assert server.started and not task.done()
            async with httpx.AsyncClient(
                transport=httpx.AsyncHTTPTransport(uds=str(path), retries=0),
                base_url="http://private.sock",
            ) as client:
                response = await client.get("/health")
            assert response.status_code == 200
            assert response.content == b"ready"
            info = path.lstat()
            assert stat.S_IMODE(info.st_mode) == 0o660
            assert info.st_uid == os.getuid()
            assert info.st_gid == os.getgid()
        finally:
            server.should_exit = True
            await asyncio.wait_for(task, timeout=5)
            listener.close()
            shutil.rmtree(socket_dir)

    asyncio.run(run_server())


@pytest.mark.parametrize("occupied_kind", ["symlink", "regular"])
def test_prebound_uds_rejects_unsafe_existing_path(tmp_path, occupied_kind):
    socket_dir = tmp_path / "socket-dir"
    socket_dir.mkdir()
    os.chown(socket_dir, os.getuid(), os.getgid())
    socket_dir.chmod(0o710)
    path = socket_dir / "api.sock"
    if occupied_kind == "symlink":
        target = socket_dir / "target"
        target.write_text("fixture")
        path.symlink_to(target)
    else:
        path.write_text("fixture")

    with pytest.raises(RuntimeError, match="path is occupied"):
        bind_private_listener(
            path,
            directory_uid=os.getuid(),
            directory_gid=os.getgid(),
            directory_mode=0o710,
            socket_uid=os.getuid(),
            socket_gid=os.getgid(),
        )


def test_firewall_is_default_deny_for_ipv4_and_ipv6_and_has_narrow_allowlist(monkeypatch):
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", run)
    capture_firewall.install_firewall()

    for binary in ("iptables", "ip6tables"):
        for chain in ("INPUT", "OUTPUT", "FORWARD"):
            assert [binary, "-w", "-P", chain, "DROP"] in commands
        assert [binary, "-w", "-F", "OUTPUT"] in commands
    assert [
        "iptables", "-w", "-A", "OUTPUT", "-p", "tcp", "-d",
        "172.31.254.2/32", "--dport", "8080", "-j", "ACCEPT",
    ] in commands
    assert [
        "iptables", "-w", "-A", "OUTPUT", "-p", "udp", "-d",
        "127.0.0.11/32", "--dport", "53", "-j", "ACCEPT",
    ] in commands
    assert not any("172.31.254.34" in command for row in commands for command in row)
    accepts = [row for row in commands if row[-2:] == ["-j", "ACCEPT"]]
    assert all(
        "-o lo" in " ".join(row)
        or "-i lo" in " ".join(row)
        or "--ctstate ESTABLISHED,RELATED" in " ".join(row)
        or "127.0.0.11/32" in row
        or "172.31.254.2/32" in row
        for row in accepts
    )


def test_firewall_rejects_any_gateway_override_before_running_commands(monkeypatch):
    calls = []
    monkeypatch.setenv("SCRAPER_CAPTURE_GATEWAY_IP", "172.31.254.34")
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: calls.append(args))
    with pytest.raises(RuntimeError, match="not the qualified fixed peer"):
        capture_firewall.install_firewall()
    assert calls == []


def test_firewall_command_failure_is_terminal(monkeypatch):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(subprocess.CalledProcessError(1, args[0])),
    )
    with pytest.raises(RuntimeError, match="firewall setup failed"):
        capture_firewall.install_firewall()


def test_worker_startup_drops_to_fixed_nonroot_uid_and_group(monkeypatch):
    calls = []
    monkeypatch.setattr(capture_firewall, "_drop_capabilities", lambda: calls.append("drop"))
    monkeypatch.setattr(capture_firewall.os, "stat", lambda _path: type("S", (), {"st_uid": 10001, "st_gid": 20000})())
    monkeypatch.setattr(capture_firewall.os, "setgroups", lambda values: calls.append(("groups", values)))
    monkeypatch.setattr(capture_firewall.os, "setgid", lambda value: calls.append(("gid", value)))
    monkeypatch.setattr(capture_firewall.os, "setuid", lambda value: calls.append(("uid", value)))
    monkeypatch.setattr(capture_firewall.os, "geteuid", lambda: 10001)
    monkeypatch.setattr(capture_firewall.os, "getegid", lambda: 20000)
    monkeypatch.setattr(capture_firewall.os, "umask", lambda value: calls.append(("umask", value)))
    monkeypatch.setenv("SCRAPER_API_SOCKET", "/run/scraper/app.sock")
    capture_firewall.drop_to_worker()
    assert calls == [
        "drop", ("groups", [20000]), ("gid", 20000), ("uid", 10001), ("umask", 0o117)
    ]


def test_candidate_scraper_is_capability_bootstrapped_on_capture_only_network():
    compose = yaml.safe_load((ROOT / "compose.experimental-candidate.yml").read_text())
    service = compose["services"]["candidate-scraper"]
    assert service["networks"] == ["candidate_capture"]
    assert service["user"] == "0:0"
    assert service.get("init") is not True
    assert set(service["cap_drop"]) == {"ALL"}
    assert set(service["cap_add"]) == {"CHOWN", "NET_ADMIN", "SETUID", "SETGID", "SETPCAP"}
    assert "scraper.capture_firewall" in (ROOT / "scraper-svc/docker-entrypoint.sh").read_text()
    assert "tini" in (ROOT / "scraper-svc/Dockerfile").read_text()
    assert any(path.endswith(":/run/scraper") for path in service["volumes"])
    assert all(
        path.endswith(":ro")
        for path in service["volumes"]
        if not path.endswith(":/run/scraper")
    )
    state = compose["services"]["candidate-scraper-state-control"]
    assert state["user"] == "10002:20000"
    assert state["mem_limit"] == "512m"
    socket_init = compose["services"]["candidate-scraper-socket-init"]
    assert "setup_socket_dir /run/scraper 10001:20000 2710" in socket_init["command"][-1]
    assert "setup_socket_dir /run/scraper-state 10002:20000 2710" in socket_init["command"][-1]
    assert "setup_socket_dir /run/scraper-llm 10002:20000 2710" in socket_init["command"][-1]
    assert "setup_socket_dir /run/browser-control 0:20000 2710" in socket_init["command"][-1]
    assert "CHOWN" in socket_init["cap_add"]


def test_bootstrap_execs_tini_only_after_firewall_and_privilege_drop(monkeypatch):
    calls = []
    monkeypatch.delenv("SCRAPER_CAPTURE_FIREWALL_READY", raising=False)
    monkeypatch.setattr(capture_firewall.os, "geteuid", lambda: 0)
    monkeypatch.setattr(capture_firewall, "install_firewall", lambda: calls.append("firewall"))
    monkeypatch.setattr(capture_firewall, "drop_to_worker", lambda: calls.append("drop"))

    def execve(path, argv, env):
        calls.append((path, argv, env["SCRAPER_CAPTURE_FIREWALL_READY"]))

    monkeypatch.setattr(capture_firewall.os, "execve", execve)
    capture_firewall.main()
    assert calls[:2] == ["firewall", "drop"]
    path, argv, ready = calls[2]
    assert path == "/usr/bin/tini"
    assert argv[1:4] == ["-s", "-g", "--"]
    assert argv[-2:] == ["-m", "scraper.capture_firewall"]
    assert ready == "1"


def test_server_start_rejects_privileged_pid1_or_worker_status():
    good = """Name:\tpython
Uid:\t10001\t10001\t10001\t10001
Gid:\t20000\t20000\t20000\t20000
CapEff:\t0000000000000000
CapPrm:\t0000000000000000
CapBnd:\t0000000000000000
NoNewPrivs:\t1
"""
    capture_firewall._validate_process_status(good, 10001, 20000)
    with pytest.raises(RuntimeError, match="retained bootstrap capabilities"):
        capture_firewall._validate_process_status(good.replace("CapEff:\t0000000000000000", "CapEff:\t0000000000001000"), 10001, 20000)
    with pytest.raises(RuntimeError, match="unexpected UID"):
        capture_firewall._validate_process_status(good.replace("10001", "0", 1), 10001, 20000)
