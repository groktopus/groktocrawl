"""Host-bridge sentinel target validation and positive reachability probe."""

from __future__ import annotations

import ipaddress
import os
from urllib.request import HTTPRedirectHandler, ProxyHandler, build_opener
from uuid import uuid4

POSITIVE_CONTROL_PREFIX = b"PRIVATE_SENTINEL_RESPONSE_V1:"
HOST_BRIDGE_PORT = 19001
MIN_DYNAMIC_ADDRESSES = 2


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def validate_capture_address_plan(
    subnet: str | None,
    gateway: str | None,
    dynamic_range: str | None,
    egress_ip: str | None,
    peer_ip: str | None,
) -> str:
    """Validate the full bridge IPAM plan before any host or denial probe."""
    if not all((subnet, gateway, dynamic_range, egress_ip, peer_ip)):
        raise RuntimeError("capture bridge address plan is incomplete")
    try:
        network = ipaddress.ip_network(subnet, strict=True)
        pool = ipaddress.ip_network(dynamic_range, strict=True)
        addresses = tuple(ipaddress.ip_address(value) for value in (gateway, egress_ip, peer_ip))
    except ValueError as error:
        raise RuntimeError("capture bridge address plan is invalid") from error
    if (
        not isinstance(network, ipaddress.IPv4Network)
        or not isinstance(pool, ipaddress.IPv4Network)
        or any(not isinstance(address, ipaddress.IPv4Address) for address in addresses)
    ):
        raise RuntimeError("capture bridge requires IPv4 addresses")
    gateway_ip = addresses[0]

    def usable(address: ipaddress.IPv4Address) -> bool:
        return address in network and address not in {
            network.network_address,
            network.broadcast_address,
        }

    reserved_in_pool = sum(address in pool for address in addresses)
    parent_network_reserved = sum(
        address in pool
        for address in (network.network_address, network.broadcast_address)
    )
    available_dynamic = pool.num_addresses - reserved_in_pool - parent_network_reserved
    if (
        not pool.subnet_of(network)
        or not network.is_private
        or network.is_loopback
        or network.is_link_local
        or network.is_multicast
        or network.is_reserved
        or available_dynamic < MIN_DYNAMIC_ADDRESSES
        or any(not usable(address) for address in addresses)
        or len(set(addresses)) != len(addresses)
        or any(address in pool for address in addresses[1:])
        or any(
            not address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_multicast
            or address.is_unspecified
            or address.is_reserved
            for address in addresses
        )
    ):
        raise RuntimeError("capture bridge address plan overlaps or is out of range")
    if (
        not gateway_ip.is_private
        or gateway_ip.is_loopback
        or gateway_ip.is_link_local
        or gateway_ip.is_multicast
        or gateway_ip.is_unspecified
        or gateway_ip.is_reserved
    ):
        raise RuntimeError("capture bridge gateway must be a private IPv4 address")
    return str(gateway_ip)


def validate_sentinel_target(
    subnet: str | None,
    expected_address: str | None,
    bind_address: str | None,
    probe_target: str | None,
    reserved_addresses: tuple[str, ...] = (),
) -> str:
    """Require a sentinel address and probe target to match a private subnet pin."""
    if not subnet or not expected_address or not bind_address or not probe_target:
        raise RuntimeError("sentinel target configuration is incomplete")
    try:
        network = ipaddress.ip_network(subnet, strict=True)
        expected_ip = ipaddress.ip_address(expected_address)
        bind_ip = ipaddress.ip_address(bind_address)
        probe_ip = ipaddress.ip_address(probe_target)
        reserved_ips = tuple(ipaddress.ip_address(value) for value in reserved_addresses)
    except ValueError as error:
        raise RuntimeError("sentinel target configuration is invalid") from error
    if (
        not isinstance(network, ipaddress.IPv4Network)
        or not isinstance(expected_ip, ipaddress.IPv4Address)
        or not isinstance(bind_ip, ipaddress.IPv4Address)
        or not isinstance(probe_ip, ipaddress.IPv4Address)
        or not expected_ip.is_private
        or expected_ip.is_loopback
        or expected_ip.is_link_local
        or expected_ip.is_multicast
        or expected_ip.is_unspecified
        or expected_ip.is_reserved
        or expected_ip not in network
        or expected_ip in {network.network_address, network.broadcast_address}
        or any(
            not isinstance(address, ipaddress.IPv4Address)
            or address not in network
            or address in {network.network_address, network.broadcast_address}
            for address in reserved_ips
        )
        or bind_ip != expected_ip
        or probe_ip != expected_ip
        or expected_ip in reserved_ips
    ):
        raise RuntimeError("sentinel target does not match its private IPv4 address")
    return str(expected_ip)


def validate_host_bridge_target(
    subnet: str | None,
    expected_gateway: str | None,
    bind_address: str | None,
    probe_target: str | None,
    reserved_addresses: tuple[str, ...] = (),
) -> str:
    """Require the host-bridge bind and probe target to match its IPAM gateway."""
    return validate_sentinel_target(
        subnet, expected_gateway, bind_address, probe_target, reserved_addresses
    )


def configured_host_bridge_target(environ: dict[str, str] | None = None) -> str:
    values = os.environ if environ is None else environ
    target = validate_capture_address_plan(
        values.get("CAPTURE_HOST_BRIDGE_SUBNET"),
        values.get("CAPTURE_HOST_BRIDGE_IP"),
        values.get("CAPTURE_HOST_BRIDGE_IP_RANGE"),
        values.get("CAPTURE_EGRESS_IP"),
        values.get("CAPTURE_PEER_IP"),
    )
    return validate_host_bridge_target(
        values.get("CAPTURE_HOST_BRIDGE_SUBNET"),
        target,
        target,
        target,
        (values.get("CAPTURE_EGRESS_IP", ""), values.get("CAPTURE_PEER_IP", "")),
    )


def verify_host_bridge_positive_control(
    target: str,
    *,
    opener=None,
    timeout: float = 2.0,
) -> str:
    """Make one nonce-bound positive request; a timeout or unrelated peer fails."""
    nonce = uuid4().hex
    expected_body = POSITIVE_CONTROL_PREFIX + nonce.encode("ascii")
    request_opener = opener or build_opener(ProxyHandler({}), _NoRedirectHandler()).open
    with request_opener(
        f"http://{target}:{HOST_BRIDGE_PORT}/positive-control/{nonce}", timeout=timeout
    ) as response:
        body = response.read(len(expected_body) + 1)
        if (
            response.status != 200
            or response.headers.get("Content-Type") != "text/plain"
            or response.headers.get("Access-Control-Allow-Origin") != "*"
            or response.headers.get("Content-Length") != str(len(expected_body))
            or body != expected_body
        ):
            raise RuntimeError("host bridge sentinel positive control was invalid")
    return nonce


def main() -> None:
    target = configured_host_bridge_target()
    nonce = verify_host_bridge_positive_control(target)
    print(nonce)


if __name__ == "__main__":
    main()
