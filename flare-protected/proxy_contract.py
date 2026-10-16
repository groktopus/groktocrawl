"""Validate the single private proxy peer used by protected FlareSolverr."""

from __future__ import annotations

import ipaddress
import os
import sys
from urllib.parse import urlsplit

DEFAULT_GATEWAY = "172.31.254.18"
DEFAULT_SUBNET = "172.31.254.16/29"


def configured_gateway(
    gateway_raw: str, proxy_raw: str, subnet_raw: str = DEFAULT_SUBNET
) -> str:
    """Require a private usable IPv4 and a proxy URL pinned to that peer."""
    try:
        gateway = ipaddress.IPv4Address(gateway_raw)
        subnet = ipaddress.IPv4Network(subnet_raw, strict=True)
        proxy = urlsplit(proxy_raw)
        port = proxy.port
    except ValueError as exc:
        raise ValueError("protected capture proxy configuration is invalid") from exc
    if (
        not any(
            gateway in ipaddress.ip_network(block)
            for block in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
        )
        or not any(
            subnet.subnet_of(ipaddress.ip_network(block))
            for block in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
        )
        or gateway not in subnet
        or gateway in (subnet.network_address, subnet.broadcast_address)
        or proxy.scheme != "http"
        or proxy.hostname != str(gateway)
        or port != 8080
        or proxy.username is not None
        or proxy.password is not None
        or proxy.path not in ("", "/")
        or proxy.query
        or proxy.fragment
        or proxy_raw != f"http://{gateway}:8080"
    ):
        raise ValueError("protected capture proxy configuration is invalid")
    return str(gateway)


def main() -> None:
    gateway_raw = os.environ.get("FLARE_CAPTURE_GATEWAY_IP", DEFAULT_GATEWAY)
    subnet_raw = os.environ.get("FLARE_CAPTURE_GATEWAY_SUBNET", DEFAULT_SUBNET)
    proxy_raw = os.environ.get("FLARE_CAPTURE_PROXY_URL", f"http://{gateway_raw}:8080")
    try:
        sys.stdout.write(configured_gateway(gateway_raw, proxy_raw, subnet_raw) + "\n")
    except ValueError:
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
