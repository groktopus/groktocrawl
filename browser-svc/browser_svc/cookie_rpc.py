"""Bounded cookie operations over a private Unix-domain socket."""

from __future__ import annotations

import asyncio
import json
import os
import stat
from contextlib import suppress
from pathlib import Path
from typing import Any

DEFAULT_COOKIE_RPC_SOCKET = "/run/browser/cookies.sock"
MAX_RPC_LINE_BYTES = 1024 * 1024 + 4096
_COOKIE_PREFIX = "cf:clearance:"


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate RPC field")
        result[key] = value
    return result


def _validate_key(key: Any) -> str:
    if (
        not isinstance(key, str)
        or not key.startswith(_COOKIE_PREFIX)
        or len(key) > 512
    ):
        raise ValueError("invalid cookie key")
    return key


class CookieRPCClient:
    """Redis-compatible get/setex subset for the renderer."""

    def __init__(self, socket_path: str = DEFAULT_COOKIE_RPC_SOCKET):
        self.socket_path = _absolute_socket_path(socket_path)

    async def get(self, key: str) -> str | None:
        result = await self._call({"operation": "get", "key": _validate_key(key)})
        value = result.get("value")
        if value is not None and not isinstance(value, str):
            raise RuntimeError("cookie service returned invalid data")
        return value

    async def setex(self, key: str, ttl: int, value: str) -> None:
        if (
            isinstance(ttl, bool)
            or not isinstance(ttl, int)
            or not 1 <= ttl <= 1500
            or not isinstance(value, str)
            or len(value.encode("utf-8")) > 1024 * 1024
        ):
            raise ValueError("invalid cookie write")
        await self._call(
            {
                "operation": "setex",
                "key": _validate_key(key),
                "ttl": ttl,
                "value": value,
            }
        )

    async def _call(self, request: dict[str, Any]) -> dict[str, Any]:
        writer: asyncio.StreamWriter | None = None
        try:
            reader, connected_writer = await asyncio.wait_for(
                asyncio.open_unix_connection(self.socket_path), timeout=2.0
            )
            writer = connected_writer
            body = json.dumps(request, separators=(",", ":")).encode("utf-8")
            if len(body) > MAX_RPC_LINE_BYTES - 1:
                raise ValueError("cookie RPC request is too large")
            connected_writer.write(body + b"\n")
            await asyncio.wait_for(connected_writer.drain(), timeout=2.0)
            line = await asyncio.wait_for(
                reader.readline(), timeout=2.0
            )
            if not line or len(line) > MAX_RPC_LINE_BYTES or not line.endswith(b"\n"):
                raise RuntimeError("cookie service returned invalid data")
            result = json.loads(line, object_pairs_hook=_reject_duplicate_pairs)
            if not isinstance(result, dict) or result.get("ok") is not True:
                raise RuntimeError("cookie service is unavailable")
            return result
        except (OSError, TimeoutError, json.JSONDecodeError) as exc:
            raise RuntimeError("cookie service is unavailable") from exc
        finally:
            if writer is not None:
                writer.close()
                with suppress(OSError, TimeoutError):
                    await asyncio.wait_for(writer.wait_closed(), timeout=1.0)


class CookieRPCServer:
    """Serve only the narrow cookie API against the controller's Valkey client."""

    def __init__(self, socket_path: str, redis_client: Any | None):
        self.socket_path = _absolute_socket_path(socket_path)
        self.redis_client = redis_client
        self.server: asyncio.AbstractServer | None = None
        self._active = asyncio.Semaphore(32)

    async def start(self) -> None:
        path = Path(self.socket_path)
        path.parent.mkdir(mode=0o770, parents=True, exist_ok=True)
        if path.is_symlink():
            raise RuntimeError("cookie socket path must not be a symlink")
        if path.exists():
            if not stat.S_ISSOCK(path.stat().st_mode):
                raise RuntimeError("cookie socket path is not a socket")
            path.unlink()
        self.server = await asyncio.start_unix_server(
            self._handle, path=self.socket_path, limit=MAX_RPC_LINE_BYTES
        )
        os.chmod(path, 0o660)

    async def close(self) -> None:
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()
            self.server = None
        path = Path(self.socket_path)
        if path.exists() and path.is_socket():
            path.unlink()

    async def _handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        async with self._active:
            try:
                line = await asyncio.wait_for(
                    reader.readline(), timeout=2.0
                )
                if not line or len(line) > MAX_RPC_LINE_BYTES or not line.endswith(b"\n"):
                    raise ValueError("invalid RPC line")
                request = json.loads(
                    line, object_pairs_hook=_reject_duplicate_pairs
                )
                result = await self._dispatch(request)
                response = {"ok": True, **result}
            except Exception:
                response = {"ok": False, "error": "unavailable"}
            try:
                payload = json.dumps(response, separators=(",", ":")).encode("utf-8")
                if len(payload) > MAX_RPC_LINE_BYTES - 1:
                    payload = b'{"ok":false,"error":"unavailable"}'
                writer.write(payload + b"\n")
                await asyncio.wait_for(writer.drain(), timeout=2.0)
            except (ConnectionError, OSError, TimeoutError):
                pass
            finally:
                writer.close()
                with suppress(ConnectionError, OSError, TimeoutError):
                    await asyncio.wait_for(writer.wait_closed(), timeout=1.0)

    async def _dispatch(self, request: Any) -> dict[str, Any]:
        if not isinstance(request, dict):
            raise ValueError("invalid RPC request")
        operation = request.get("operation")
        key = _validate_key(request.get("key"))
        if operation == "get" and set(request) == {"operation", "key"}:
            if self.redis_client is None:
                return {"value": None}
            return {"value": await self.redis_client.get(key)}
        if operation == "setex" and set(request) == {
            "operation",
            "key",
            "ttl",
            "value",
        }:
            ttl = request.get("ttl")
            value = request.get("value")
            if (
                isinstance(ttl, bool)
                or not isinstance(ttl, int)
                or not 1 <= ttl <= 1500
                or not isinstance(value, str)
                or len(value.encode("utf-8")) > 1024 * 1024
            ):
                raise ValueError("invalid cookie write")
            if self.redis_client is not None:
                await self.redis_client.setex(key, ttl, value)
            return {}
        raise ValueError("invalid RPC operation")


def _absolute_socket_path(value: str) -> str:
    if not isinstance(value, str) or not Path(value).is_absolute() or "\x00" in value:
        raise ValueError("socket path must be absolute")
    return value
