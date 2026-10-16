"""Shared URL utility functions for GroktoCrawl.

Consolidates urlparse-based URL handling across all services into a single,
testable module. Uses only stdlib plus socket I/O for DNS resolution.
"""

import logging
import socket
from ipaddress import (
    IPv4Address,
    IPv4Network,
    IPv6Address,
    IPv6Network,
    ip_address,
    ip_network,
)
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# ── Private/hostile network definitions (SSRF guard) ────────────────

_PRIVATE_NETWORKS: list[IPv4Network | IPv6Network] = [
    ip_network("10.0.0.0/8"),
    ip_network("172.16.0.0/12"),
    ip_network("192.168.0.0/16"),
    ip_network("127.0.0.0/8"),  # loopback
    ip_network("169.254.0.0/16"),  # link-local
    ip_network("0.0.0.0/8"),  # "this" network (RFC 1122)
    ip_network("100.64.0.0/10"),  # Carrier-grade NAT (RFC 6598)
    ip_network("198.18.0.0/15"),  # Benchmarking (RFC 2544)
    ip_network("240.0.0.0/4"),  # Reserved / future use
    ip_network("224.0.0.0/4"),  # Multicast (RFC 5771)
    ip_network("::1/128"),  # IPv6 loopback
    ip_network("fc00::/7"),  # IPv6 unique-local (ULA)
    ip_network("fe80::/10"),  # IPv6 link-local
    ip_network("ff00::/8"),  # IPv6 multicast
    ip_network("64:ff9b::/32"),  # NAT64 WKP + local-use (RFC 6052/7050)
]

_METADATA_IPS: list[IPv4Address | IPv6Address] = [
    ip_address("169.254.169.254"),  # AWS/GCP/Azure metadata
    ip_address("100.100.100.200"),  # Alibaba Cloud metadata
    ip_address("fd00:ec2::254"),  # AWS IMDSv2 IPv6
]

_PRIVATE_HOSTNAME_SUFFIXES: list[str] = [
    ".docker.internal",
]

# IPv6 networks that embed an IPv4 destination (RFC 4291, RFC 3056, RFC 4380)
_IPV4_COMPATIBLE_NET = ip_network("::/96")  # deprecated IPv4-compatible
_6TO4_NET = ip_network("2002::/16")  # 6to4 relay prefix
_TEREDO_NET = ip_network("2001::/32")  # Teredo service prefix


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
        host = f"[{hostname}]" if ":" in hostname else hostname
        return f"{parsed.scheme}://{host}{port}"
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


def _unmap_embedded_ipv4(
    addr: IPv4Address | IPv6Address,
) -> IPv4Address | IPv6Address:
    """Unwrap IPv6 forms that embed an IPv4 destination to their IPv4 address.

    Handles the encapsulation forms that carry an IPv4 destination and
    can reach it with the right routing, so the embedded address is
    checked against IPv4 private ranges instead of passing as a
    non-private IPv6 literal:

    * ``::ffff:a.b.c.d`` — IPv4-mapped (RFC 4291 section 2.2)
    * ``::a.b.c.d`` — deprecated IPv4-compatible (RFC 4291 section 2.5.5.1)
    * ``2002:a.b.c.d::/48`` — 6to4 (RFC 3056)
    * ``2001:0000::/32`` — Teredo, de-obfuscated client IPv4 (RFC 4380)

    Without unmapping, e.g. ``::ffff:127.0.0.1`` or ``2002:0a00:0001::``
    matches no IPv6 private network and the SSRF guard is bypassed.
    NAT64 addresses (``64:ff9b::/32``) are rejected outright as a
    special-purpose range in :data:`_PRIVATE_NETWORKS`; no extraction is
    needed for them.
    """
    if not isinstance(addr, IPv6Address):
        return addr
    if addr.ipv4_mapped is not None:
        return addr.ipv4_mapped
    int_addr = int(addr)
    if addr in _IPV4_COMPATIBLE_NET:
        return IPv4Address(int_addr & 0xFFFFFFFF)
    if addr in _6TO4_NET:
        # IPv4 occupies hextets 2-3 (int bits 80-111)
        return IPv4Address((int_addr >> 80) & 0xFFFFFFFF)
    if addr in _TEREDO_NET:
        # Destination is the Teredo client IPv4 (low 32 bits, XOR-obscured)
        return IPv4Address((int_addr & 0xFFFFFFFF) ^ 0xFFFFFFFF)
    return addr


def _resolve_to_ips(hostname: str) -> list[IPv4Address | IPv6Address]:
    """Resolve a hostname to all IP addresses (IPv4 and IPv6).

    Addresses are returned raw; :func:`_addr_is_restricted` handles
    embedded-IPv4 forms when checked.
    """
    ips, _transient = _resolve_to_ips_with_transient(hostname)
    return ips


def _resolve_to_ips_with_transient(
    hostname: str,
) -> tuple[list[IPv4Address | IPv6Address], bool]:
    """Resolve a hostname, reporting whether the failure was transient.

    Returns ``(ips, transient)``. ``transient`` is True when DNS failed
    with a temporary resolver error (``EAI_AGAIN``) that may succeed on
    retry, so callers can distinguish retryable failures from permanent
    ones (NXDOMAIN, misconfigured resolver, etc.).

    Addresses are returned raw; :func:`_addr_is_restricted` handles
    IPv4-embedded forms when checked.
    """
    try:
        addrinfo = socket.getaddrinfo(hostname, None)
        ips: set[IPv4Address | IPv6Address] = set()
        for _family, _stype, _proto, _canonname, sockaddr in addrinfo:
            try:
                ips.add(ip_address(sockaddr[0]))
            except ValueError:
                continue
        return list(ips), False
    except socket.gaierror as exc:
        logger.warning("DNS resolution failed for %s — treating as private", hostname)
        return [], exc.errno == getattr(socket, "EAI_AGAIN", -3)
    except UnicodeError:
        # Non-IDNA hostnames (e.g. >63-char labels) are permanently
        # unresolvable, not transiently failing.
        logger.warning("DNS resolution failed for %s — treating as private", hostname)
        return [], False


def _addr_is_restricted(addr: IPv4Address | IPv6Address) -> bool:
    """Return True when an IP address is private or otherwise restricted.

    Checks the raw address first (covers special-purpose ranges like the
    NAT64 ``64:ff9b::/32`` block and multicast), then unwraps IPv6 forms
    that embed an IPv4 destination and checks the embedded address
    against the same ranges.
    """
    for net in _PRIVATE_NETWORKS:
        if addr in net:
            return True
    if addr in _METADATA_IPS:
        return True
    unmapped = _unmap_embedded_ipv4(addr)
    if unmapped is not addr:
        for net in _PRIVATE_NETWORKS:
            if unmapped in net:
                return True
        if unmapped in _METADATA_IPS:
            return True
    return False


def is_private_host_literal(hostname: str) -> bool | None:
    """Classify a hostname without DNS, returning ``None`` when lookup is needed.

    The result preserves the same literal-IP and internal-suffix policy used
    by :func:`is_private_host`. A caller with a trusted DNS policy service can
    resolve the remaining hostname without weakening this guard.
    """
    if not hostname:
        return True
    hostname_lower = hostname.lower()
    if any(hostname_lower.endswith(suffix) for suffix in _PRIVATE_HOSTNAME_SUFFIXES):
        return True
    try:
        return _addr_is_restricted(ip_address(hostname))
    except ValueError:
        return None


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

    # Apply deterministic hostname/IP checks before any DNS lookup.
    literal_policy = is_private_host_literal(hostname)
    if literal_policy is not None:
        return literal_policy

    # Resolve hostname to IPs and check each
    ips = _resolve_to_ips(hostname)
    if not ips:
        # Can't resolve — _resolve_to_ips already logged at WARNING
        return True

    return any(_addr_is_restricted(addr) for addr in ips)


class WebhookDestinationValidationError(ValueError):
    """A webhook destination was permanently rejected by the SSRF guard."""


class WebhookDestinationDNSRetryableError(WebhookDestinationValidationError):
    """Webhook destination validation failed due to a transient DNS error.

    Raised when the hostname could not be resolved because of a
    temporary resolver failure (``EAI_AGAIN``). Callers should retry
    rather than treat the destination as permanently rejected.
    """


def validate_outbound_webhook_url(url: str) -> None:
    """Validate a user-supplied webhook destination before delivery.

    Enforces the shared SSRF policy for outbound webhook POSTs: only
    explicit ``http``/``https`` schemes are allowed, and the host must
    resolve to a public, non-restricted address.

    This function never raises exceptions other than the documented
    contract below; malformed URLs (bad IPv6 brackets, non-IDNA
    hostnames, etc.) are treated as permanent rejections rather than
    leaking parser or resolver errors to callers.

    Raises:
        WebhookDestinationValidationError: If the URL is missing, uses a
            non-HTTP(S) scheme, has no hostname, or its host is
            private/restricted — a permanent rejection.
        WebhookDestinationDNSRetryableError: If the hostname could not
            be resolved due to a transient DNS failure (``EAI_AGAIN``);
            a subclass of ``WebhookDestinationValidationError`` so
            callers may retry or treat it as a rejection.

    DNS resolution and host checks use the same private/hostile network
    definitions as :func:`is_private_host` (RFC 1918, loopback,
    link-local, multicast, metadata endpoints, Docker internal
    hostnames), and unresolvable hostnames fail closed.
    """
    try:
        _validate_outbound_webhook_url_inner(url)
    except WebhookDestinationValidationError:
        raise
    except Exception as exc:  # pragma: no cover - defense in depth
        raise WebhookDestinationValidationError(
            f"webhook URL could not be validated: {exc}"
        ) from exc


def _validate_outbound_webhook_url_inner(url: str) -> None:
    """Implementation of :func:`validate_outbound_webhook_url`.

    Raises ``WebhookDestinationValidationError`` /
    ``WebhookDestinationDNSRetryableError`` for every rejection; the
    public wrapper converts any unexpected exception to the permanent
    rejection contract.
    """
    if not isinstance(url, str) or not url.strip():
        raise WebhookDestinationValidationError("webhook URL is missing")

    parsed = urlparse(url)
    if parsed.scheme.lower() not in ("http", "https"):
        raise WebhookDestinationValidationError(
            f"webhook URL must use http or https, got {parsed.scheme!r}"
        )

    try:
        hostname = parsed.hostname
    except ValueError:
        raise WebhookDestinationValidationError(
            "webhook URL has an invalid host"
        ) from None
    if not hostname:
        raise WebhookDestinationValidationError("webhook URL has no hostname")

    hostname_lower = hostname.lower()
    for suffix in _PRIVATE_HOSTNAME_SUFFIXES:
        if hostname_lower.endswith(suffix):
            raise WebhookDestinationValidationError(
                "webhook URL resolves to a private or restricted destination"
            )

    # IP literal (deterministic — includes IPv4-embedding IPv6 literals)
    try:
        addr = ip_address(hostname)
    except ValueError:
        addr = None
    if addr is not None:
        if _addr_is_restricted(addr):
            raise WebhookDestinationValidationError(
                "webhook URL resolves to a private or restricted destination"
            )
        return

    # Hostname: resolve and check every address; transient DNS failures
    # are retryable rather than permanent rejections.
    ips, transient = _resolve_to_ips_with_transient(hostname)
    if transient:
        raise WebhookDestinationDNSRetryableError(
            "webhook URL DNS resolution temporarily failed"
        )
    if not ips or any(_addr_is_restricted(addr) for addr in ips):
        raise WebhookDestinationValidationError(
            "webhook URL resolves to a private or restricted destination"
        )
