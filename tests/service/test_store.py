"""Tests for agent-svc/agent/store.py — JobStore backed by a fake Redis."""

import json
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def fake_redis():
    """Return mocks that mimic Redis basic operations including scan/pipeline."""
    store_data = {}

    r = MagicMock()
    r.set = MagicMock(
        side_effect=lambda key, val, **kw: store_data.update({key: val}) or True
    )
    r.get = MagicMock(side_effect=lambda key: store_data.get(key))

    def _incr(key):
        current = store_data.get(key)
        if current is None:
            current = "0"
        new_val = str(int(current) + 1)
        store_data[key] = new_val
        return int(new_val)

    r.incr = MagicMock(side_effect=_incr)
    r.delete = MagicMock(side_effect=lambda key: store_data.pop(key, None) or True)

    def mock_scan(cursor=0, match=None, count=10):
        import fnmatch

        all_keys = list(store_data.keys())
        if match:
            matched = [k for k in all_keys if fnmatch.fnmatch(k, match)]
        else:
            matched = all_keys
        return (0, matched)

    r.scan = MagicMock(side_effect=mock_scan)

    # Pipeline support
    class _Pipeline:
        def __init__(self):
            self._keys = []

        def get(self, key):
            self._keys.append(key)
            return self

        def execute(self):
            results = [store_data.get(k) for k in self._keys]
            self._keys = []
            return results

    r.pipeline = MagicMock(return_value=_Pipeline())

    return r


@pytest.fixture
def store(fake_redis):
    from agent.store import JobStore

    s = JobStore(redis_url="redis://fake:6379/0")
    s.redis = fake_redis
    return s


class TestJobStore:
    def test_create_job_returns_id_and_sets_meta(self, store, fake_redis):
        job_id = store.create_job(kind="agent", payload={"prompt": "hello"})
        assert job_id is not None
        assert len(job_id) > 0
        raw = fake_redis.get(f"job:{job_id}:meta")
        assert raw is not None
        meta = json.loads(raw)
        assert meta["kind"] == "agent"
        assert meta["status"] == "processing"
        assert meta["payload"] == {"prompt": "hello"}

    def test_get_job_returns_meta(self, store, fake_redis):
        job_id = store.create_job(kind="agent")
        meta = store.get_job(job_id)
        assert meta is not None
        assert meta["status"] == "processing"

    def test_get_job_returns_none_for_missing(self, store):
        assert store.get_job("nonexistent") is None

    def test_complete_job(self, store, fake_redis):
        job_id = store.create_job(kind="agent")
        store.complete_job(job_id, {"result": "done"})
        meta = store.get_job(job_id)
        assert meta["status"] == "completed"
        assert meta["completed_at"] is not None
        # complete_job adds the atomic completed count
        assert meta["data"] == {"result": "done", "completed": 0}

    def test_complete_job_unknown_id_does_not_raise(self, store):
        store.complete_job("unknown", {"result": "done"})  # should not raise

    def test_fail_job(self, store, fake_redis):
        job_id = store.create_job(kind="agent")
        store.fail_job(job_id, "Something went wrong")
        meta = store.get_job(job_id)
        assert meta["status"] == "failed"
        assert meta["error"] == "Something went wrong"

    def test_fail_job_persists_bounded_provider_diagnostics(self, store):
        job_id = store.create_job(kind="agent")
        details = {
            "finish_reason": "length",
            "refusal_present": False,
            "prompt_tokens": 123,
            "completion_tokens": 8192,
        }

        store.fail_job(
            job_id,
            "LLM provider output was incomplete",
            error_code="LLM_OUTPUT_TRUNCATED",
            error_details=details,
        )

        failed = store.get_job(job_id)
        assert failed["error_code"] == "LLM_OUTPUT_TRUNCATED"
        assert failed["error_details"] == details

    def test_fail_job_unknown_id_does_not_raise(self, store):
        store.fail_job("unknown", "error")  # should not raise

    def test_cancel_job(self, store, fake_redis):
        job_id = store.create_job(kind="agent")
        cancelled = store.cancel_job(job_id)
        assert cancelled is True
        meta = store.get_job(job_id)
        assert meta["status"] == "cancelled"

    def test_cancel_completed_job_returns_false(self, store, fake_redis):
        job_id = store.create_job(kind="agent")
        store.complete_job(job_id, {"result": "done"})
        cancelled = store.cancel_job(job_id)
        assert cancelled is False  # already completed

    def test_cancel_unknown_job_returns_false(self, store):
        cancelled = store.cancel_job("nonexistent")
        assert cancelled is False

    def test_list_active_jobs(self, store, fake_redis):
        store.create_job(kind="agent")
        store.create_job(kind="crawl")
        id3 = store.create_job(kind="agent")
        store.complete_job(id3, {})  # complete one

        # Should return processing jobs only
        active = store.list_active_jobs()
        assert len(active) >= 2  # at least 2 processing
        statuses = [j["status"] for j in active]
        assert all(s == "processing" for s in statuses)

    def test_list_active_jobs_filters_by_kind(self, store, fake_redis):
        store.create_job(kind="agent")
        store.create_job(kind="crawl")

        agent_jobs = store.list_active_jobs(kind="agent")
        assert all(j["kind"] == "agent" for j in agent_jobs)
        assert len(agent_jobs) >= 1

    def test_list_active_jobs_empty_when_all_completed(self, store, fake_redis):
        ids = [store.create_job(kind="agent") for _ in range(3)]
        for jid in ids:
            store.complete_job(jid, {})

        active = store.list_active_jobs()
        assert len(active) == 0

    def test_reuses_fake_redis_connection(self, store, fake_redis):
        """Verify that create_job and get_job use the same store."""
        jid = store.create_job(kind="test")
        meta = store.get_job(jid)
        assert meta["kind"] == "test"

    def test_complete_job_attaches_data(self, store, fake_redis):
        jid = store.create_job(kind="agent")
        data = {"result": "the answer", "sources": ["https://a.com"]}
        store.complete_job(jid, data)
        meta = store.get_job(jid)
        assert meta["data"] == data

    # ── Atomic progress (VAL-CONC-042) ─────────────────────────

    def test_increment_completed_starts_at_0(self, store, fake_redis):
        """Atomic completed counter is initialized to 0 on create."""
        jid = store.create_job(kind="crawl")
        assert store.get_completed(jid) == 0

    def test_increment_completed_atomic(self, store, fake_redis):
        """INCR returns increasing values — no lost increments."""
        jid = store.create_job(kind="crawl")
        assert store.increment_completed(jid) == 1
        assert store.increment_completed(jid) == 2
        assert store.increment_completed(jid) == 3
        assert store.get_completed(jid) == 3

    def test_increment_completed_multiple_jobs_independent(self, store, fake_redis):
        """Two jobs have independent completed counters."""
        jid_a = store.create_job(kind="crawl")
        jid_b = store.create_job(kind="crawl")

        store.increment_completed(jid_a)
        store.increment_completed(jid_a)
        store.increment_completed(jid_b)

        assert store.get_completed(jid_a) == 2
        assert store.get_completed(jid_b) == 1

    def test_get_completed_returns_0_for_unknown_job(self, store):
        """Non-existent job returns 0."""
        assert store.get_completed("nonexistent") == 0

    def test_update_job_progress_sources_completed_from_atomic_counter(
        self, store, fake_redis
    ):
        """update_job_progress() uses the atomic counter for completed field."""
        jid = store.create_job(kind="crawl")
        store.increment_completed(jid)
        store.increment_completed(jid)
        store.increment_completed(jid)

        store.update_job_progress(
            job_id=jid,
            pages=[
                {"url": "https://a.com"},
                {"url": "https://b.com"},
                {"url": "https://c.com"},
            ],
            total=10,
            errors=[],
            robots_blocked=[],
        )

        raw = store.get_job(jid)
        assert raw is not None
        data = raw.get("data", {})
        assert data["completed"] == 3
        assert data["total"] == 10
        assert len(data["pages"]) == 3

    def test_update_job_progress_concurrent_safety(self, store, fake_redis):
        """Simulating concurrent progress updates — each completed page is
        counted exactly once via INCR, and the data payload reflects the
        atomic count, not a stale local len()."""
        jid = store.create_job(kind="crawl")

        # Simulate three concurrent page completions
        store.increment_completed(jid)
        store.increment_completed(jid)
        store.increment_completed(jid)

        # Any subsequent update_job_progress call sees completed=3
        store.update_job_progress(
            job_id=jid, total=5, pages=[{"url": "a"}, {"url": "b"}, {"url": "c"}]
        )
        raw = store.get_job(jid)
        data = raw.get("data", {})
        assert data["completed"] == 3

    def test_complete_job_includes_atomic_completed(self, store, fake_redis):
        """complete_job() uses the atomic counter for the final count."""
        jid = store.create_job(kind="crawl")
        store.increment_completed(jid)
        store.increment_completed(jid)

        store.complete_job(jid, {"pages": [{"url": "a"}, {"url": "b"}]})
        meta = store.get_job(jid)
        data = meta.get("data", {})
        assert data["completed"] == 2

    def test_delete_completed_counter(self, store, fake_redis):
        jid = store.create_job(kind="crawl")
        store.increment_completed(jid)
        store.delete_completed_counter(jid)
        assert store.get_completed(jid) == 0

    # ── Cancel-race-condition guards ──────────────────────────────

    def test_complete_job_on_cancelled_job_preserves_cancelled(self, store, fake_redis):
        """complete_job must NOT overwrite 'cancelled' status.

        This prevents the race: DELETE /v2/crawl/{id} (cancel_job)
        arriving between a was_cancelled check and complete_job().
        """
        jid = store.create_job(kind="crawl")
        # Simulate: DELETE arrived first → status = cancelled
        store.cancel_job(jid)
        assert store.get_job(jid)["status"] == "cancelled"

        # Now simulate: worker's complete_job() tries to run
        store.complete_job(jid, {"pages": []})

        # Status must remain 'cancelled', not 'completed'
        meta = store.get_job(jid)
        assert meta["status"] == "cancelled", (
            f"Expected 'cancelled', got '{meta['status']}' — "
            "complete_job() overwrote cancelled status!"
        )

    def test_complete_job_on_failed_job_preserves_failed(self, store, fake_redis):
        """complete_job must NOT overwrite 'failed' status."""
        jid = store.create_job(kind="crawl")
        store.fail_job(jid, "Something went wrong")
        assert store.get_job(jid)["status"] == "failed"

        store.complete_job(jid, {"pages": []})

        meta = store.get_job(jid)
        assert meta["status"] == "failed"

    def test_complete_job_on_completed_job_preserves_completed(self, store, fake_redis):
        """complete_job is idempotent — calling it twice is a no-op."""
        jid = store.create_job(kind="crawl")
        store.complete_job(jid, {"pages": [{"url": "a"}]})
        completed_at = store.get_job(jid)["completed_at"]

        # Call complete_job again with different data
        store.complete_job(jid, {"pages": [{"url": "b"}]})

        meta = store.get_job(jid)
        assert meta["status"] == "completed"
        # completed_at should still be the same first timestamp
        assert meta["completed_at"] == completed_at

    def test_fail_job_on_cancelled_job_preserves_cancelled(self, store, fake_redis):
        """fail_job must NOT overwrite 'cancelled' status.

        Prevents the race: an exception thrown after cancellation
        would otherwise overwrite the intended cancelled status.
        """
        jid = store.create_job(kind="crawl")
        store.cancel_job(jid)
        assert store.get_job(jid)["status"] == "cancelled"

        store.fail_job(jid, "Some error after cancel")

        meta = store.get_job(jid)
        assert meta["status"] == "cancelled", (
            f"Expected 'cancelled', got '{meta['status']}'"
        )
        # error field should NOT be set
        assert "error" not in meta

    def test_fail_job_on_completed_job_preserves_completed(self, store, fake_redis):
        """fail_job must NOT overwrite 'completed' status."""
        jid = store.create_job(kind="crawl")
        store.complete_job(jid, {"pages": []})

        store.fail_job(jid, "Error after completion")

        meta = store.get_job(jid)
        assert meta["status"] == "completed"
        assert "error" not in meta

    def test_complete_job_still_works_on_processing(self, store, fake_redis):
        """complete_job on a processing job still transitions to completed."""
        jid = store.create_job(kind="crawl")
        store.complete_job(jid, {"pages": [{"url": "a"}]})
        meta = store.get_job(jid)
        assert meta["status"] == "completed"
        assert meta["data"]["pages"] == [{"url": "a"}]

    def test_fail_job_still_works_on_processing(self, store, fake_redis):
        """fail_job on a processing job still transitions to failed."""
        jid = store.create_job(kind="crawl")
        store.fail_job(jid, "Error")
        meta = store.get_job(jid)
        assert meta["status"] == "failed"
        assert meta["error"] == "Error"
