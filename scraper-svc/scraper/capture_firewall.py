"""Install the scraper's gateway-only network policy, then drop to its worker UID."""

from __future__ import annotations

import ctypes
import ipaddress
import os
import stat
import sys
from pathlib import Path

import uvicorn

from common.private_uds import bind_private_listener

GATEWAY = "172.31.254.2"
GATEWAY_PORT = 8080
WORKER_UID = 10001
WORKER_GID = 20000


def _run(command: list[str]) -> None:
    import subprocess

    try:
        subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError("scraper gateway-only firewall setup failed") from exc


def install_firewall() -> None:
    """Default deny every network path except DNS and the fixed HTTP gateway."""
    gateway = ipaddress.IPv4Address(os.environ.get("SCRAPER_CAPTURE_GATEWAY_IP", GATEWAY))
    port_raw = os.environ.get("SCRAPER_CAPTURE_GATEWAY_PORT", str(GATEWAY_PORT))
    if not port_raw.isdecimal() or not 1 <= int(port_raw) <= 65535:
        raise RuntimeError("invalid scraper capture gateway port")
    port = int(port_raw)
    if str(gateway) != GATEWAY or port != GATEWAY_PORT:
        raise RuntimeError("scraper capture gateway is not the qualified fixed peer")

    for binary in ("iptables", "ip6tables"):
        for chain in ("INPUT", "OUTPUT", "FORWARD"):
            _run([binary, "-w", "-F", chain])
            _run([binary, "-w", "-P", chain, "DROP"])
        _run([binary, "-w", "-A", "INPUT", "-i", "lo", "-j", "ACCEPT"])
        _run([binary, "-w", "-A", "OUTPUT", "-o", "lo", "-j", "ACCEPT"])
        _run([binary, "-w", "-A", "INPUT", "-m", "conntrack", "--ctstate", "ESTABLISHED,RELATED", "-j", "ACCEPT"])
        _run([binary, "-w", "-A", "OUTPUT", "-m", "conntrack", "--ctstate", "ESTABLISHED,RELATED", "-j", "ACCEPT"])

    # Docker's embedded resolver is local to the namespace. IPv6 has no
    # allowed upstream at all; only IPv4 source traffic is proxied.
    for proto in ("udp", "tcp"):
        _run(["iptables", "-w", "-A", "OUTPUT", "-p", proto, "-d", "127.0.0.11/32", "--dport", "53", "-j", "ACCEPT"])
    _run(["iptables", "-w", "-A", "OUTPUT", "-p", "tcp", "-d", f"{gateway}/32", "--dport", str(port), "-j", "ACCEPT"])


def _drop_capabilities() -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    # CAP_NET_ADMIN installs the policy; CHOWN, SETUID, SETGID and SETPCAP
    # are bootstrap-only and are all removed before uvicorn starts.
    for capability in (0, 12, 7, 6, 8):
        if libc.prctl(24, capability, 0, 0, 0) != 0:
            raise RuntimeError("scraper could not drop bootstrap capabilities")


def drop_to_worker() -> None:
    socket_dir = os.path.dirname(os.environ.get("SCRAPER_API_SOCKET", "/run/scraper/app.sock"))
    if os.stat(socket_dir).st_uid != WORKER_UID or os.stat(socket_dir).st_gid != WORKER_GID:
        raise RuntimeError("scraper API socket directory has unexpected owner")
    _drop_capabilities()
    os.setgroups([WORKER_GID])
    os.setgid(WORKER_GID)
    os.setuid(WORKER_UID)
    if os.geteuid() != WORKER_UID or os.getegid() != WORKER_GID:
        raise RuntimeError("scraper did not drop to its unprivileged identity")
    os.umask(0o117)


def _validate_process_status(content: str, uid: int, gid: int) -> None:
    fields = {}
    for line in content.splitlines():
        if line.startswith(("Uid:", "Gid:", "CapEff:", "CapPrm:", "CapBnd:", "NoNewPrivs:")):
            name, value = line.split(":", 1)
            fields[name] = value.split()
    if [int(value) for value in fields.get("Uid", [])] != [uid] * 4:
        raise RuntimeError("protected scraper process has an unexpected UID")
    if [int(value) for value in fields.get("Gid", [])] != [gid] * 4:
        raise RuntimeError("protected scraper process has an unexpected GID")
    if any(
        int(fields.get(name, ["-1"])[0], 16) != 0
        for name in ("CapEff", "CapPrm", "CapBnd")
    ):
        raise RuntimeError("protected scraper process retained bootstrap capabilities")
    if fields.get("NoNewPrivs") != ["1"]:
        raise RuntimeError("protected scraper process lacks no-new-privileges")


def _verify_process(pid: int) -> None:
    try:
        content = Path(f"/proc/{pid}/status").read_text(encoding="ascii")
    except OSError as exc:
        raise RuntimeError("protected scraper process status is unavailable") from exc
    _validate_process_status(content, WORKER_UID, WORKER_GID)


def main() -> None:
    if os.environ.get("SCRAPER_CAPTURE_FIREWALL_READY") == "1":
        if os.geteuid() != WORKER_UID or os.getegid() != WORKER_GID:
            raise RuntimeError("protected scraper identity is invalid")
        _verify_process(os.getpid())
        _verify_process(1)
        socket_path = Path(os.environ["SCRAPER_API_SOCKET"])
        listener = bind_private_listener(
            socket_path,
            directory_uid=WORKER_UID,
            directory_gid=WORKER_GID,
            directory_mode=0o2710,
            socket_uid=WORKER_UID,
            socket_gid=WORKER_GID,
        )
        socket_info = socket_path.lstat()
        owned_identity = (socket_info.st_dev, socket_info.st_ino)
        try:
            uvicorn.run(
                "scraper.app:app",
                fd=listener.fileno(),
                access_log=False,
            )
        finally:
            listener.close()
            try:
                current = socket_path.lstat()
                if (
                    stat.S_ISSOCK(current.st_mode)
                    and (current.st_dev, current.st_ino) == owned_identity
                ):
                    socket_path.unlink()
            except FileNotFoundError:
                pass
        return
    if os.geteuid() != 0:
        raise RuntimeError("protected scraper bootstrap must start as root")
    install_firewall()
    drop_to_worker()
    child_env = os.environ.copy()
    child_env["SCRAPER_CAPTURE_FIREWALL_READY"] = "1"
    os.execve(
        "/usr/bin/tini",
        ["/usr/bin/tini", "-s", "-g", "--", sys.executable, "-m", "scraper.capture_firewall"],
        child_env,
    )


if __name__ == "__main__":
    main()
