"""Job CRUD operations backed by Valkey.

Stores job metadata, status, and results in Valkey key-value store.
Uses the same valkey connection for both the API and the worker.
Atomic progress updates use Valkey INCR to prevent lost increments
under concurrency (VAL-CONC-042).
"""

import json
import uuid
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from typing import Any

from redis import Redis

_EXECUTION_CONTEXT: ContextVar[tuple[str, str, int] | None] = ContextVar(
    "groktocrawl_job_execution_context", default=None
)
_WEBHOOK_STORE_CONTEXT: ContextVar[Any | None] = ContextVar(
    "groktocrawl_webhook_store_context", default=None
)

_ENQUEUE_WEBHOOK_SCRIPT = """
local existing = redis.call('GET', KEYS[2])
if existing then return redis.call('HGET', KEYS[1], existing) end
redis.call('HSET', KEYS[1], ARGV[1], ARGV[2])
redis.call('SET', KEYS[2], ARGV[1], 'EX', ARGV[3])
redis.call('EXPIRE', KEYS[1], ARGV[3])
return ARGV[2]
"""
_CLAIM_WEBHOOK_SCRIPT = """
local raw = redis.call('HGET', KEYS[1], ARGV[1])
if not raw then return nil end
local event = cjson.decode(raw)
if event.status == 'delivered' or event.status == 'abandoned' then return nil end
if event.lease_owner and event.lease_owner ~= cjson.null and tonumber(event.lease_expires_at or 0) > tonumber(ARGV[3]) then return nil end
event.lease_owner = ARGV[2]
event.lease_expires_at = tonumber(ARGV[3]) + tonumber(ARGV[4])
event.status = 'delivering'
redis.call('HSET', KEYS[1], ARGV[1], cjson.encode(event))
return cjson.encode(event)
"""
_FINISH_WEBHOOK_SCRIPT = """
local raw = redis.call('HGET', KEYS[1], ARGV[1])
if not raw then return 0 end
local event = cjson.decode(raw)
if event.lease_owner ~= ARGV[2] then return 0 end
event.lease_owner = nil
event.lease_expires_at = 0
event.attempts = tonumber(event.attempts or 0) + 1
if ARGV[3] == 'delivered' or ARGV[3] == 'abandoned' then
 event.status = ARGV[3]
else
 event.status = 'pending'
end
event.last_error = ARGV[4]
redis.call('HSET', KEYS[1], ARGV[1], cjson.encode(event))
return 1
"""

_COMPLETE_FENCED_SCRIPT = """
local raw = redis.call('GET', KEYS[1])
if not raw then return 0 end
local meta = cjson.decode(raw)
local e = meta.execution
if (meta.status ~= 'processing' and meta.status ~= 'retry_scheduled') or type(e) ~= 'table' then return 0 end
if e.lease_owner ~= ARGV[1] or tonumber(e.fence) ~= tonumber(ARGV[2]) then return 0 end
if tonumber(e.lease_expires_at or 0) <= tonumber(ARGV[3]) then return 0 end
meta.status = 'completed'
meta.completed_at = ARGV[4]
e.state = 'completed'
e.pending_operation = nil
e.lease_owner = nil
e.lease_expires_at = 0
e.checkpoint = {kind='terminal', status='completed', at=ARGV[4]}
local data = cjson.decode(ARGV[5])
data.completed = tonumber(redis.call('GET', KEYS[3]) or data.completed or 0)
redis.call('SET', KEYS[1], cjson.encode(meta), 'KEEPTTL')
redis.call('SET', KEYS[2], cjson.encode(data), 'EX', ARGV[6])
if ARGV[7] ~= '' and not redis.call('GET', KEYS[5]) then
 redis.call('HSET', KEYS[4], ARGV[8], ARGV[9])
 redis.call('SET', KEYS[5], ARGV[8], 'EX', ARGV[6])
 redis.call('EXPIRE', KEYS[4], ARGV[6])
end
return 1
"""
_FAIL_FENCED_SCRIPT = """
local raw = redis.call('GET', KEYS[1])
if not raw then return 0 end
local meta = cjson.decode(raw)
local e = meta.execution
if (meta.status ~= 'processing' and meta.status ~= 'retry_scheduled') or type(e) ~= 'table' then return 0 end
if e.lease_owner ~= ARGV[1] or tonumber(e.fence) ~= tonumber(ARGV[2]) then return 0 end
if tonumber(e.lease_expires_at or 0) <= tonumber(ARGV[3]) then return 0 end
meta.status = 'failed'
meta.error = ARGV[4]
meta.completed_at = ARGV[5]
e.state = 'failed'
e.pending_operation = nil
e.lease_owner = nil
e.lease_expires_at = 0
e.checkpoint = {kind='terminal', status='failed', at=ARGV[5]}
redis.call('SET', KEYS[1], cjson.encode(meta), 'KEEPTTL')
if ARGV[7] ~= '' and not redis.call('GET', KEYS[3]) then
 redis.call('HSET', KEYS[2], ARGV[8], ARGV[9])
 redis.call('SET', KEYS[3], ARGV[8], 'EX', ARGV[6])
 redis.call('EXPIRE', KEYS[2], ARGV[6])
end
return 1
"""
_CANCEL_SCRIPT = """
local raw = redis.call('GET', KEYS[1])
if not raw then return 0 end
local meta = cjson.decode(raw)
if meta.status ~= 'processing' and meta.status ~= 'retry_scheduled' then return 0 end
meta.status = 'cancelled'
meta.completed_at = ARGV[1]
local e = meta.execution
if type(e) == 'table' then
  e.state = 'cancelled'
  e.fence = tonumber(e.fence or 0) + 1
  e.lease_owner = nil
  e.lease_expires_at = 0
  e.pending_operation = nil
  e.checkpoint = {kind='terminal', status='cancelled', at=ARGV[1]}
end
redis.call('SET', KEYS[1], cjson.encode(meta), 'KEEPTTL')
if ARGV[3] ~= '' and not redis.call('GET', KEYS[3]) then
 redis.call('HSET', KEYS[2], ARGV[4], ARGV[5])
 redis.call('SET', KEYS[3], ARGV[4], 'EX', ARGV[2])
 redis.call('EXPIRE', KEYS[2], ARGV[2])
end
return 1
"""
_CREATE_PLAN_JOB_SCRIPT = """
local plan = redis.call('GET', KEYS[1])
if not plan then return nil end
redis.call('SET', KEYS[2], ARGV[1], 'EX', ARGV[2])
redis.call('SET', KEYS[3], 0, 'EX', ARGV[2])
redis.call('DEL', KEYS[1])
return plan
"""


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _expires_iso() -> str:
    """Return ISO 8601 timestamp for 24 hours from now."""
    return (datetime.now(UTC) + timedelta(hours=24)).isoformat()


def _default_ttl() -> int:
    """Jobs expire after 24 hours."""
    return 86400


# Key for atomic completed counter
_COMPLETED_KEY = "job:{id}:completed"


class JobStore:
    """Simple Valkey-backed job store.

    Key schema:
      job:{id}:meta  -> JSON with status, created_at, etc.
      job:{id}:data  -> JSON with result data (set on completion)
      job:{id}:completed  -> INTEGER with atomic completed count (INCR)
    """

    def __init__(self, redis_url: str = "redis://localhost:6379/0"):
        self.redis = Redis.from_url(redis_url, decode_responses=True)

    def create_job(self, kind: str = "agent", payload: dict | None = None) -> str:
        """Create a new job and return its ID."""
        job_id = str(uuid.uuid4())
        meta = {
            "id": job_id,
            "kind": kind,
            "status": "processing",
            "created_at": _now_iso(),
            "expires_at": _expires_iso(),
            "payload": payload or {},
            # The public status remains ``processing`` for API compatibility;
            # this internal state makes ownership recoverable across process
            # loss. A new job is safe to dispatch until a worker claims it.
            "execution": {
                "version": 1,
                "state": "queued",
                "fence": 0,
                "lease_owner": None,
                "lease_expires_at": None,
                "pending_operation": None,
                "checkpoint": None,
            },
        }
        self.redis.set(f"job:{job_id}:meta", json.dumps(meta), ex=_default_ttl())
        # Initialize atomic completed counter
        self.redis.set(_COMPLETED_KEY.format(id=job_id), 0, ex=_default_ttl())
        return job_id

    def create_plan_execution_job(self, plan_id: str, payload: dict) -> str | None:
        """Atomically consume a plan while creating its durable execution job."""
        job_id = str(uuid.uuid4())
        meta = {
            "id": job_id,
            "kind": "plan_execute",
            "status": "processing",
            "created_at": _now_iso(),
            "expires_at": _expires_iso(),
            "payload": payload,
            "execution": {
                "version": 1,
                "state": "queued",
                "fence": 0,
                "lease_owner": None,
                "lease_expires_at": None,
                "pending_operation": None,
                "checkpoint": None,
            },
        }
        raw_plan = self.redis.eval(
            _CREATE_PLAN_JOB_SCRIPT,
            3,
            f"plan:{plan_id}",
            f"job:{job_id}:meta",
            _COMPLETED_KEY.format(id=job_id),
            json.dumps(meta),
            _default_ttl(),
        )
        return job_id if raw_plan is not None else None

    def increment_completed(self, job_id: str) -> int:
        """Atomically increment the completed page count for a crawl job.

        Uses Valkey ``INCR`` which is atomic and immune to read-modify-write
        races. Returns the new count after increment.

        The completed key is initialized to 0 in ``create_job()``, so an
        ``INCR`` is safe even on first call (the TTL is preserved from
        creation time).
        """
        return self.redis.incr(_COMPLETED_KEY.format(id=job_id))

    def get_completed(self, job_id: str) -> int:
        """Get the current atomic completed count for a job.

        Returns 0 if the key does not exist (e.g., job was never created
        or has expired).
        """
        val = self.redis.get(_COMPLETED_KEY.format(id=job_id))
        if val is None:
            return 0
        return int(val)

    def update_job_progress(
        self,
        job_id: str,
        pages: list[dict] | None = None,
        total: int | None = None,
        errors: list[dict] | None = None,
        robots_blocked: list[dict] | None = None,
        filtered_out: list[dict] | None = None,
    ) -> None:
        """Update the job data key with current crawl progress.

        Uses Valkey ``GETSET`` on the data key so that concurrent calls
        do not lose pages/errors. The completed count is maintained
        separately via ``increment_completed()`` so that it is never
        overwritten by a stale data payload.

        Args:
            job_id: The crawl job ID.
            pages: Current list of successfully scraped pages.
            total: Current total (scraped + queued).
            errors: Current list of error entries.
            robots_blocked: Current list of politeness-blocked entries.
            filtered_out: Current list of filtered-out URL entries.
        """
        ttl = _default_ttl()
        data_key = f"job:{job_id}:data"
        completed = self.get_completed(job_id)

        payload: dict = {
            "completed": completed,
            "pages": pages or [],
            "errors": errors or [],
            "robots_blocked": robots_blocked or [],
        }
        if total is not None:
            payload["total"] = total
        if filtered_out is not None:
            payload["filtered_out"] = filtered_out

        # Use SET (not GETSET) — we build the full payload each time,
        # but the completed count is sourced from the atomic counter,
        # not from the list length.
        self.redis.set(data_key, json.dumps(payload), ex=ttl)

    def get_job(self, job_id: str) -> dict | None:
        """Get job metadata. Returns None if not found."""
        raw = self.redis.get(f"job:{job_id}:meta")
        if raw is None:
            return None
        meta = json.loads(raw)
        # Attach data if available
        data_raw = self.redis.get(f"job:{job_id}:data")
        if data_raw:
            meta["data"] = json.loads(data_raw)
        return meta  # type: ignore[no-any-return]

    def complete_job(
        self, job_id: str, data: dict, webhook_event: dict | None = None
    ) -> bool:
        """Mark a job as completed with its result data.

        Only transitions ``processing`` → ``completed``. If the current
        status is ``cancelled``, ``failed``, or already ``completed``,
        the status is left unchanged. This prevents a race where a
        concurrent ``cancel_job()`` (e.g., via ``DELETE /v2/crawl/{id}``)
        is silently overwritten.
        """
        context = _EXECUTION_CONTEXT.get()
        if context is not None and context[0] == job_id:
            result = self.redis.eval(
                _COMPLETE_FENCED_SCRIPT,
                5,
                f"job:{job_id}:meta",
                f"job:{job_id}:data",
                _COMPLETED_KEY.format(id=job_id),
                self._webhook_key(job_id),
                f"job:{job_id}:webhook-fingerprint:{webhook_event['fingerprint']}"
                if webhook_event
                else f"job:{job_id}:webhook-fingerprint:none",
                context[1],
                context[2],
                __import__("time").time(),
                _now_iso(),
                json.dumps(data),
                _default_ttl(),
                webhook_event["fingerprint"] if webhook_event else "",
                webhook_event["event"]["webhookId"] if webhook_event else "",
                json.dumps(webhook_event["event"]) if webhook_event else "",
            )
            if isinstance(result, int):
                return result == 1
        meta_raw = self.redis.get(f"job:{job_id}:meta")
        if meta_raw is None:
            return False
        meta = json.loads(meta_raw)
        if meta["status"] != "processing":
            # Job was cancelled, failed, or already completed concurrently.
            # Preserve the existing terminal status — do not overwrite.
            return False
        meta["status"] = "completed"
        meta["completed_at"] = _now_iso()
        self.redis.set(f"job:{job_id}:meta", json.dumps(meta), ex=_default_ttl())
        # Ensure the final data payload has the correct completed count
        data["completed"] = self.get_completed(job_id)
        self.redis.set(f"job:{job_id}:data", json.dumps(data), ex=_default_ttl())
        if webhook_event:
            self.enqueue_webhook(
                job_id, webhook_event["fingerprint"], webhook_event["event"]
            )
        return True

    def overwrite_job_data(self, job_id: str, data: dict) -> None:
        """Overwrite the data payload of an already-completed job.

        Used by stale-while-revalidate so the existing ``GET /v2/agent/{id}``
        job handle reflects the refreshed result without changing the job's
        terminal ``completed`` status.
        """
        data["completed"] = self.get_completed(job_id)
        self.redis.set(f"job:{job_id}:data", json.dumps(data), ex=_default_ttl())

    def fail_job(
        self, job_id: str, error: str, webhook_event: dict | None = None
    ) -> bool:
        """Mark a job as failed with an error message.

        Only transitions ``processing`` or ``retry_scheduled`` → ``failed``.
        If the current status is ``cancelled`` or already terminal, the
        status is left unchanged. This prevents a race where a concurrent
        ``cancel_job()`` is silently overwritten with ``failed``.
        """
        context = _EXECUTION_CONTEXT.get()
        if context is not None and context[0] == job_id:
            result = self.redis.eval(
                _FAIL_FENCED_SCRIPT,
                3,
                f"job:{job_id}:meta",
                self._webhook_key(job_id),
                f"job:{job_id}:webhook-fingerprint:{webhook_event['fingerprint']}"
                if webhook_event
                else f"job:{job_id}:webhook-fingerprint:none",
                context[1],
                context[2],
                __import__("time").time(),
                error,
                _now_iso(),
                _default_ttl(),
                webhook_event["fingerprint"] if webhook_event else "",
                webhook_event["event"]["webhookId"] if webhook_event else "",
                json.dumps(webhook_event["event"]) if webhook_event else "",
            )
            if isinstance(result, int):
                return result == 1
        meta_raw = self.redis.get(f"job:{job_id}:meta")
        if meta_raw is None:
            return False
        meta = json.loads(meta_raw)
        if meta["status"] not in ("processing", "retry_scheduled"):
            # Preserve existing terminal status (e.g., cancelled).
            return False
        meta["status"] = "failed"
        meta["error"] = error
        meta["completed_at"] = _now_iso()
        self.redis.set(f"job:{job_id}:meta", json.dumps(meta), ex=_default_ttl())
        if webhook_event:
            self.enqueue_webhook(
                job_id, webhook_event["fingerprint"], webhook_event["event"]
            )
        return True

    def cancel_job(self, job_id: str, webhook_event: dict | None = None) -> bool:
        """Cancel a job that's still processing or waiting to retry.

        Returns True if cancelled. A job waiting in ``retry_scheduled``
        is cancelled so no further retry starts (ADR-0053 AC-003.6).
        """
        result = self.redis.eval(
            _CANCEL_SCRIPT,
            3,
            f"job:{job_id}:meta",
            self._webhook_key(job_id),
            f"job:{job_id}:webhook-fingerprint:{webhook_event['fingerprint']}"
            if webhook_event
            else f"job:{job_id}:webhook-fingerprint:none",
            _now_iso(),
            _default_ttl(),
            webhook_event["fingerprint"] if webhook_event else "",
            webhook_event["event"]["webhookId"] if webhook_event else "",
            json.dumps(webhook_event["event"]) if webhook_event else "",
        )
        if isinstance(result, int):
            return result == 1
        meta_raw = self.redis.get(f"job:{job_id}:meta")
        if meta_raw is None:
            return False
        meta = json.loads(meta_raw)
        if meta["status"] not in ("processing", "retry_scheduled"):
            return False
        meta["status"] = "cancelled"
        meta["completed_at"] = _now_iso()
        self.redis.set(f"job:{job_id}:meta", json.dumps(meta), ex=_default_ttl())
        if webhook_event:
            self.enqueue_webhook(
                job_id, webhook_event["fingerprint"], webhook_event["event"]
            )
        return True

    def schedule_retry(
        self,
        job_id: str,
        *,
        retry_at: str,
        retry_attempt: int,
        retry_limit: int,
        reason: str,
        retry_after_seconds: float,
    ) -> bool:
        """Transition a processing job into the non-terminal retry state.

        Stores the retry metadata (``retry_at``, ``retry_attempt``,
        ``retry_limit``, ``retry_reason``, ``retry_after_seconds``) on
        the job meta so ``GET`` status endpoints can expose it. The job
        is NOT failed and no terminal event is emitted.

        Returns ``True`` when the transition happened; ``False`` when the
        job was already cancelled, failed, completed, or does not exist
        (the caller must not schedule a retry in those cases).
        """
        meta_raw = self.redis.get(f"job:{job_id}:meta")
        if meta_raw is None:
            return False
        meta = json.loads(meta_raw)
        if meta["status"] != "processing":
            return False
        meta["status"] = "retry_scheduled"
        meta["retry_at"] = retry_at
        meta["retry_attempt"] = retry_attempt
        meta["retry_limit"] = retry_limit
        meta["retry_reason"] = reason
        meta["retry_after_seconds"] = retry_after_seconds
        self.redis.set(f"job:{job_id}:meta", json.dumps(meta), ex=_default_ttl())
        return True

    def resume_retry(self, job_id: str) -> bool:
        """Transition ``retry_scheduled`` → ``processing`` when a retry begins.

        No-op (returns ``False``) when the job was cancelled while
        waiting (``DELETE`` wins over the retry wakeup) or the job no
        longer exists. Only the owning worker task calls this, so at most
        one retry is ever claimed per job.
        """
        meta_raw = self.redis.get(f"job:{job_id}:meta")
        if meta_raw is None:
            return False
        meta = json.loads(meta_raw)
        if meta["status"] != "retry_scheduled":
            return False
        meta["status"] = "processing"
        self.redis.set(f"job:{job_id}:meta", json.dumps(meta), ex=_default_ttl())
        return True

    def delete_completed_counter(self, job_id: str) -> None:
        """Delete the atomic completed counter for a job.

        Called during cleanup to remove the auxiliary counter key.
        """
        self.redis.delete(_COMPLETED_KEY.format(id=job_id))

    def list_active_jobs(
        self, kind: str | None = None, status: str = "processing", limit: int = 50
    ) -> list[dict]:
        """List jobs by status, optionally filtered by kind.

        Uses Valkey SCAN with pattern ``job:*:meta`` — no dedicated index.
        For production at scale, replace with a sorted set or dedicated index.

        Args:
            kind: If set, only return jobs of this kind (``crawl``, ``agent``, etc.)
            status: Status filter (default ``processing``)
            limit: Maximum jobs to return (default 50)

        Returns:
            List of job metadata dicts (without attached data payloads).
        """
        active: list[dict] = []
        cursor = 0
        while len(active) < limit:
            cursor, keys = self.redis.scan(cursor=cursor, match="job:*:meta", count=100)
            if not keys:
                if cursor == 0:
                    break
                continue
            pipe = self.redis.pipeline()
            for key in keys:
                pipe.get(key)
            results = pipe.execute()
            for raw in results:
                if raw is None:
                    continue
                meta = json.loads(raw)
                if meta.get("status") != status:
                    continue
                if kind is not None and meta.get("kind") != kind:
                    continue
                active.append(meta)
                if len(active) >= limit:
                    return active
            if cursor == 0:
                break
        return active

    def list_processing_jobs(self, limit: int = 500) -> list[dict]:
        """Return bounded processing records for startup recovery scans."""
        jobs: list[dict] = []
        cursor = 0
        while len(jobs) < limit:
            cursor, keys = self.redis.scan(cursor=cursor, match="job:*:meta", count=100)
            if keys:
                pipe = self.redis.pipeline()
                for key in keys:
                    pipe.get(key)
                for raw in pipe.execute():
                    if raw is None:
                        continue
                    meta = json.loads(raw)
                    if meta.get("status") in ("processing", "retry_scheduled"):
                        jobs.append(meta)
                        if len(jobs) >= limit:
                            return jobs
            if cursor == 0:
                break
        return jobs

    @staticmethod
    def _webhook_key(job_id: str) -> str:
        return f"job:{job_id}:webhooks"

    def enqueue_webhook(self, job_id: str, fingerprint: str, event: dict) -> dict:
        """Persist a webhook event before network delivery; return canonical event."""
        webhook_id = str(event["webhookId"])
        raw = json.dumps({**event, "status": "pending", "attempts": 0})
        result = self.redis.eval(
            _ENQUEUE_WEBHOOK_SCRIPT,
            2,
            self._webhook_key(job_id),
            f"job:{job_id}:webhook-fingerprint:{fingerprint}",
            webhook_id,
            raw,
            _default_ttl(),
        )
        return json.loads(result)

    def claim_webhook(
        self, job_id: str, webhook_id: str, owner: str, lease: int = 30
    ) -> dict | None:
        result = self.redis.eval(
            _CLAIM_WEBHOOK_SCRIPT,
            1,
            self._webhook_key(job_id),
            webhook_id,
            owner,
            datetime.now(UTC).timestamp(),
            lease,
        )
        return json.loads(result) if result else None

    def finish_webhook(
        self,
        job_id: str,
        webhook_id: str,
        owner: str,
        status: str,
        error: str | None = None,
    ) -> bool:
        result = self.redis.eval(
            _FINISH_WEBHOOK_SCRIPT,
            1,
            self._webhook_key(job_id),
            webhook_id,
            owner,
            status,
            error or "",
        )
        return result == 1

    def list_pending_webhooks(self, limit: int = 500) -> list[dict]:
        events: list[dict] = []
        cursor = 0
        while len(events) < limit:
            cursor, keys = self.redis.scan(
                cursor=cursor, match="job:*:webhooks", count=100
            )
            for key in keys:
                job_id = key.split(":", 2)[1]
                for raw in self.redis.hgetall(key).values():
                    event = json.loads(raw)
                    if event.get("status") in ("pending", "delivering"):
                        event["job_id"] = job_id
                        events.append(event)
                        if len(events) >= limit:
                            return events
            if cursor == 0:
                break
        return events

    def count_active_jobs(self, kind: str | None = None) -> int:
        """Count the number of jobs currently in 'processing' status.

        Uses the same SCAN-based approach as list_active_jobs but
        only returns the count rather than the full metadata list.

        Args:
            kind: If set, only count jobs of this kind (``crawl``, ``agent``, etc.)

        Returns:
            Number of active processing jobs.
        """
        count = 0
        cursor = 0
        while True:
            cursor, keys = self.redis.scan(cursor=cursor, match="job:*:meta", count=100)
            if keys:
                pipe = self.redis.pipeline()
                for key in keys:
                    pipe.get(key)
                results = pipe.execute()
                for raw in results:
                    if raw is None:
                        continue
                    meta = json.loads(raw)
                    if meta.get("status") != "processing":
                        continue
                    if kind is not None and meta.get("kind") != kind:
                        continue
                    count += 1
            if cursor == 0:
                break
        return count
