"""Narrow Valkey capability socket for the isolated scraper process.

The scraper receives cache, robots, politeness, and clearance-cookie operations
only. It never receives a Redis connection or an arbitrary command transport.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import stat
from contextlib import suppress
from pathlib import Path
from typing import Any

DEFAULT_SOCKET = "/run/scraper-state/control.sock"
MAX_VALUE_BYTES = 8 * 1024 * 1024
# json.dumps may expand one control byte to six ASCII bytes (``\\u00xx``).
# This is a bounded worst-case frame, not a larger stored-value allowance.
MAX_LINE_BYTES = MAX_VALUE_BYTES * 6 + 8192
MAX_ACTIVE_CONNECTIONS = 2
MAX_TTL_SECONDS = 365 * 24 * 60 * 60
_HEX = re.compile(r"^[0-9a-f]{64}$")
_COOKIE_DOMAIN = re.compile(r"^[a-z0-9.-]{1,253}$")

RESERVE_SLOT_SCRIPT = """\
local clock = redis.call('TIME')
local now = clock[1] * 1000 + math.floor(clock[2] / 1000)
local delay = tonumber(ARGV[1])
local ceiling = tonumber(ARGV[2])
local next_slot = tonumber(redis.call('GET', KEYS[1]) or '0')
local slot = math.max(now, next_slot)
local wait = slot - now
if wait > ceiling then return -1 end
redis.call('PSETEX', KEYS[1], math.max(1000, wait + delay + 1000), slot + delay)
return wait
"""


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate RPC field")
        result[key] = value
    return result


def _valid_key(value: Any) -> str:
    if not isinstance(value, str) or len(value) > 512:
        raise ValueError("invalid key")
    if value.startswith("scrape_cache:") and _HEX.fullmatch(value[13:]):
        return value
    if value.startswith("politeness:robots:") and _HEX.fullmatch(value[18:]):
        return value
    if value.startswith("politeness:rate:"):
        digest = value[16:].removesuffix(":slots")
        if _HEX.fullmatch(digest):
            return value
    prefix = "cf:clearance:"
    if value.startswith(prefix):
        domain = value[len(prefix):]
        labels = domain.split(".")
        if (
            _COOKIE_DOMAIN.fullmatch(domain)
            and all(
                1 <= len(label) <= 63
                and label[0].isalnum()
                and label[-1].isalnum()
                for label in labels
            )
        ):
            return value
    raise ValueError("invalid key")


def _value_key(value: Any) -> str:
    key = _valid_key(value)
    if key.endswith(":slots"):
        raise ValueError("reservation keys are writable only by reserve_slot")
    return key


class ValkeyControlClient:
    """Redis-compatible subset used by scraper cache and cookie helpers."""

    def __init__(self, socket_path: str = DEFAULT_SOCKET):
        path = Path(socket_path)
        if not path.is_absolute() or "\x00" in socket_path:
            raise ValueError("socket path must be absolute")
        self.socket_path = str(path)

    async def ping(self) -> bool:
        return (await self._call({"operation": "ping"})).get("value") is True

    async def get(self, key: str) -> str | None:
        result = await self._call({"operation": "get", "key": _value_key(key)})
        value = result.get("value")
        if value is not None and not isinstance(value, str):
            raise RuntimeError("Valkey control returned invalid value")
        return value

    async def setex(self, key: str, ttl: int, value: str) -> None:
        _validate_ttl_value(ttl, value)
        await self._call({
            "operation": "setex", "key": _value_key(key),
            "ttl": ttl, "value": value,
        })

    async def eval(self, script: str, numkeys: int, *args: Any) -> int:
        if (
            script != RESERVE_SLOT_SCRIPT
            or type(numkeys) is not int
            or numkeys != 1
            or len(args) != 3
        ):
            raise ValueError("unsupported Valkey script")
        key = _valid_key(args[0])
        if not key.startswith("politeness:rate:") or not key.endswith(":slots"):
            raise ValueError("unsupported reservation key")
        delay, ceiling = args[1:]
        if (
            type(delay) is not int or not 0 <= delay <= 2**31 - 1
            or type(ceiling) is not int or ceiling != 30000
        ):
            raise ValueError("invalid reservation parameters")
        value = await self._call({
            "operation": "reserve_slot", "key": key,
            "delay_ms": delay, "ceiling_ms": ceiling,
        })
        result = value.get("value")
        if type(result) is not int:
            raise RuntimeError("Valkey control returned invalid reservation")
        return result

    async def aclose(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def _call(self, request: dict[str, Any]) -> dict[str, Any]:
        writer: asyncio.StreamWriter | None = None
        try:
            reader, connected_writer = await asyncio.wait_for(
                asyncio.open_unix_connection(self.socket_path), timeout=2
            )
            writer = connected_writer
            body = json.dumps(request, separators=(",", ":")).encode()
            if len(body) >= MAX_LINE_BYTES:
                raise ValueError("Valkey RPC request too large")
            connected_writer.write(body + b"\n")
            await asyncio.wait_for(connected_writer.drain(), timeout=2)
            line = await asyncio.wait_for(reader.readline(), timeout=10)
            if not line or len(line) > MAX_LINE_BYTES or not line.endswith(b"\n"):
                raise RuntimeError("invalid Valkey RPC response")
            result = json.loads(line, object_pairs_hook=_pairs)
            if not isinstance(result, dict) or result.get("ok") is not True:
                raise RuntimeError("Valkey control unavailable")
            return result
        except (OSError, TimeoutError, json.JSONDecodeError) as exc:
            raise RuntimeError("Valkey control unavailable") from exc
        finally:
            if writer is not None:
                writer.close()
                with suppress(OSError, TimeoutError):
                    await asyncio.wait_for(writer.wait_closed(), timeout=1)


def _validate_ttl_value(ttl: Any, value: Any) -> None:
    if (
        type(ttl) is not int or not 1 <= ttl <= MAX_TTL_SECONDS
        or not isinstance(value, str)
        or len(value.encode("utf-8")) > MAX_VALUE_BYTES
    ):
        raise ValueError("invalid Valkey value")


class ValkeyControlServer:
    """Serve the restricted operation set against one fixed Redis client."""

    def __init__(
        self,
        socket_path: str,
        redis_client: Any,
        *,
        socket_gid: int = 20000,
        directory_uid: int = 10002,
        directory_gid: int = 20000,
        directory_mode: int = 0o2710,
    ):
        path = Path(socket_path)
        if not path.is_absolute() or "\x00" in socket_path:
            raise ValueError("socket path must be absolute")
        self.socket_path = path
        self.redis = redis_client
        self.socket_gid = socket_gid
        self.directory_uid = directory_uid
        self.directory_gid = directory_gid
        self.directory_mode = directory_mode
        self.server: asyncio.AbstractServer | None = None
        self.active = 0
        self._active_lock = asyncio.Lock()

    async def start(self) -> None:
        parent = self.socket_path.parent.lstat()
        if (
            stat.S_ISLNK(parent.st_mode)
            or not stat.S_ISDIR(parent.st_mode)
            or parent.st_uid != self.directory_uid
            or parent.st_gid != self.directory_gid
            or stat.S_IMODE(parent.st_mode) != self.directory_mode
        ):
            raise RuntimeError("Valkey control socket directory is invalid")
        if self.socket_path.is_symlink():
            raise RuntimeError("Valkey socket path may not be a symlink")
        if self.socket_path.exists():
            if not stat.S_ISSOCK(self.socket_path.stat().st_mode):
                raise RuntimeError("Valkey socket path is not a socket")
            self.socket_path.unlink()
        self.server = await asyncio.start_unix_server(
            self._handle,
            path=str(self.socket_path),
            limit=MAX_LINE_BYTES,
            backlog=MAX_ACTIVE_CONNECTIONS,
        )
        os.chmod(self.socket_path, 0o660)
        socket_stat = self.socket_path.lstat()
        if (
            stat.S_ISLNK(socket_stat.st_mode)
            or not stat.S_ISSOCK(socket_stat.st_mode)
            or socket_stat.st_uid != os.getuid()
            or socket_stat.st_gid != self.socket_gid
            or stat.S_IMODE(socket_stat.st_mode) != 0o660
        ):
            self.server.close()
            await self.server.wait_closed()
            self.server = None
            raise RuntimeError("Valkey control socket permissions are invalid")

    async def close(self) -> None:
        if self.server:
            self.server.close()
            await self.server.wait_closed()
            self.server = None
        if self.socket_path.exists() and self.socket_path.is_socket():
            self.socket_path.unlink()
        await self.redis.aclose()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        async with self._active_lock:
            if self.active >= MAX_ACTIVE_CONNECTIONS:
                writer.close()
                with suppress(ConnectionError, OSError, TimeoutError):
                    await asyncio.wait_for(writer.wait_closed(), timeout=0.25)
                return
            self.active += 1
        try:
            try:
                line = await asyncio.wait_for(reader.readline(), timeout=5)
                if not line or len(line) > MAX_LINE_BYTES or not line.endswith(b"\n"):
                    raise ValueError("invalid RPC frame")
                request = json.loads(line, object_pairs_hook=_pairs)
                result = await self._dispatch(request)
                response = {"ok": True, **result}
            except Exception:
                response = {"ok": False, "error": "unavailable"}
            try:
                payload = json.dumps(response, separators=(",", ":")).encode() + b"\n"
                if len(payload) > MAX_LINE_BYTES:
                    payload = b'{"ok":false,"error":"unavailable"}\n'
                writer.write(payload)
                await asyncio.wait_for(writer.drain(), timeout=10)
            except (ConnectionError, OSError, TimeoutError):
                pass
            finally:
                writer.close()
                with suppress(ConnectionError, OSError, TimeoutError):
                    await asyncio.wait_for(writer.wait_closed(), timeout=1)
        finally:
            async with self._active_lock:
                self.active -= 1

    async def _dispatch(self, request: Any) -> dict[str, Any]:
        if not isinstance(request, dict):
            raise ValueError("invalid RPC request")
        operation = request.get("operation")
        if operation == "ping" and set(request) == {"operation"}:
            return {"value": await self.redis.ping() is True}
        if operation == "get" and set(request) == {"operation", "key"}:
            value = await self.redis.get(_value_key(request["key"]))
            if value is not None and len(str(value).encode("utf-8")) > MAX_VALUE_BYTES:
                raise ValueError("stored value too large")
            return {"value": value}
        if operation == "setex" and set(request) == {
            "operation", "key", "ttl", "value"
        }:
            key = _value_key(request["key"])
            _validate_ttl_value(request["ttl"], request["value"])
            await self.redis.setex(key, request["ttl"], request["value"])
            return {}
        if operation == "reserve_slot" and set(request) == {
            "operation", "key", "delay_ms", "ceiling_ms"
        }:
            key = _valid_key(request["key"])
            delay = request["delay_ms"]
            ceiling = request["ceiling_ms"]
            if (
                not key.startswith("politeness:rate:") or not key.endswith(":slots")
                or type(delay) is not int or not 0 <= delay <= 2**31 - 1
                or type(ceiling) is not int or ceiling != 30000
            ):
                raise ValueError("invalid reservation")
            value = await self.redis.eval(RESERVE_SLOT_SCRIPT, 1, key, delay, ceiling)
            return {"value": int(value)}
        raise ValueError("unsupported Valkey operation")


async def serve() -> None:
    from redis.asyncio import Redis

    host = os.environ.get("VALKEY_HOST", "candidate-valkey")
    port = int(os.environ.get("VALKEY_PORT", "6379"))
    db = int(os.environ.get("VALKEY_DB", "0"))
    redis = Redis(host=host, port=port, db=db, decode_responses=True)
    await redis.ping()
    server = ValkeyControlServer(
        os.environ.get("VALKEY_RPC_SOCKET", DEFAULT_SOCKET), redis
    )
    await server.start()
    try:
        await asyncio.Event().wait()
    finally:
        await server.close()


if __name__ == "__main__":
    asyncio.run(serve())
