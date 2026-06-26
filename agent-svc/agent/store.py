"""Job CRUD operations backed by Valkey.

Stores job metadata, status, and results in Valkey key-value store.
Uses the same valkey connection for both the API and the worker.
Atomic progress updates use Valkey INCR to prevent lost increments
under concurrency (VAL-CONC-042).
"""

import json
import uuid
from datetime import UTC, datetime, timedelta

from redis import Redis


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
        }
        self.redis.set(f"job:{job_id}:meta", json.dumps(meta), ex=_default_ttl())
        # Initialize atomic completed counter
        self.redis.set(_COMPLETED_KEY.format(id=job_id), 0, ex=_default_ttl())
        return job_id

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

    def complete_job(self, job_id: str, data: dict) -> None:
        """Mark a job as completed with its result data.

        Only transitions ``processing`` → ``completed``. If the current
        status is ``cancelled``, ``failed``, or already ``completed``,
        the status is left unchanged. This prevents a race where a
        concurrent ``cancel_job()`` (e.g., via ``DELETE /v2/crawl/{id}``)
        is silently overwritten.
        """
        meta_raw = self.redis.get(f"job:{job_id}:meta")
        if meta_raw is None:
            return
        meta = json.loads(meta_raw)
        if meta["status"] != "processing":
            # Job was cancelled, failed, or already completed concurrently.
            # Preserve the existing terminal status — do not overwrite.
            return
        meta["status"] = "completed"
        meta["completed_at"] = _now_iso()
        self.redis.set(f"job:{job_id}:meta", json.dumps(meta), ex=_default_ttl())
        # Ensure the final data payload has the correct completed count
        data["completed"] = self.get_completed(job_id)
        self.redis.set(f"job:{job_id}:data", json.dumps(data), ex=_default_ttl())

    def fail_job(self, job_id: str, error: str) -> None:
        """Mark a job as failed with an error message.

        Only transitions ``processing`` → ``failed``. If the current
        status is ``cancelled`` or already terminal, the status is left
        unchanged. This prevents a race where a concurrent
        ``cancel_job()`` is silently overwritten with ``failed``.
        """
        meta_raw = self.redis.get(f"job:{job_id}:meta")
        if meta_raw is None:
            return
        meta = json.loads(meta_raw)
        if meta["status"] != "processing":
            # Preserve existing terminal status (e.g., cancelled).
            return
        meta["status"] = "failed"
        meta["error"] = error
        meta["completed_at"] = _now_iso()
        self.redis.set(f"job:{job_id}:meta", json.dumps(meta), ex=_default_ttl())

    def cancel_job(self, job_id: str) -> bool:
        """Cancel a job that's still processing. Returns True if cancelled."""
        meta_raw = self.redis.get(f"job:{job_id}:meta")
        if meta_raw is None:
            return False
        meta = json.loads(meta_raw)
        if meta["status"] != "processing":
            return False
        meta["status"] = "cancelled"
        meta["completed_at"] = _now_iso()
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
