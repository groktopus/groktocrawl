"""Capability and compatibility tests for scraper Valkey UDS operations."""

from __future__ import annotations

import asyncio
import errno
import hashlib
import os
import shutil
import stat
import tempfile
from pathlib import Path

import pytest
from scraper.politeness import _RESERVE_SLOT
from scraper.valkey_control import (
    MAX_ACTIVE_CONNECTIONS,
    MAX_LINE_BYTES,
    MAX_VALUE_BYTES,
    ValkeyControlClient,
    ValkeyControlServer,
)

from tests.outcome_governance import governed_skip


def _skip_if_socket_binding_is_restricted(error: OSError) -> None:
    if error.errno not in {errno.EACCES, errno.EPERM}:
        raise error
    governed_skip(
        "sandbox does not permit local Unix socket binding",
        owner="repository-maintainer",
        issue="#436",
        classification="retained",
        environment="restricted test sandbox denies AF_UNIX bind",
    )


class FakeRedis:
    def __init__(self):
        self.values: dict[str, str] = {}
        self.ttls: dict[str, int] = {}
        self.calls: list[tuple[object, ...]] = []

    async def ping(self):
        return True

    async def get(self, key):
        self.calls.append(("get", key))
        return self.values.get(key)

    async def setex(self, key, ttl, value):
        self.calls.append(("setex", key, ttl))
        self.values[key] = value
        self.ttls[key] = ttl

    async def eval(self, script, count, key, delay, ceiling):
        self.calls.append(("eval", script, count, key, delay, ceiling))
        assert script == _RESERVE_SLOT
        return 0

    async def aclose(self):
        return None


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def test_only_fixed_keyspaces_and_reservation_script_are_client_capabilities():
    client = ValkeyControlClient("/run/not-opened.sock")
    assert client.socket_path == "/run/not-opened.sock"
    with pytest.raises(ValueError, match="invalid key"):
        asyncio.run(client.get("admin:settings"))
    with pytest.raises(ValueError, match="reservation keys"):
        asyncio.run(client.setex(f"politeness:rate:{_digest('example')}:slots", 10, "x"))
    with pytest.raises(ValueError, match="unsupported Valkey script"):
        asyncio.run(client.eval("return redis.call('FLUSHALL')", 0))
    with pytest.raises(ValueError, match="invalid reservation parameters"):
        asyncio.run(
            client.eval(
                _RESERVE_SLOT,
                1,
                f"politeness:rate:{_digest('example')}:slots",
                True,
                30000,
            )
        )


@pytest.mark.asyncio
async def test_typed_socket_preserves_cache_robots_cookie_and_rate_operations():
    socket_dir = Path(tempfile.mkdtemp(prefix="vk-", dir="/tmp"))
    os.chown(socket_dir, os.getuid(), os.getgid())
    socket_dir.chmod(0o700)
    socket_path = str(socket_dir / "control.sock")
    redis = FakeRedis()
    server = ValkeyControlServer(
        socket_path,
        redis,
        socket_gid=os.getgid(),
        directory_uid=os.getuid(),
        directory_gid=os.getgid(),
        directory_mode=0o700,
    )
    try:
        await server.start()
    except OSError as error:
        _skip_if_socket_binding_is_restricted(error)
    socket_info = os.lstat(socket_path)
    assert stat.S_IMODE(socket_info.st_mode) == 0o660
    assert socket_info.st_gid == os.getgid()
    client = ValkeyControlClient(socket_path)
    try:
        assert await client.ping() is True
        cache_key = f"scrape_cache:{_digest('https://example.test')}"
        robots_key = f"politeness:robots:{_digest('example.test')}"
        rate_key = f"politeness:rate:{_digest('example.test')}"
        cookie_key = "cf:clearance:example.test"
        await client.setex(cache_key, 3600, '{"markdown":"cached"}')
        await client.setex(robots_key, 3600, "User-agent: *")
        await client.setex(rate_key, 60, "123.0")
        await client.setex(cookie_key, 1500, '{"cookies":[]}')
        assert await client.get(cache_key) == '{"markdown":"cached"}'
        assert await client.get(robots_key) == "User-agent: *"
        assert await client.get(rate_key) == "123.0"
        assert await client.get(cookie_key) == '{"cookies":[]}'
        assert await client.eval(_RESERVE_SLOT, 1, f"{rate_key}:slots", 125, 30000) == 0
        assert redis.ttls[cookie_key] == 1500
        assert [call[0] for call in redis.calls].count("eval") == 1
        with pytest.raises(ValueError, match="unsupported Valkey script"):
            await client.eval("return redis.call('CONFIG', 'SET')", 0)
    finally:
        await server.close()
        shutil.rmtree(socket_dir)


def test_rpc_memory_limits_are_bounded_before_frame_reading():
    assert MAX_ACTIVE_CONNECTIONS == 2
    assert MAX_VALUE_BYTES == 8 * 1024 * 1024
    assert MAX_LINE_BYTES == MAX_VALUE_BYTES * 6 + 8192


@pytest.mark.asyncio
async def test_third_held_connection_is_rejected_without_waiting_for_a_frame():
    socket_dir = Path(tempfile.mkdtemp(prefix="vk-", dir="/tmp"))
    os.chown(socket_dir, os.getuid(), os.getgid())
    socket_dir.chmod(0o700)
    socket_path = str(socket_dir / "control.sock")
    server = ValkeyControlServer(
        socket_path,
        FakeRedis(),
        socket_gid=os.getgid(),
        directory_uid=os.getuid(),
        directory_gid=os.getgid(),
        directory_mode=0o700,
    )
    try:
        await server.start()
    except OSError as error:
        _skip_if_socket_binding_is_restricted(error)
    connections = []
    try:
        for _ in range(MAX_ACTIVE_CONNECTIONS):
            connections.append(await asyncio.open_unix_connection(socket_path))
        await asyncio.sleep(0.05)
        assert server.active == MAX_ACTIVE_CONNECTIONS
        rejected_reader, rejected_writer = await asyncio.open_unix_connection(socket_path)
        try:
            rejected = await asyncio.wait_for(rejected_reader.readline(), timeout=1)
            assert rejected == b""
            assert server.active == MAX_ACTIVE_CONNECTIONS
        finally:
            rejected_writer.close()
            await rejected_writer.wait_closed()
    finally:
        for _reader, writer in connections:
            writer.close()
            await writer.wait_closed()
        await server.close()
        shutil.rmtree(socket_dir)
