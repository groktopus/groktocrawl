"""Shared URL utility functions for GroktoCrawl.

Consolidates urlparse-based URL handling across all services into a single,
testable module. All functions are pure (no I/O, no external dependencies).
"""

import socket
from ipaddress import ip_address, ip_network
from urllib.parse import urlparse

# ── Private/hostile network definitions (SSRF guard) ────────────────

_PRIVATE_NETWORKS: list[ip_network] = [
    ip_network("10.0.0.0/8"),
    ip_network("172.16.0.0/12"),
    ip_network("192.168.0.0/16"),
    ip_network("127.0.0.0/8"),  # loopback
    ip_network("169.254.0.0/16"),  # link-local
    ip_network("::1/128"),  # IPv6 loopback
    ip_network("fc00::/7"),  # IPv6 unique-local (ULA)
    ip_network("fe80::/10"),  # IPv6 link-local
]

_METADATA_IPS: list[ip_address] = [
    ip_address("169.254.169.254"),  # AWS/GCP/Azure metadata
    ip_address("100.100.100.200"),  # Alibaba Cloud metadata
    ip_address("fd00:ec2::254"),  # AWS IMDSv2 IPv6
]

_PRIVATE_HOSTNAME_SUFFIXES: list[str] = [
    ".docker.internal",
]


# ── Public API ─────────────────────────────────────────────────────


def normalize_url(url: str) -> str:
    """Normalize a URL for consistent cache keying.

    Lowercases scheme and hostname, strips trailing slash from path
    (preserving root '/'), and sorts query parameters alphabetically.
    """
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    path = parsed.path.lower().rstrip("/") if parsed.path.lower() != "/" else "/"
    query = parsed.query
    fragment = parsed.fragment
    # Sort query parameters for consistency
    if query:
        params = sorted(query.lower().split("&"))
        query = "&".join(params)
    normalized = f"{scheme}://{netloc}{path}"
    if query:
        normalized += f"?{query}"
    if fragment:
        normalized += f"#{fragment}"
    return normalized


def extract_domain(url: str, include_scheme: bool = False) -> str:
    """Extract the hostname/netloc from a URL.

    Args:
        url: The URL to parse.
        include_scheme: When True, returns ``scheme://hostname``
            instead of just ``hostname``.

    Returns:
        The hostname (with port if non-default) or ``""`` for empty/invalid URLs.
    """
    parsed = urlparse(url)
    hostname = parsed.hostname or ""
    if not hostname:
        return ""
    if include_scheme:
        port = f":{parsed.port}" if parsed.port is not None else ""
        return f"{parsed.scheme}://{hostname}{port}"
    return hostname


def is_same_origin(url1: str, url2: str) -> bool:
    """Check whether two URLs share the same scheme and host.

    Comparison is case-insensitive. Port is included when explicitly present.
    """
    p1 = urlparse(url1)
    p2 = urlparse(url2)
    return (
        p1.scheme.lower() == p2.scheme.lower()
        and p1.netloc.lower() == p2.netloc.lower()
    )


def _resolve_to_ips(hostname: str) -> list[ip_address]:
    """Resolve a hostname to all IP addresses (IPv4 and IPv6)."""
    try:
        addrinfo = socket.getaddrinfo(hostname, None)
        ips: set[ip_address] = set()
        for _family, _stype, _proto, _canonname, sockaddr in addrinfo:
            try:
                ips.add(ip_address(sockaddr[0]))
            except ValueError:
                continue
        return list(ips)
    except socket.gaierror:
        return []


def is_private_host(url: str) -> bool:
    """Check if a URL's hostname resolves to a private or internal IP.

    Covers RFC 1918 private ranges, RFC 4193 unique-local IPv6,
    link-local addresses, loopback, cloud metadata endpoints, and
    Docker internal hostnames.

    Returns ``True`` when the host is private, internal, or
    otherwise unsafe to navigate to (SSRF guard).
    """
    parsed = urlparse(url)
    hostname = parsed.hostname or ""

    # Reject empty/relative URLs
    if not hostname:
        return True

    # Check hostname suffixes for internal Docker resolution
    hostname_lower = hostname.lower()
    for suffix in _PRIVATE_HOSTNAME_SUFFIXES:
        if hostname_lower.endswith(suffix):
            return True

    # Check if hostname is itself a private IP literal
    try:
        addr = ip_address(hostname)
        for net in _PRIVATE_NETWORKS:
            if addr in net:
                return True
        if addr in _METADATA_IPS:
            return True
        # It's a valid, non-private IP literal — safe to navigate
        return False
    except ValueError:
        pass  # Not an IP literal, treat as hostname

    # Resolve hostname to IPs and check each
    ips = _resolve_to_ips(hostname)
    if not ips:
        # Can't resolve — log and reject (DNS rebinding risk)
        return True

    for addr in ips:
        for net in _PRIVATE_NETWORKS:
            if addr in net:
                return True
        if addr in _METADATA_IPS:
            return True

    return False
