"""Resolve and connect to a source destination without a second DNS lookup.

This is a low-level primitive for a future policy egress gateway. It does not
implement HTTP, redirects, connection pooling, or browser isolation.
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
Resolver = Callable[[str, int], Awaitable[Sequence[str | IPAddress]]]
Dialer = Callable[[IPAddress, int], Awaitable["ConnectedSocket"]]

_HOST_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$", re.IGNORECASE)
_NUMERIC_HOST = re.compile(r"^[0-9.]+$")


class DestinationError(Exception):
    """Base class whose message is safe to expose to callers."""

    code = "destination-error"

    def __init__(self) -> None:
        super().__init__(self.code)


class DestinationDeniedError(DestinationError):
    """The authority or resolved destination violates policy."""

    code = "destination-denied"


class DestinationResolutionError(DestinationError):
    """Resolution failed or yielded no usable address."""

    code = "destination-resolution-failed"


class DestinationConnectionError(DestinationError):
    """The vetted peer could not be reached or did not match the dial target."""

    code = "destination-connect-failed"


class DestinationTimeoutError(DestinationError):
    """Resolution or connection exceeded the configured bound."""

    code = "destination-timeout"


class ConnectedSocket(Protocol):
    """Minimal connected-socket interface used by the dialer and tests."""

    def getpeername(self) -> tuple[object, ...]:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError


@dataclass(frozen=True)
class DestinationAuthority:
    host: str
    port: int
    literal: IPAddress | None = None


@dataclass
class BoundConnection:
    """A connected socket with the exact vetted peer recorded."""

    authority: DestinationAuthority
    address: IPAddress
    socket: ConnectedSocket

    def close(self) -> None:
        self.socket.close()


def parse_authority(authority: str) -> DestinationAuthority:
    """Parse a strict ``host:port`` or ``[IPv6]:port`` authority."""
    if (
        not isinstance(authority, str)
        or not authority
        or authority != authority.strip()
    ):
        raise DestinationDeniedError()
    if any(ord(char) < 0x21 or ord(char) == 0x7F for char in authority):
        raise DestinationDeniedError()
    if any(char in authority for char in "/\\?#@%"):
        raise DestinationDeniedError()

    literal: IPAddress | None = None
    if authority.startswith("["):
        closing = authority.find("]")
        if closing <= 1 or authority[closing + 1 : closing + 2] != ":":
            raise DestinationDeniedError()
        raw_host = authority[1:closing]
        raw_port = authority[closing + 2 :]
        try:
            literal = ipaddress.ip_address(raw_host)
        except ValueError as exc:
            raise DestinationDeniedError() from exc
        if not isinstance(literal, ipaddress.IPv6Address):
            raise DestinationDeniedError()
        host = literal.compressed
    else:
        if authority.count(":") != 1:
            raise DestinationDeniedError()
        raw_host, raw_port = authority.rsplit(":", 1)
        if not raw_host:
            raise DestinationDeniedError()
        try:
            literal = ipaddress.ip_address(raw_host)
        except ValueError:
            if _NUMERIC_HOST.fullmatch(raw_host):
                raise DestinationDeniedError()
            try:
                host = raw_host.removesuffix(".").encode("idna").decode("ascii").lower()
            except UnicodeError as exc:
                raise DestinationDeniedError() from exc
            if (
                not host
                or len(host) > 253
                or any(not _HOST_LABEL.fullmatch(label) for label in host.split("."))
            ):
                raise DestinationDeniedError()
        else:
            host = literal.compressed

    if (
        not raw_port
        or len(raw_port) > 5
        or not raw_port.isascii()
        or not raw_port.isdecimal()
    ):
        raise DestinationDeniedError()
    # Reject ambiguous leading-zero forms rather than letting downstream
    # clients interpret them differently.
    if len(raw_port) > 1 and raw_port.startswith("0"):
        raise DestinationDeniedError()
    port = int(raw_port)
    if not 1 <= port <= 65535:
        raise DestinationDeniedError()
    return DestinationAuthority(host=host, port=port, literal=literal)


def _canonical_vetted_address(value: str | IPAddress) -> IPAddress:
    try:
        address = (
            value
            if isinstance(value, (ipaddress.IPv4Address, ipaddress.IPv6Address))
            else ipaddress.ip_address(value)
        )
    except (ValueError, TypeError) as exc:
        raise DestinationResolutionError() from exc

    if isinstance(address, ipaddress.IPv6Address):
        if address.scope_id is not None:
            raise DestinationDeniedError()
        if address.ipv4_mapped is not None:
            address = address.ipv4_mapped
        elif address.sixtofour is not None or address.teredo is not None:
            raise DestinationDeniedError()
        elif address in ipaddress.ip_network(
            "::/96"
        ) or address in ipaddress.ip_network("64:ff9b::/32"):
            raise DestinationDeniedError()
    if (
        not address.is_global
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
        or address.is_loopback
        or address.is_link_local
        or address.is_private
        or (isinstance(address, ipaddress.IPv6Address) and address.is_site_local)
    ):
        raise DestinationDeniedError()
    return address


async def _system_resolver(host: str, port: int) -> Sequence[str]:
    loop = asyncio.get_running_loop()
    try:
        records = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError) as exc:
        raise DestinationResolutionError() from exc
    addresses: list[str] = []
    for record in records:
        sockaddr = record[4]
        if (
            record[0] not in (socket.AF_INET, socket.AF_INET6)
            or not isinstance(sockaddr, tuple)
            or not sockaddr
            or not isinstance(sockaddr[0], str)
        ):
            raise DestinationResolutionError()
        addresses.append(sockaddr[0])
    return addresses


async def _numeric_dialer(address: IPAddress, port: int) -> socket.socket:
    family = (
        socket.AF_INET6
        if isinstance(address, ipaddress.IPv6Address)
        else socket.AF_INET
    )
    sock = socket.socket(family, socket.SOCK_STREAM)
    sock.setblocking(False)
    try:
        loop = asyncio.get_running_loop()
        target: tuple[object, ...]
        if family == socket.AF_INET6:
            target = (str(address), port, 0, 0)
        else:
            target = (str(address), port)
        await loop.sock_connect(sock, target)  # numeric address: no hostname lookup
        return sock
    except BaseException:
        sock.close()
        raise


async def resolve_and_connect(
    authority: str,
    *,
    resolver: Resolver = _system_resolver,
    dialer: Dialer = _numeric_dialer,
    timeout: float = 2.0,
) -> BoundConnection:
    """Resolve once, validate every answer, and dial a vetted numeric peer.

    The timeout covers resolution, policy evaluation, and connection setup.
    Injected resolver/dialer seams make tests deterministic and offline.
    """
    if (
        not isinstance(timeout, (int, float))
        or isinstance(timeout, bool)
        or not 0 < timeout <= 30
    ):
        raise ValueError("timeout must be > 0 and <= 30 seconds")
    try:
        async with asyncio.timeout(timeout):
            parsed, addresses = await resolve_and_validate(
                authority, resolver=resolver, timeout=timeout
            )
            connected = await dialer(addresses[0], parsed.port)
    except asyncio.CancelledError:
        raise
    except DestinationError:
        raise
    except TimeoutError as exc:
        raise DestinationTimeoutError() from exc
    except Exception as exc:
        raise DestinationConnectionError() from exc

    try:
        peer = connected.getpeername()
        peer_ip = ipaddress.ip_address(str(peer[0]))
        if isinstance(peer_ip, ipaddress.IPv6Address) and peer_ip.ipv4_mapped:
            peer_ip = peer_ip.ipv4_mapped
        if peer_ip != addresses[0] or len(peer) < 2 or peer[1] != parsed.port:
            raise DestinationConnectionError()
        return BoundConnection(parsed, addresses[0], connected)
    except asyncio.CancelledError:
        connected.close()
        raise
    except Exception as exc:
        connected.close()
        if isinstance(exc, DestinationConnectionError):
            raise
        raise DestinationConnectionError() from exc
    except BaseException:
        connected.close()
        raise


async def resolve_and_validate(
    authority: str,
    *,
    resolver: Resolver = _system_resolver,
    timeout: float = 2.0,
) -> tuple[DestinationAuthority, list[IPAddress]]:
    """Resolve an authority and vet every answer without opening a socket.

    This is suitable for a policy preflight oracle. The eventual proxy request
    must still call :func:`resolve_and_connect`, which repeats resolution and
    binds the real connection to a vetted numeric peer.
    """
    if (
        not isinstance(timeout, (int, float))
        or isinstance(timeout, bool)
        or not 0 < timeout <= 30
    ):
        raise ValueError("timeout must be > 0 and <= 30 seconds")
    parsed = parse_authority(authority)
    try:
        async with asyncio.timeout(timeout):
            if parsed.literal is not None:
                answers: Sequence[str | IPAddress] = [parsed.literal]
            else:
                try:
                    answers = await resolver(parsed.host, parsed.port)
                except asyncio.CancelledError:
                    raise
                except DestinationError:
                    raise
                except Exception as exc:
                    raise DestinationResolutionError() from exc
            if not answers:
                raise DestinationResolutionError()

            vetted = [_canonical_vetted_address(answer) for answer in answers]
            # Deduplicate while preserving resolver order. Every answer must
            # pass policy before any address is attempted.
            unique = list(dict.fromkeys(vetted))
            return parsed, unique
    except TimeoutError as exc:
        raise DestinationTimeoutError() from exc


async def resolve_and_connect_model_target(
    authority: str,
    *,
    private_host_grants: frozenset[str] = frozenset(),
    resolver: Resolver = _system_resolver,
    dialer: Dialer = _numeric_dialer,
    timeout: float = 2.0,
) -> BoundConnection:
    """Resolve and numerically dial one model-only authority.

    This is deliberately separate from ``resolve_and_connect``: source
    destinations remain globally-routable-only. Private answers are accepted
    only for a hostname explicitly granted by the operator, and only inside
    RFC1918/ULA ranges. The caller must separately constrain the authority to
    its fixed model-target inventory.
    """
    if (
        not isinstance(timeout, (int, float))
        or isinstance(timeout, bool)
        or not 0 < timeout <= 30
    ):
        raise ValueError("timeout must be > 0 and <= 30 seconds")
    if not isinstance(private_host_grants, (set, frozenset)) or any(
        not isinstance(host, str) for host in private_host_grants
    ):
        raise ValueError("private_host_grants must be a set of hostnames")
    grants = frozenset(host.lower().rstrip(".") for host in private_host_grants)
    parsed = parse_authority(authority)
    private_granted = parsed.host.lower().rstrip(".") in grants
    try:
        async with asyncio.timeout(timeout):
            if parsed.literal is not None:
                answers: Sequence[str | IPAddress] = [parsed.literal]
            else:
                try:
                    answers = await resolver(parsed.host, parsed.port)
                except asyncio.CancelledError:
                    raise
                except DestinationError:
                    raise
                except Exception as exc:
                    raise DestinationResolutionError() from exc
            if not answers:
                raise DestinationResolutionError()

            vetted: list[IPAddress] = []
            answer_scope: str | None = None
            private_ranges = (
                ipaddress.ip_network("10.0.0.0/8"),
                ipaddress.ip_network("172.16.0.0/12"),
                ipaddress.ip_network("192.168.0.0/16"),
                ipaddress.ip_network("fc00::/7"),
            )
            for answer in answers:
                try:
                    address = (
                        answer
                        if isinstance(
                            answer, (ipaddress.IPv4Address, ipaddress.IPv6Address)
                        )
                        else ipaddress.ip_address(answer)
                    )
                except (ValueError, TypeError) as exc:
                    raise DestinationResolutionError() from exc
                if isinstance(address, ipaddress.IPv6Address):
                    if address.scope_id is not None:
                        raise DestinationDeniedError()
                    if address.ipv4_mapped is not None:
                        address = address.ipv4_mapped
                    elif address.sixtofour is not None or address.teredo is not None:
                        raise DestinationDeniedError()
                if (
                    address.is_multicast
                    or address.is_unspecified
                    or address.is_loopback
                    or address.is_link_local
                    or address.is_reserved
                ):
                    raise DestinationDeniedError()
                if address.is_global:
                    if answer_scope == "private":
                        raise DestinationDeniedError()
                    answer_scope = "global"
                    vetted.append(address)
                    continue
                if private_granted and any(
                    address in network for network in private_ranges
                ):
                    if answer_scope == "global":
                        raise DestinationDeniedError()
                    answer_scope = "private"
                    vetted.append(address)
                    continue
                raise DestinationDeniedError()

            selected = next(iter(dict.fromkeys(vetted)))
            try:
                connected = await dialer(selected, parsed.port)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                raise DestinationConnectionError() from exc
            try:
                peer = connected.getpeername()
                peer_ip = ipaddress.ip_address(str(peer[0]))
                if isinstance(peer_ip, ipaddress.IPv6Address) and peer_ip.ipv4_mapped:
                    peer_ip = peer_ip.ipv4_mapped
                if peer_ip != selected or len(peer) < 2 or peer[1] != parsed.port:
                    raise DestinationConnectionError()
                return BoundConnection(parsed, selected, connected)
            except BaseException as exc:
                connected.close()
                if isinstance(exc, asyncio.CancelledError):
                    raise
                if isinstance(exc, DestinationError):
                    raise
                raise DestinationConnectionError() from exc
    except TimeoutError as exc:
        raise DestinationTimeoutError() from exc
