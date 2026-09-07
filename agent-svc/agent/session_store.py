"""Session storage backed by Valkey with TTL.

Stores research session metadata, step history, accumulated artifact,
and reference content under the ``session:`` key prefix.  Follows the
same Redis/Valkey patterns as the existing ``JobStore``.

Key schema (HSET for meta and refs, legacy JSON history plus an append log):
  session:{id}:meta     → HSET {id, status, created_at, expires_at, step_count, ttl, artifact_chars}
  session:{id}:steps    → immutable legacy JSON array of step objects
  session:{id}:step_log → list of new step JSON objects
  session:{id}:artifact → plain text markdown (accumulated, append-only)
  session:{id}:refs     → HSET of ref_id → JSON {url, title, char_count, markdown}

Concurrency guarantees:
  - Atomic step counter via Valkey ``HINCRBY`` on ``session:{id}:meta``
    field ``step_count``.  No read-modify-write race possible.
  - Per-session locking via ``SETNX`` with 30s timeout to serialise
    concurrent step execution on the same session.

Default TTL: 1 hour (3600s).  TTL resets on every write operation.
"""

import asyncio
import json
import uuid
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta

from redis import Redis

_LOCK_OWNER: ContextVar[str | None] = ContextVar("session_lock_owner", default=None)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _expires_iso(ttl: int = 3600) -> str:
    """Return ISO 8601 timestamp for TTL seconds from now."""
    return (datetime.now(UTC) + timedelta(seconds=ttl)).isoformat()


# ── Key helpers ─────────────────────────────────────────────────


def _meta_key(session_id: str) -> str:
    return f"session:{session_id}:meta"


def _steps_key(session_id: str) -> str:
    return f"session:{session_id}:steps"


def _step_log_key(session_id: str) -> str:
    return f"session:{session_id}:step_log"


def _artifact_key(session_id: str) -> str:
    return f"session:{session_id}:artifact"


def _refs_key(session_id: str) -> str:
    return f"session:{session_id}:refs"


def _lock_key(session_id: str) -> str:
    return f"session:{session_id}:lock"


def _idempotency_key(session_id: str) -> str:
    return f"session:{session_id}:idempotency"


def _decode_hash_json(raw: str) -> dict:
    """Decode Valkey's cjson representation of HGETALL output."""
    parsed = json.loads(raw)
    if isinstance(parsed, dict):
        return parsed
    return dict(zip(parsed[::2], parsed[1::2], strict=True))


def _all_keys(session_id: str) -> list[str]:
    """Return all session keys (used for delete and TTL refresh)."""
    return [
        _meta_key(session_id),
        _steps_key(session_id),
        _artifact_key(session_id),
        _refs_key(session_id),
        _step_log_key(session_id),
        _idempotency_key(session_id),
    ]


class SessionStore:
    """Valkey-backed session storage with TTL-based expiry.

    Key schema:
      session:{id}:meta     → HSET {id, status, created_at, expires_at, step_count, ttl, artifact_chars}
      session:{id}:steps    → immutable legacy JSON step prefix
      session:{id}:step_log → append-only list of JSON steps
      session:{id}:artifact → plain text markdown (accumulated, append-only)
      session:{id}:refs     → HSET of ref_id → JSON {url, title, markdown, scraped_at, source, char_count}

    Default TTL: 1 hour (3600s).  TTL resets on every write operation.
    """

    def __init__(
        self,
        redis_url: str = "redis://localhost:6379/0",
        default_ttl: int = 3600,
    ):
        self.redis = Redis.from_url(
            redis_url,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=10,
        )
        self.default_ttl = default_ttl
        self._io_slots = asyncio.Semaphore(8)

    def set_lock_owner(self, owner_token: str):
        """Bind a serialized session owner to storage calls in this context."""
        return _LOCK_OWNER.set(owner_token)

    def reset_lock_owner(self, context) -> None:
        _LOCK_OWNER.reset(context)

    # ── Create / Read / Update / Delete ──────────────────────────

    def create(self, ttl: int | None = None) -> str:
        """Create a new session and return its ID.

        Stores meta as a HSET so ``HINCRBY`` can atomically increment
        ``step_count`` without read-modify-write races.

        Args:
            ttl: Session TTL in seconds.  Defaults to ``self.default_ttl`` (1 hour).

        Returns:
            The new session ID (UUID v4).
        """
        session_id = str(uuid.uuid4())
        effective_ttl = ttl if ttl is not None else self.default_ttl

        # Store meta as a HSET — individual fields for HINCRBY support
        meta_key = _meta_key(session_id)
        meta_mapping: dict[str, str] = {
            "id": session_id,
            "status": "active",
            "created_at": _now_iso(),
            "expires_at": _expires_iso(effective_ttl),
            "step_count": "0",  # string for HINCRBY compatibility
            "ttl": str(effective_ttl),
            # Redis STRLEN reports bytes.  Keep a separate character count so
            # the public API retains its existing Unicode semantics.
            "artifact_chars": "0",
            "revision": "0",
            "next_step_index": "0",
        }
        self.redis.hset(meta_key, mapping=meta_mapping)  # type: ignore[arg-type]
        self.redis.expire(meta_key, effective_ttl)

        # Steps stored as JSON string (same as before)
        self.redis.set(
            _steps_key(session_id),
            json.dumps([]),
            ex=effective_ttl,
        )
        # Artifact stored as plain string
        self.redis.set(
            _artifact_key(session_id),
            "",
            ex=effective_ttl,
        )
        # Refs stored as HSET (ref_id → JSON ref_data)
        refs_key = _refs_key(session_id)
        # Create an empty hash so the key exists with TTL
        self.redis.hset(refs_key, "__init__", "1")
        self.redis.hdel(refs_key, "__init__")
        self.redis.expire(refs_key, effective_ttl)
        idempotency_key = _idempotency_key(session_id)
        # Redis removes an empty hash.  Keep an inert JSON record so this
        # data key has the same TTL lifecycle as the other session keys.
        self.redis.hset(idempotency_key, "__init__", '{"status":"sentinel"}')
        self.redis.expire(idempotency_key, effective_ttl)

        return session_id

    def get(self, session_id: str) -> dict | None:
        """Get session metadata + step summaries (no full refs).

        Returns None if the session does not exist or has expired.
        The returned dict has the same shape as the old JSON-meta format
        for backward compatibility with ``session.py`` and ``api.py``.
        """
        meta_raw = self.redis.hgetall(_meta_key(session_id))
        if not meta_raw:
            return None

        # Build meta dict, converting string values back to appropriate types
        meta: dict = dict(meta_raw)
        meta["step_count"] = int(meta.get("step_count", "0"))
        meta["ttl"] = int(meta.get("ttl", str(self.default_ttl)))

        meta["steps"] = self.get_steps(session_id)

        # Include artifact length for progress visibility
        artifact_chars = meta.get("artifact_chars")
        if artifact_chars is not None:
            meta["artifact_length"] = int(artifact_chars)
        else:
            # Existing sessions predate the counter.  This one-time fallback
            # preserves their character-count behavior without changing the
            # public response shape.
            artifact_raw = self.redis.get(_artifact_key(session_id))
            meta["artifact_length"] = len(artifact_raw) if artifact_raw else 0

        return meta

    def update_meta(self, session_id: str, updates: dict) -> bool:
        """Update session metadata fields atomically.

        Uses ``HSET`` for individual field updates.  ``step_count`` and
        ``ttl`` updates are stored as strings for HINCRBY compatibility.

        Returns False if the session does not exist.
        """
        # Coerce numeric values to strings for HSET
        string_updates: dict[str, str] = {}
        for k, v in updates.items():
            if k in ("step_count", "ttl"):
                string_updates[k] = str(v)
            else:
                string_updates[k] = str(v) if not isinstance(v, str) else v

        encoded = [value for item in string_updates.items() for value in item]
        script = """
        -- session_update_meta_v1
        if redis.call('exists', KEYS[1]) == 0 then return 0 end
        if ARGV[1] ~= '' and redis.call('get', KEYS[7]) ~= ARGV[1] then return 0 end
        if #ARGV > 3 then redis.call('hset', KEYS[1], unpack(ARGV, 3, #ARGV - 1)) end
        local ttl = redis.call('hget', KEYS[1], 'ttl') or ARGV[2]
        redis.call('hset', KEYS[1], 'expires_at', ARGV[#ARGV])
        for i = 1, 6 do redis.call('expire', KEYS[i], ttl) end
        return 1
        """
        return bool(
            self.redis.eval(
                script,
                7,
                *_all_keys(session_id),
                _lock_key(session_id),
                _LOCK_OWNER.get() or "",
                self.default_ttl,
                *encoded,
                self._session_expiry(session_id),
            )
        )

    def _session_expiry(self, session_id: str) -> str:
        ttl = self.redis.hget(_meta_key(session_id), "ttl")
        return _expires_iso(int(ttl) if ttl else self.default_ttl)

    def append_step(self, session_id: str, step: dict) -> int | None:
        """Atomically append history without rewriting the legacy JSON prefix."""
        script = r"""
        -- session_append_step_v1
        if redis.call('exists', KEYS[1]) == 0 then return false end
        if ARGV[5] ~= '' and redis.call('get', KEYS[7]) ~= ARGV[5] then return false end
        local step_count = redis.call('hget', KEYS[1], 'step_count') or '0'
        local next_index = redis.call('hget', KEYS[1], 'next_step_index')
        if not next_index or tonumber(next_index) < tonumber(step_count) then
            next_index = step_count
            redis.call('hset', KEYS[1], 'next_step_index', next_index)
        end
        local index = redis.call('hincrby', KEYS[1], 'next_step_index', 1)
        redis.call('hincrby', KEYS[1], 'step_count', 1)
        local payload = '{"index":' .. index .. ',"timestamp":' .. ARGV[2]
        if ARGV[1] ~= '{}' then
            payload = payload .. ',' .. string.sub(ARGV[1], 2)
        else
            payload = payload .. '}'
        end
        redis.call('rpush', KEYS[5], payload)
        local ttl = redis.call('hget', KEYS[1], 'ttl') or ARGV[4]
        redis.call('hset', KEYS[1], 'expires_at', ARGV[3])
        for i = 1, 6 do redis.call('expire', KEYS[i], ttl) end
        return index
        """
        ttl = self.default_ttl
        result = self.redis.eval(
            script,
            7,
            *_all_keys(session_id),
            _lock_key(session_id),
            json.dumps(
                {k: v for k, v in step.items() if k not in {"index", "timestamp"}}
            ),
            json.dumps(_now_iso()),
            self._session_expiry(session_id),
            ttl,
            _LOCK_OWNER.get() or "",
        )
        return int(result) if result is not None else None

    def get_steps(self, session_id: str) -> list[dict]:
        """Read the legacy prefix and append log in one atomic snapshot."""
        legacy, appended = self.redis.eval(
            """
        -- session_read_steps_v1
        return {redis.call('get', KEYS[1]) or '[]',
                redis.call('lrange', KEYS[2], 0, -1)}
        """,
            2,
            _steps_key(session_id),
            _step_log_key(session_id),
        )
        return [*json.loads(legacy), *(json.loads(item) for item in appended)]

    def append_artifact(self, session_id: str, content: str) -> bool:
        """Append without transferring or rewriting the previous artifact."""
        return self._append_artifact_atomic(session_id, content)

    def get_artifact(self, session_id: str) -> str:
        """Get the full accumulated artifact text."""
        return str(self.redis.get(_artifact_key(session_id)) or "")

    async def _offload(self, function, *args, **kwargs):
        """Bound blocking storage work and drain it before cancellation escapes.

        A cancelled write may have committed. Draining guarantees its caller
        cannot release the session lock while that write is still executing.
        """
        async with self._io_slots:
            task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                while not task.done():
                    try:
                        await asyncio.shield(task)
                    except asyncio.CancelledError:
                        continue
                    except Exception:
                        break
                if not task.cancelled():
                    task.exception()
                raise

    # ── Async storage boundary ─────────────────────────────────

    async def acreate(self, ttl: int | None = None) -> str:
        """Create a session without running blocking Redis I/O on the loop."""
        return await self._offload(self.create, ttl)

    async def aget(self, session_id: str) -> dict | None:
        """Read session metadata off the event loop."""
        return await self._offload(self.get, session_id)

    async def aupdate_meta(self, session_id: str, updates: dict) -> bool:
        return await self._offload(self.update_meta, session_id, updates)

    async def aappend_step(self, session_id: str, step: dict) -> int | None:
        return await self._offload(self.append_step, session_id, step)

    async def aget_steps(self, session_id: str) -> list[dict]:
        return await self._offload(self.get_steps, session_id)

    async def aappend_artifact(self, session_id: str, content: str) -> bool:
        """Append a section atomically and maintain a Unicode length counter.

        ``APPEND`` avoids downloading and rewriting the accumulated artifact.
        The metadata counter uses Python's character length, deliberately
        avoiding Redis ``STRLEN`` byte semantics.  The sync method uses the same atomic append contract.
        """
        return await self._offload(self._append_artifact_atomic, session_id, content)

    def _append_artifact_atomic(self, session_id: str, content: str) -> bool:
        script = r"""
        -- session_append_artifact_v1
        if redis.call('exists', KEYS[1]) == 0 then return 0 end
        if ARGV[5] ~= '' and redis.call('get', KEYS[7]) ~= ARGV[5] then return 0 end
        if not redis.call('hget', KEYS[1], 'artifact_chars') then
            local existing = redis.call('get', KEYS[3]) or ''
            -- Count UTF-8 leading bytes, matching Python's Unicode length.
            local _, chars = string.gsub(existing, "[^\128-\191]", "")
            redis.call('hset', KEYS[1], 'artifact_chars', chars)
        end
        redis.call('append', KEYS[3], ARGV[1])
        redis.call('hincrby', KEYS[1], 'artifact_chars', ARGV[2])
        local ttl = redis.call('hget', KEYS[1], 'ttl') or ARGV[4]
        redis.call('hset', KEYS[1], 'expires_at', ARGV[3])
        for i = 1, 6 do redis.call('expire', KEYS[i], ttl) end
        return 1
        """
        return bool(
            self.redis.eval(
                script,
                7,
                *_all_keys(session_id),
                _lock_key(session_id),
                content,
                len(content),
                self._session_expiry(session_id),
                self.default_ttl,
                _LOCK_OWNER.get() or "",
            )
        )

    async def aget_artifact(self, session_id: str) -> str:
        return await self._offload(self.get_artifact, session_id)

    def export_snapshot(self, session_id: str) -> dict | None:
        """Read session metadata, history, refs, and artifact atomically."""
        raw = self.redis.eval(
            """
        -- session_export_snapshot_v1
        if redis.call('exists', KEYS[1]) == 0 then return {} end
        return {
            cjson.encode(redis.call('hgetall', KEYS[1])),
            redis.call('get', KEYS[2]) or '[]',
            cjson.encode(redis.call('lrange', KEYS[3], 0, -1)),
            redis.call('get', KEYS[4]) or '',
            cjson.encode(redis.call('hgetall', KEYS[5]))
        }
        """,
            5,
            _meta_key(session_id),
            _steps_key(session_id),
            _step_log_key(session_id),
            _artifact_key(session_id),
            _refs_key(session_id),
        )
        if not raw:
            return None
        meta = _decode_hash_json(raw[0])
        meta["step_count"] = int(meta.get("step_count", "0"))
        meta["ttl"] = int(meta.get("ttl", str(self.default_ttl)))
        legacy_steps = json.loads(raw[1])
        appended_steps = json.loads(raw[2])
        steps = [*legacy_steps, *(json.loads(item) for item in appended_steps)]
        artifact = str(raw[3] or "")
        artifact_chars = meta.get("artifact_chars")
        meta["artifact_length"] = (
            int(artifact_chars) if artifact_chars is not None else len(artifact)
        )
        refs = {
            key: json.loads(value) for key, value in _decode_hash_json(raw[4]).items()
        }
        return {"session": meta, "steps": steps, "refs": refs, "artifact": artifact}

    async def aexport_snapshot(self, session_id: str) -> dict | None:
        return await self._offload(self.export_snapshot, session_id)

    async def aadd_ref(self, session_id: str, ref_id: str, ref_data: dict) -> bool:
        return await self.aadd_refs(session_id, {ref_id: ref_data})

    async def aadd_refs(self, session_id: str, refs: dict[str, dict]) -> bool:
        """Commit all refs for one step in one offloaded, pipelined operation."""
        return await self._offload(self.add_refs, session_id, refs)

    def add_refs(self, session_id: str, refs: dict[str, dict]) -> bool:
        """One guarded bulk write; deletion/expiry cannot resurrect metadata."""
        script = """
        -- session_add_refs_v1
        if redis.call('exists', KEYS[1]) == 0 then return 0 end
        if ARGV[3] ~= '' and redis.call('get', KEYS[7]) ~= ARGV[3] then return 0 end
        if #ARGV > 3 then
            redis.call('hset', KEYS[4], unpack(ARGV, 4))
        end
        local ttl = redis.call('hget', KEYS[1], 'ttl') or ARGV[2]
        redis.call('hset', KEYS[1], 'expires_at', ARGV[1])
        for i = 1, 6 do redis.call('expire', KEYS[i], ttl) end
        return 1
        """
        encoded = [
            value for key, ref in refs.items() for value in (key, json.dumps(ref))
        ]
        return bool(
            self.redis.eval(
                script,
                7,
                *_all_keys(session_id),
                _lock_key(session_id),
                self._session_expiry(session_id),
                self.default_ttl,
                _LOCK_OWNER.get() or "",
                *encoded,
            )
        )

    async def aget_ref(self, session_id: str, ref_id: str) -> dict | None:
        return await self._offload(self.get_ref, session_id, ref_id)

    async def aget_refs(self, session_id: str) -> dict:
        return await self._offload(self.get_refs, session_id)

    async def adelete(self, session_id: str) -> bool:
        return await self._offload(self.delete, session_id)

    async def areserve_step(
        self,
        session_id: str,
        idempotency_key: str,
        lease_ttl: int = 120,
        max_pending: int = 8,
        request_fingerprint: str | None = None,
    ) -> dict | None:
        """Reserve an independent step without holding the session lock."""
        return await self._offload(
            self.reserve_step,
            session_id,
            idempotency_key,
            lease_ttl,
            max_pending,
            request_fingerprint,
        )

    def reserve_step(
        self,
        session_id: str,
        idempotency_key: str,
        lease_ttl: int = 120,
        max_pending: int = 8,
        request_fingerprint: str | None = None,
    ) -> dict | None:
        """Reserve a stable step index and idempotency identity atomically."""
        meta_key = _meta_key(session_id)
        idempotency_hash = _idempotency_key(session_id)
        token = str(uuid.uuid4())
        now = int(datetime.now(UTC).timestamp())
        script = """
        if redis.call('exists', KEYS[1]) == 0 then return '' end
        local existing = redis.call('hget', KEYS[2], ARGV[1])
        if existing then
            local item = cjson.decode(existing)
            if ARGV[6] ~= '' and item.fingerprint and
               item.fingerprint ~= ARGV[6] then
                return cjson.encode({status='conflict'})
            end
            if item.status == 'committed' then return existing end
            if tonumber(item.expires_at or 0) > tonumber(ARGV[4]) then return existing end
        end
        if redis.call('exists', KEYS[3]) == 1 then
            return cjson.encode({status='busy'})
        end
        local pending = 0
        for _, raw_item in ipairs(redis.call('hvals', KEYS[2])) do
            local item = cjson.decode(raw_item)
            if item.status == 'pending' and
               tonumber(item.expires_at or 0) > tonumber(ARGV[4]) then
                pending = pending + 1
            end
        end
        if pending >= tonumber(ARGV[5]) then
            return cjson.encode({status='busy'})
        end
        local step_count = redis.call('hget', KEYS[1], 'step_count') or '0'
        local next_index = redis.call('hget', KEYS[1], 'next_step_index')
        if not next_index or tonumber(next_index) < tonumber(step_count) then
            next_index = step_count
            redis.call('hset', KEYS[1], 'next_step_index', next_index)
        end
        local index = redis.call('hincrby', KEYS[1], 'next_step_index', 1)
        local item = cjson.encode({status='pending', token=ARGV[2], index=index,
                                   revision=tonumber(redis.call('hget', KEYS[1], 'revision') or '0'),
                                   expires_at=tonumber(ARGV[4]) + tonumber(ARGV[3]),
                                   fingerprint=ARGV[6]})
        redis.call('hset', KEYS[2], ARGV[1], item)
        local session_ttl = redis.call('hget', KEYS[1], 'ttl') or ARGV[3]
        redis.call('expire', KEYS[2], session_ttl)
        return item
        """
        raw = self.redis.eval(
            script,
            3,
            meta_key,
            idempotency_hash,
            _lock_key(session_id),
            idempotency_key,
            token,
            lease_ttl,
            now,
            max_pending,
            request_fingerprint or "",
        )
        if raw in (None, ""):
            return None
        item = json.loads(raw)
        item.setdefault("idempotency_key", idempotency_key)
        if item.get("status") == "pending" and item.get("token") == token:
            item["acquired"] = True
        return item

    async def acommit_step(
        self,
        session_id: str,
        reservation: dict,
        step: dict,
        refs: dict[str, dict],
        artifact: str,
        result: dict,
        ttl: int | None = None,
    ) -> dict | None:
        return await self._offload(
            self.commit_step,
            session_id,
            reservation,
            step,
            refs,
            artifact,
            result,
            ttl,
        )

    def commit_step(
        self,
        session_id: str,
        reservation: dict,
        step: dict,
        refs: dict[str, dict],
        artifact: str,
        result: dict,
        ttl: int | None = None,
    ) -> dict | None:
        """Publish an independent step atomically after its remote work."""
        meta_key = _meta_key(session_id)
        steps_key = _steps_key(session_id)
        step_log_key = _step_log_key(session_id)
        artifact_key = _artifact_key(session_id)
        refs_key = _refs_key(session_id)
        idempotency_hash = _idempotency_key(session_id)
        if ttl is None:
            ttl_raw = self.redis.hget(meta_key, "ttl")
            effective_ttl = int(ttl_raw) if ttl_raw else self.default_ttl
        else:
            effective_ttl = ttl
        encoded_refs = {ref_id: json.dumps(data) for ref_id, data in refs.items()}
        stored_result = json.dumps(
            {
                "status": "committed",
                "result": result,
                "fingerprint": reservation.get("fingerprint", ""),
            }
        )
        step_json = json.dumps(step)
        refs_args: list[str] = []
        for ref_id, data in encoded_refs.items():
            refs_args.extend((ref_id, data))
        script = """
        if redis.call('exists', KEYS[1]) == 0 then return '' end
        local existing = redis.call('hget', KEYS[5], ARGV[1])
        if not existing then return '' end
        local item = cjson.decode(existing)
        local ref_count = tonumber(ARGV[6])
        local artifact_arg = 7 + (ref_count * 2)
        if (item.fingerprint or '') ~= ARGV[artifact_arg + 6] then return '' end
        if item.status == 'committed' then return existing end
        if item.token ~= ARGV[2] then return '' end
        if tonumber(item.expires_at or 0) <= tonumber(ARGV[artifact_arg + 5]) then
            return ''
        end
        local revision = tonumber(redis.call('hget', KEYS[1], 'revision') or '0')
        if revision < tonumber(ARGV[4]) then return '' end
        local step = cjson.decode(ARGV[5])
        redis.call('rpush', KEYS[6], ARGV[5])
        local arg = 7
        for i = 1, ref_count do
            redis.call('hset', KEYS[4], ARGV[arg], ARGV[arg + 1])
            arg = arg + 2
        end
        if not redis.call('hget', KEYS[1], 'artifact_chars') then
            local existing_artifact = redis.call('get', KEYS[3]) or ''
            local _, chars = string.gsub(existing_artifact, "[^\\128-\\191]", "")
            redis.call('hset', KEYS[1], 'artifact_chars', chars)
        end
        redis.call('append', KEYS[3], ARGV[artifact_arg])
        redis.call('hincrby', KEYS[1], 'step_count', 1)
        redis.call('hincrby', KEYS[1], 'artifact_chars', tonumber(ARGV[artifact_arg + 1]))
        redis.call('hset', KEYS[1], 'revision', revision + 1,
                   'expires_at', ARGV[artifact_arg + 3])
        redis.call('hset', KEYS[5], ARGV[1], ARGV[artifact_arg + 2])
        for i = 1, 6 do redis.call('expire', KEYS[i], ARGV[artifact_arg + 4]) end
        return ARGV[artifact_arg + 2]
        """
        # ARGV layout after refs: artifact, character delta, result, expires_at,
        # and the fixed TTL. Build the prefix and append values, then let Lua
        # index from the ref count.
        args = [
            reservation["idempotency_key"],
            reservation["token"],
            str(effective_ttl),
            str(reservation["revision"]),
            step_json,
            str(len(encoded_refs)),
            *refs_args,
            artifact,
            str(len(artifact)),
            stored_result,
            _expires_iso(effective_ttl),
            str(effective_ttl),
            str(int(datetime.now(UTC).timestamp())),
            reservation.get("fingerprint", ""),
        ]
        raw = self.redis.eval(
            script,
            6,
            meta_key,
            steps_key,
            artifact_key,
            refs_key,
            idempotency_hash,
            step_log_key,
            *args,
        )
        if raw in (None, ""):
            return None
        decoded = json.loads(raw)
        return (
            decoded.get("result") if decoded.get("status") == "committed" else decoded
        )

    async def arelease_step(self, session_id: str, reservation: dict) -> None:
        await self._offload(self.release_step, session_id, reservation)

    async def ahas_pending_steps(self, session_id: str) -> bool:
        return await self._offload(self.has_pending_steps, session_id)

    def has_pending_steps(self, session_id: str) -> bool:
        """Return whether an unexpired independent reservation is active."""
        now = int(datetime.now(UTC).timestamp())
        for raw_item in self.redis.hgetall(_idempotency_key(session_id)).values():
            item = json.loads(raw_item)
            if item.get("status") == "pending" and int(item.get("expires_at", 0)) > now:
                return True
        return False

    def release_step(self, session_id: str, reservation: dict) -> None:
        """Release only this caller's pending idempotency reservation."""
        script = """
        local existing = redis.call('hget', KEYS[1], ARGV[1])
        if existing and cjson.decode(existing).status == 'pending' and
           cjson.decode(existing).token == ARGV[2] then
            redis.call('hdel', KEYS[1], ARGV[1])
        end
        return 1
        """
        self.redis.eval(
            script,
            1,
            _idempotency_key(session_id),
            reservation["idempotency_key"],
            reservation["token"],
        )

    # ── Reference Storage (HSET-based) ──────────────────────────

    def add_ref(self, session_id: str, ref_id: str, ref_data: dict) -> bool:
        """Store a reference (scraped content) by ref ID.

        ``ref_id`` is typically ``ref_{step}_{source}`` (e.g., ``ref_0_2``).
        Each ref is stored as an individual HSET field, allowing O(1)
        single-ref lookup without deserialising the entire refs collection.

        Returns False if the session does not exist.
        """
        return self.add_refs(session_id, {ref_id: ref_data})

    def get_ref(self, session_id: str, ref_id: str) -> dict | None:
        """Get a single reference by ref ID.

        Uses ``HGET`` for O(1) single-ref lookup.  Returns None if
        the ref or session does not exist.
        """
        raw = self.redis.hget(_refs_key(session_id), ref_id)
        if raw is None:
            return None
        return json.loads(raw)  # type: ignore[no-any-return]

    def get_refs(self, session_id: str) -> dict:
        """Get all references for a session.

        Returns a dict of ``{ref_id: ref_data}``.  Each ref_data is
        deserialised from its JSON HSET value.
        """
        raw = self.redis.hgetall(_refs_key(session_id))
        if not raw:
            return {}
        return {k: json.loads(v) for k, v in raw.items()}  # type: ignore[no-any-return]

    # ── Lifecycle ───────────────────────────────────────────────

    def delete(self, session_id: str) -> bool:
        """Delete a session and all its keys (meta, steps, artifact, refs, lock).

        Returns True if the session existed and was deleted.
        """
        keys = [
            *_all_keys(session_id),
            _lock_key(session_id),
        ]
        deleted = self.redis.delete(*keys)
        return deleted > 0

    def cleanup_expired(self) -> int:
        """Clean up expired session keys.

        Since Valkey handles TTL expiry automatically, this is a no-op
        for standard expiry.  Returns 0 — all cleanup is done by Valkey's
        built-in expiration.
        """
        return 0

    # ── Per-Session Locking ─────────────────────────────────────

    async def acquire_lock(
        self, session_id: str, timeout: int = 30, lease_ttl: int = 120
    ) -> str | None:
        """Acquire a per-session lock for concurrent step execution.

        Uses ``SETNX`` with async blocking retry so that concurrent steps
        on the same session are serialised rather than rejected.  The lock
        value is an ownership token (UUID) used for compare-and-delete on
        release, preventing accidental deletion of another caller's lock
        after lease expiry.

        Per ADR-0040: "If two steps race on the same session, the second
        waits for the first's step to complete."  This implementation
        blocks with exponential backoff (up to 50ms max delay) using
        ``asyncio.sleep`` to avoid blocking the FastAPI event loop.

        Args:
            session_id: The session to lock.
            timeout: Maximum time in seconds to wait for the lock
                (default 30).  Only affects how long we block before
                giving up.
            lease_ttl: How long the lock key lives in Valkey before
                auto-expiring (default 120).  Must be longer than the
                longest expected step (e.g. scrape timeout = 70s).

        Returns:
            The ownership token (str) if the lock was acquired, or
            ``None`` if the timeout was exceeded without acquiring
            the lock.
        """
        import time as _time
        import uuid as _uuid

        owner_token = str(_uuid.uuid4())
        deadline = _time.monotonic() + timeout
        backoff = 0.005  # start at 5ms
        max_backoff = 0.05  # cap at 50ms

        while _time.monotonic() < deadline:
            try:
                acquired = await self._offload(
                    self.redis.set,
                    _lock_key(session_id),
                    owner_token,
                    nx=True,
                    ex=lease_ttl,
                )
            except asyncio.CancelledError:
                # SET may have completed while cancellation was delivered.
                # Compare-and-delete only this caller's ownership token.
                await self.arelease_lock(session_id, owner_token)
                raise
            if acquired:
                return owner_token
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, max_backoff)

        return None

    def release_lock(self, session_id: str, owner_token: str) -> None:
        """Release the per-session lock using compare-and-delete.

        Only deletes the lock key if its current value matches
        *owner_token*, preventing accidental deletion of another
        caller's lock after lease expiry and re-acquisition.

        Args:
            session_id: The session to unlock.
            owner_token: The ownership token returned by
                :meth:`acquire_lock`.
        """
        lock_key = _lock_key(session_id)
        # Lua script for atomic compare-and-delete
        script = """
        if redis.call("get", KEYS[1]) == ARGV[1] then
            return redis.call("del", KEYS[1])
        else
            return 0
        end
        """
        self.redis.eval(script, 1, lock_key, owner_token)

    async def arelease_lock(self, session_id: str, owner_token: str) -> None:
        """Release a lock without blocking the event loop."""
        await self._offload(self.release_lock, session_id, owner_token)

    def renew_lock(self, session_id: str, owner_token: str, lease_ttl: int) -> bool:
        """Extend a lock lease only while *owner_token* still owns it."""
        return bool(
            self.redis.eval(
                """
                if redis.call('get', KEYS[1]) ~= ARGV[1] then return 0 end
                return redis.call('expire', KEYS[1], ARGV[2])
                """,
                1,
                _lock_key(session_id),
                owner_token,
                lease_ttl,
            )
        )

    async def arenew_lock(
        self, session_id: str, owner_token: str, lease_ttl: int
    ) -> bool:
        """Renew a lock lease without blocking the event loop."""
        return await self._offload(self.renew_lock, session_id, owner_token, lease_ttl)

    def is_locked(self, session_id: str) -> bool:
        """Check whether the session lock is currently held."""
        return bool(self.redis.exists(_lock_key(session_id)))

    # ── TTL Management ──────────────────────────────────────────

    def _refresh_ttl(self, session_id: str, ttl: int) -> None:
        """Refresh TTL on all session keys (meta, steps, artifact, refs).

        Called after every write operation to reset the idle timeout.
        """
        for key in _all_keys(session_id):
            self.redis.expire(key, ttl)
