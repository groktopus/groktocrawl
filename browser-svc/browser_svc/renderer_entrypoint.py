"""Serve the Playwright renderer only on its private Unix socket."""

from __future__ import annotations

import asyncio
import ctypes
import ipaddress
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

import uvicorn

from .app import _chromium_launch_options, _protected_renderer_enabled
from .controller import DEFAULT_RENDERER_SOCKET, _renderer_socket_path


async def serve() -> None:
    # Fail before creating a reachable renderer socket if protected mode is
    # requested without a complete explicit proxy setting.
    _chromium_launch_options()
    if (
        _protected_renderer_enabled()
        and os.environ.get("BROWSER_CAPTURE_FIREWALL_READY") != "1"
    ):
        raise RuntimeError("protected renderer firewall is not active")
    socket_path = _renderer_socket_path(
        os.environ.get("BROWSER_RENDERER_SOCKET", DEFAULT_RENDERER_SOCKET)
    )
    socket_file = Path(socket_path)
    socket_file.parent.mkdir(mode=0o770, parents=True, exist_ok=True)
    if socket_file.is_symlink():
        raise RuntimeError("renderer socket path must not be a symlink")
    if socket_file.exists():
        if not socket_file.is_socket():
            raise RuntimeError("renderer socket path exists and is not a socket")
        socket_file.unlink()

    config = uvicorn.Config(
        "browser_svc.app:app",
        uds=socket_path,
        log_level=os.environ.get("LOG_LEVEL", "info").lower(),
        access_log=False,
    )
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    while not server.started and not task.done():
        await asyncio.sleep(0.01)
    if task.done():
        await task
        raise RuntimeError("renderer server stopped before startup")
    os.chmod(socket_path, 0o660)
    await task


def _run_firewall_command(command: list[str]) -> None:
    import subprocess

    try:
        subprocess.run(
            command,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError("protected renderer firewall setup failed") from exc


def _install_firewall() -> None:
    """Allow only established responses and the configured gateway socket."""
    options = _chromium_launch_options()
    proxy_server = options.get("proxy", {}).get("server")
    if not isinstance(proxy_server, str):
        raise RuntimeError("protected renderer requires capture proxy")
    parsed = urlsplit(proxy_server)
    try:
        gateway = ipaddress.IPv4Address(parsed.hostname or "")
        port = parsed.port
    except ValueError as exc:
        raise RuntimeError("protected renderer gateway must be numeric IPv4") from exc
    if parsed.scheme != "http" or port is None or not 1 <= port <= 65535:
        raise RuntimeError("protected renderer gateway is invalid")

    rules = [
        (
            "iptables",
            "INPUT",
            "-m",
            "conntrack",
            "--ctstate",
            "ESTABLISHED,RELATED",
            "-j",
            "ACCEPT",
        ),
        (
            "iptables",
            "OUTPUT",
            "-m",
            "conntrack",
            "--ctstate",
            "ESTABLISHED,RELATED",
            "-j",
            "ACCEPT",
        ),
        (
            "iptables",
            "OUTPUT",
            "-p",
            "tcp",
            "-d",
            f"{gateway}/32",
            "--dport",
            str(port),
            "-j",
            "ACCEPT",
        ),
    ]
    for binary, chain in (
        ("iptables", "INPUT"),
        ("iptables", "OUTPUT"),
        ("iptables", "FORWARD"),
    ):
        _run_firewall_command([binary, "-w", "-F", chain])
        _run_firewall_command([binary, "-w", "-P", chain, "DROP"])
    for binary, chain in (
        ("ip6tables", "INPUT"),
        ("ip6tables", "OUTPUT"),
        ("ip6tables", "FORWARD"),
    ):
        _run_firewall_command([binary, "-w", "-F", chain])
        _run_firewall_command([binary, "-w", "-P", chain, "DROP"])

    for binary, chain, *rule in rules:
        _run_firewall_command([binary, "-w", "-A", chain, *rule])
    for chain, rule_args in (
        (
            "INPUT",
            ("-m", "conntrack", "--ctstate", "ESTABLISHED,RELATED", "-j", "ACCEPT"),
        ),
        (
            "OUTPUT",
            ("-m", "conntrack", "--ctstate", "ESTABLISHED,RELATED", "-j", "ACCEPT"),
        ),
    ):
        _run_firewall_command(["ip6tables", "-w", "-A", chain, *rule_args])


def _net_admin_capability_present() -> bool:
    try:
        status = Path("/proc/self/status").read_text(encoding="ascii")
        masks = {
            line.split("\t", 1)[0].rstrip(":"): int(line.split("\t", 1)[1], 16)
            for line in status.splitlines()
            if line.startswith(("CapEff:", "CapBnd:"))
        }
        return any(masks.get(name, 0) & (1 << 12) for name in ("CapEff", "CapBnd"))
    except (OSError, StopIteration, ValueError, IndexError) as exc:
        raise RuntimeError("renderer capability state is unavailable") from exc


def _prctl(option: int, arg2: int) -> int:
    libc = ctypes.CDLL(None, use_errno=True)
    return int(libc.prctl(option, arg2, 0, 0, 0))


def _drop_bounding_capabilities() -> None:
    # CAP_NET_ADMIN is the only network capability granted to the container.
    # CHOWN prepares the shared socket directory; SETUID/SETGID are needed
    # only to drop to the unprivileged renderer user;
    # SETPCAP is dropped last after removing the other entries.
    pr_capbset_drop = 24
    for capability in (0, 12, 7, 6, 8):
        if _prctl(pr_capbset_drop, capability) != 0:
            raise RuntimeError("protected renderer could not drop capabilities")


def _prepare_unprivileged_renderer() -> None:
    directory = Path("/run/browser")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chown(directory, 10001, 20000)
    _drop_bounding_capabilities()
    os.setgroups([20000])
    os.setgid(20000)
    os.setuid(10001)
    # Change permissions as the new owner, after dropping privileges. Root
    # deliberately has no CAP_FOWNER once CHOWN changes directory ownership.
    os.chmod(directory, 0o770)


def main() -> None:
    if _protected_renderer_enabled():
        if "--after-firewall" not in sys.argv:
            _install_firewall()
            _prepare_unprivileged_renderer()
            child_env = os.environ.copy()
            child_env["BROWSER_CAPTURE_FIREWALL_READY"] = "1"
            child_env["HOME"] = "/home/browser"
            try:
                os.execve(
                    sys.executable,
                    [
                        sys.executable,
                        "-m",
                        "browser_svc.renderer_entrypoint",
                        "--after-firewall",
                    ],
                    child_env,
                )
            except OSError as exc:
                raise RuntimeError(
                    "protected renderer could not drop firewall capability"
                ) from exc
        if os.environ.get("BROWSER_CAPTURE_FIREWALL_READY") != "1":
            raise RuntimeError("protected renderer firewall is not active")
        if _net_admin_capability_present():
            raise RuntimeError("protected renderer retained firewall capability")
    asyncio.run(serve())


if __name__ == "__main__":
    main()
