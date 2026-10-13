import asyncio
import ipaddress
import socket

import pytest

from common import capture_destination
from common.capture_destination import (
    DestinationConnectionError,
    DestinationDeniedError,
    DestinationResolutionError,
    DestinationTimeoutError,
    resolve_and_connect,
    resolve_and_connect_model_target,
    resolve_and_validate,
)


class FakeSocket:
    def __init__(self, peer: str) -> None:
        self.peer = peer
        self.closed = False

    def getpeername(self) -> tuple[object, ...]:
        return self.peer, 443

    def close(self) -> None:
        self.closed = True


class BrokenPeerSocket(FakeSocket):
    def __init__(self, failure: str) -> None:
        super().__init__("93.184.216.34")
        self.failure = failure

    def getpeername(self) -> tuple[object, ...]:
        if self.failure == "raises":
            raise OSError("raw peer detail")
        if self.failure == "empty":
            return ()
        if self.failure == "invalid-ip":
            return ("not-an-ip", 443)
        if self.failure == "wrong-port":
            return ("93.184.216.34", 444)
        return ("93.184.216.34",)


class FakeOwnedSocket:
    def __init__(self, family: int, socktype: int) -> None:
        self.family = family
        self.socktype = socktype
        self.blocking: bool | None = None
        self.peer: tuple[object, ...] = ()
        self.closed = False

    def setblocking(self, blocking: bool) -> None:
        self.blocking = blocking

    def getpeername(self) -> tuple[object, ...]:
        return self.peer

    def close(self) -> None:
        self.closed = True


def _addrinfo(host: str, family: int, sockaddr: tuple[object, ...]) -> tuple:
    return (family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", sockaddr)


@pytest.mark.asyncio
async def test_resolves_once_and_dials_selected_numeric_address() -> None:
    calls: list[tuple[str, int]] = []
    dials: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    sock = FakeSocket("93.184.216.34")

    async def resolver(host: str, port: int) -> list[str]:
        calls.append((host, port))
        # The resolver's later state is deliberately different; the dialer
        # receives the vetted numeric address, never this hostname.
        return ["93.184.216.34", "2606:4700:4700::1111"]

    async def dialer(
        address: ipaddress.IPv4Address | ipaddress.IPv6Address, port: int
    ) -> FakeSocket:
        assert port == 443
        dials.append(address)
        return sock

    bound = await resolve_and_connect(
        "example.com:443", resolver=resolver, dialer=dialer
    )

    assert calls == [("example.com", 443)]
    assert dials == [ipaddress.ip_address("93.184.216.34")]
    assert bound.address == ipaddress.ip_address("93.184.216.34")
    assert not sock.closed


@pytest.mark.asyncio
async def test_resolve_and_validate_checks_every_answer_without_dialing() -> None:
    calls: list[tuple[str, int]] = []

    async def resolver(host: str, port: int) -> list[str]:
        calls.append((host, port))
        return ["93.184.216.34", "2606:4700:4700::1111"]

    parsed, addresses = await resolve_and_validate("example.com:443", resolver=resolver)

    assert parsed.host == "example.com"
    assert calls == [("example.com", 443)]
    assert addresses == [
        ipaddress.ip_address("93.184.216.34"),
        ipaddress.ip_address("2606:4700:4700::1111"),
    ]


@pytest.mark.asyncio
async def test_resolve_and_validate_denies_mixed_answers_without_dialing() -> None:
    async def resolver(_host: str, _port: int) -> list[str]:
        return ["93.184.216.34", "10.0.0.1"]

    with pytest.raises(DestinationDeniedError):
        await resolve_and_validate("example.com:443", resolver=resolver)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "answers",
    [
        ["93.184.216.34", "10.0.0.1"],
        ["93.184.216.34", "100.64.0.1"],
        ["93.184.216.34", "169.254.169.254"],
        ["93.184.216.34", "::ffff:127.0.0.1"],
        ["93.184.216.34", "2001:db8::1"],
        ["224.0.0.1"],
        ["ff0e::1"],
        ["fec0::1"],
        ["2606:4700:4700::1111%eth0"],
        ["64:ff9b::808:808"],
    ],
)
async def test_any_restricted_answer_denies_before_dial(answers: list[str]) -> None:
    dials: list[str] = []

    async def resolver(host: str, port: int) -> list[str]:
        return answers

    async def dialer(address: object, port: int) -> FakeSocket:
        dials.append(str(address))
        sock = FakeSocket(str(address))
        sock.getpeername = lambda: (str(address), port)  # type: ignore[method-assign]
        return sock

    with pytest.raises(DestinationDeniedError):
        await resolve_and_connect("example.com:443", resolver=resolver, dialer=dialer)
    assert dials == []


@pytest.mark.asyncio
async def test_ipv6_literal_uses_numeric_ipv6_dial_and_verifies_peer() -> None:
    seen: list[object] = []
    sock = FakeSocket("2606:4700:4700::1111")

    async def resolver(host: str, port: int) -> list[str]:
        raise AssertionError("literal addresses must not trigger DNS")

    async def dialer(
        address: ipaddress.IPv4Address | ipaddress.IPv6Address, port: int
    ) -> FakeSocket:
        seen.append((address, port))
        return sock

    bound = await resolve_and_connect(
        "[2606:4700:4700::1111]:443", resolver=resolver, dialer=dialer
    )
    assert seen == [(ipaddress.ip_address("2606:4700:4700::1111"), 443)]
    assert bound.address == ipaddress.ip_address("2606:4700:4700::1111")


@pytest.mark.asyncio
async def test_system_resolver_calls_getaddrinfo_once_and_keeps_both_families(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loop = asyncio.get_running_loop()
    calls: list[tuple[object, ...]] = []

    async def fake_getaddrinfo(host: str, port: int, *, type: int) -> list[tuple]:
        calls.append((host, port, type))
        return [
            _addrinfo(host, socket.AF_INET, ("93.184.216.34", port)),
            _addrinfo(host, socket.AF_INET6, ("2606:4700:4700::1111", port, 0, 0)),
        ]

    monkeypatch.setattr(loop, "getaddrinfo", fake_getaddrinfo)
    answers = await capture_destination._system_resolver("example.com", 443)
    assert calls == [("example.com", 443, socket.SOCK_STREAM)]
    assert answers == ["93.184.216.34", "2606:4700:4700::1111"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("address", "expected_family", "expected_sockaddr"),
    [
        (
            ipaddress.ip_address("93.184.216.34"),
            socket.AF_INET,
            ("93.184.216.34", 443),
        ),
        (
            ipaddress.ip_address("2606:4700:4700::1111"),
            socket.AF_INET6,
            ("2606:4700:4700::1111", 443, 0, 0),
        ),
    ],
)
async def test_numeric_dialer_uses_numeric_sockaddr_without_dns(
    monkeypatch: pytest.MonkeyPatch,
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
    expected_family: int,
    expected_sockaddr: tuple[object, ...],
) -> None:
    loop = asyncio.get_running_loop()
    created: list[FakeOwnedSocket] = []
    connected: list[tuple[object, ...]] = []

    def fake_socket(family: int, socktype: int) -> FakeOwnedSocket:
        result = FakeOwnedSocket(family, socktype)
        created.append(result)
        return result

    async def fake_sock_connect(
        sock: FakeOwnedSocket, sockaddr: tuple[object, ...]
    ) -> None:
        connected.append(sockaddr)
        sock.peer = sockaddr

    monkeypatch.setattr(capture_destination.socket, "socket", fake_socket)
    monkeypatch.setattr(loop, "sock_connect", fake_sock_connect)
    result = await capture_destination._numeric_dialer(address, 443)

    assert result is created[0]
    assert created[0].family == expected_family
    assert created[0].socktype == socket.SOCK_STREAM
    assert created[0].blocking is False
    assert connected == [expected_sockaddr]
    assert not created[0].closed


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["error", "cancel"])
async def test_numeric_dialer_closes_socket_on_connect_error_or_cancellation(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    loop = asyncio.get_running_loop()
    created: list[FakeOwnedSocket] = []

    def fake_socket(family: int, socktype: int) -> FakeOwnedSocket:
        result = FakeOwnedSocket(family, socktype)
        created.append(result)
        return result

    async def failing_sock_connect(
        sock: FakeOwnedSocket, sockaddr: tuple[object, ...]
    ) -> None:
        if failure == "cancel":
            raise asyncio.CancelledError()
        raise OSError("private socket detail")

    monkeypatch.setattr(capture_destination.socket, "socket", fake_socket)
    monkeypatch.setattr(loop, "sock_connect", failing_sock_connect)
    expected = asyncio.CancelledError if failure == "cancel" else OSError
    with pytest.raises(expected):
        await capture_destination._numeric_dialer(
            ipaddress.ip_address("93.184.216.34"), 443
        )
    assert len(created) == 1
    assert created[0].closed


@pytest.mark.asyncio
async def test_default_factories_bind_selected_peer_without_second_dns_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loop = asyncio.get_running_loop()
    lookups: list[tuple[object, ...]] = []
    dials: list[tuple[object, ...]] = []
    sockets: list[FakeOwnedSocket] = []

    async def fake_getaddrinfo(host: str, port: int, *, type: int) -> list[tuple]:
        lookups.append((host, port, type))
        return [
            _addrinfo(host, socket.AF_INET, ("93.184.216.34", port)),
            _addrinfo(host, socket.AF_INET6, ("2606:4700:4700::1111", port, 0, 0)),
        ]

    def fake_socket(family: int, socktype: int) -> FakeOwnedSocket:
        result = FakeOwnedSocket(family, socktype)
        sockets.append(result)
        return result

    async def fake_sock_connect(
        sock: FakeOwnedSocket, sockaddr: tuple[object, ...]
    ) -> None:
        dials.append(sockaddr)
        # A hypothetical second DNS answer is intentionally never consulted.
        sock.peer = sockaddr

    monkeypatch.setattr(loop, "getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr(capture_destination.socket, "socket", fake_socket)
    monkeypatch.setattr(loop, "sock_connect", fake_sock_connect)

    bound = await resolve_and_connect("example.com:443")

    assert lookups == [("example.com", 443, socket.SOCK_STREAM)]
    assert dials == [("93.184.216.34", 443)]
    assert bound.address == ipaddress.ip_address("93.184.216.34")
    assert bound.socket is sockets[0]
    assert not sockets[0].closed


@pytest.mark.asyncio
async def test_mapped_public_address_is_normalized_to_ipv4() -> None:
    sock = FakeSocket("93.184.216.34")

    async def resolver(host: str, port: int) -> list[str]:
        return ["::ffff:93.184.216.34"]

    async def dialer(
        address: ipaddress.IPv4Address | ipaddress.IPv6Address, port: int
    ) -> FakeSocket:
        assert isinstance(address, ipaddress.IPv4Address)
        return sock

    bound = await resolve_and_connect(
        "example.com:443", resolver=resolver, dialer=dialer
    )
    assert bound.address == ipaddress.ip_address("93.184.216.34")


@pytest.mark.asyncio
async def test_mismatched_connected_peer_is_closed() -> None:
    sock = FakeSocket("93.184.216.35")

    async def resolver(host: str, port: int) -> list[str]:
        return ["93.184.216.34"]

    async def dialer(address: object, port: int) -> FakeSocket:
        return sock

    with pytest.raises(DestinationConnectionError):
        await resolve_and_connect("example.com:443", resolver=resolver, dialer=dialer)
    assert sock.closed


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", ["raises", "empty", "invalid-ip", "wrong-port", "missing-port"]
)
async def test_malformed_peer_is_sanitized_and_socket_closed(failure: str) -> None:
    sock = BrokenPeerSocket(failure)

    async def resolver(host: str, port: int) -> list[str]:
        return ["93.184.216.34"]

    async def dialer(address: object, port: int) -> BrokenPeerSocket:
        return sock

    with pytest.raises(DestinationConnectionError) as raised:
        await resolve_and_connect("example.com:443", resolver=resolver, dialer=dialer)
    assert str(raised.value) == "destination-connect-failed"
    assert sock.closed


@pytest.mark.asyncio
async def test_resolution_timeout_is_typed_and_sanitized() -> None:
    async def resolver(host: str, port: int) -> list[str]:
        await asyncio.sleep(0.05)
        return ["93.184.216.34"]

    with pytest.raises(DestinationTimeoutError) as raised:
        await resolve_and_connect(
            "private-name.example:443", resolver=resolver, timeout=0.001
        )
    assert str(raised.value) == "destination-timeout"
    assert "private-name" not in str(raised.value)


@pytest.mark.parametrize(
    "authority",
    [
        "",
        "example.com",
        "example.com:0",
        "example.com:65536",
        "example.com:" + ("9" * 5000),
        "example.com:0443",
        "user@example.com:443",
        "example.com:443/path",
        "example.com:443?x=1",
        "example.com:443#fragment",
        "example.com:443%40evil",
        "127.1:443",
        "[127.0.0.1]:443",
        "[fe80::1%25eth0]:443",
        "example..com:443",
        "bad_host.example:443",
        "example.com:٤٤٣",
    ],
)
def test_malformed_or_ambiguous_authority_denied(authority: str) -> None:
    async def resolver(host: str, port: int) -> list[str]:
        raise AssertionError("malformed authority must be rejected before DNS")

    with pytest.raises(DestinationDeniedError):
        asyncio.run(resolve_and_connect(authority, resolver=resolver))


@pytest.mark.asyncio
async def test_empty_dns_answer_is_rejected_without_dial() -> None:
    async def resolver(host: str, port: int) -> list[str]:
        return []

    async def dialer(address: object, port: int) -> FakeSocket:
        raise AssertionError("empty answer set cannot dial")

    with pytest.raises(DestinationResolutionError):
        await resolve_and_connect("example.com:443", resolver=resolver, dialer=dialer)


@pytest.mark.asyncio
async def test_resolution_exception_is_sanitized() -> None:
    async def resolver(host: str, port: int) -> list[str]:
        raise OSError("private resolver detail")

    with pytest.raises(DestinationResolutionError) as raised:
        await resolve_and_connect("example.com:443", resolver=resolver)
    assert str(raised.value) == "destination-resolution-failed"
    assert "private resolver detail" not in str(raised.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "family,sockaddr", [(socket.AF_UNIX, "local"), (socket.AF_INET, (7, 443))]
)
async def test_default_resolver_rejects_non_internet_or_malformed_records(
    monkeypatch: pytest.MonkeyPatch, family: int, sockaddr: str | tuple[int, int]
) -> None:
    loop = asyncio.get_running_loop()

    async def malformed_getaddrinfo(
        host: str, port: int, *, type: int
    ) -> list[tuple[int, int, int, str, str | tuple[int, int]]]:
        return [(family, socket.SOCK_STREAM, 0, "", sockaddr)]

    monkeypatch.setattr(loop, "getaddrinfo", malformed_getaddrinfo)
    with pytest.raises(DestinationResolutionError):
        await capture_destination._system_resolver("example.com", 443)


@pytest.mark.asyncio
async def test_model_target_private_address_requires_exact_host_grant() -> None:
    calls: list[tuple[str, int]] = []
    dials: list[str] = []

    async def resolver(host: str, port: int) -> list[str]:
        calls.append((host, port))
        return ["172.31.253.20"]

    async def dialer(address: object, port: int) -> FakeSocket:
        dials.append(str(address))
        sock = FakeSocket(str(address))
        sock.getpeername = lambda: (str(address), port)  # type: ignore[method-assign]
        return sock

    with pytest.raises(DestinationDeniedError):
        await resolve_and_connect_model_target(
            "llm-svc:4001", resolver=resolver, dialer=dialer
        )
    assert dials == []

    bound = await resolve_and_connect_model_target(
        "llm-svc:4001",
        private_host_grants=frozenset({"llm-svc"}),
        resolver=resolver,
        dialer=dialer,
    )
    assert bound.address == ipaddress.ip_address("172.31.253.20")
    assert dials == ["172.31.253.20"]
    assert calls == [("llm-svc", 4001), ("llm-svc", 4001)]


@pytest.mark.asyncio
async def test_model_target_rejects_mixed_private_public_dns_answers() -> None:
    dials: list[str] = []

    async def resolver(host: str, port: int) -> list[str]:
        return ["172.31.253.20", "93.184.216.34"]

    async def dialer(address: object, port: int) -> FakeSocket:
        dials.append(str(address))
        sock = FakeSocket(str(address))
        sock.getpeername = lambda: (str(address), port)  # type: ignore[method-assign]
        return sock

    with pytest.raises(DestinationDeniedError):
        await resolve_and_connect_model_target(
            "llm-svc:4001",
            private_host_grants=frozenset({"llm-svc"}),
            resolver=resolver,
            dialer=dialer,
        )
    assert dials == []


@pytest.mark.asyncio
async def test_model_target_closes_connection_when_numeric_peer_does_not_match() -> (
    None
):
    socket = FakeSocket("172.31.253.21")

    async def resolver(host: str, port: int) -> list[str]:
        return ["172.31.253.20"]

    async def dialer(address: object, port: int) -> FakeSocket:
        return socket

    with pytest.raises(DestinationConnectionError):
        await resolve_and_connect_model_target(
            "llm-svc:443",
            private_host_grants=frozenset({"llm-svc"}),
            resolver=resolver,
            dialer=dialer,
        )
    assert socket.closed
