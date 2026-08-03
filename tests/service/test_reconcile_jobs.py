"""Unit tests for scripts/reconcile-jobs.py (no network).

Covers staleness detection (boundary semantics), kind/limit filters,
terminal-status preservation, dry-run default, --fail reconciliation,
idempotency, JSON output, exit codes, and VALKEY_URL/--redis-url
resolution — all against a fake redis client. Follows the
`importlib.util.spec_from_file_location` pattern from
`test_enforce_branch_protection.py`.
"""

from __future__ import annotations

import argparse
import fnmatch
import importlib.util
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import redis

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "reconcile-jobs.py"

SPEC = importlib.util.spec_from_file_location("reconcile_jobs", SCRIPT)
assert SPEC and SPEC.loader, "scripts/reconcile-jobs.py must exist"
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

NOW = datetime(2026, 8, 3, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def fake_redis() -> MagicMock:
    return make_fake_redis()


def _iso(ts: datetime) -> str:
    return ts.isoformat()


def make_fake_redis() -> MagicMock:
    """Fake redis supporting scan/get/set (set records kwargs)."""
    store_data: dict[str, str] = {}
    set_calls: list[dict] = []

    def _scan(cursor=0, match=None, count=10):
        # Single page; the fake always exhausts the cursor. Keep the
        # arguments bound so the signature matches redis and vulture
        # sees them as used.
        page_size = count
        _ = (cursor, page_size)
        keys = (
            [k for k in store_data if fnmatch.fnmatch(k, match)]
            if match
            else list(store_data)
        )
        return (0, keys)

    def _set(key: str, value: str, **kwargs) -> bool:
        store_data[key] = value
        set_calls.append(kwargs)
        return True

    client = MagicMock()
    client.scan.side_effect = _scan
    client.get.side_effect = lambda key: store_data.get(key)
    client.set.side_effect = _set
    client._store = store_data
    client._set_calls = set_calls
    return client


def seed_job(
    client: MagicMock,
    job_id: str,
    *,
    kind: str = "crawl",
    status: str = "processing",
    created_at: datetime | str | None = None,
    payload: dict | None = None,
) -> str:
    """Write a job:*:meta record and return its key."""
    if created_at is None:
        created_at = NOW
    created = created_at.isoformat() if isinstance(created_at, datetime) else created_at
    meta = {
        "id": job_id,
        "kind": kind,
        "status": status,
        "created_at": created,
        "expires_at": _iso(NOW + timedelta(hours=24)),
        "payload": payload or {},
    }
    if status in ("completed", "failed", "cancelled"):
        meta["completed_at"] = _iso(NOW)
    if status == "failed":
        meta["error"] = "original error"
    client._store[f"job:{job_id}:meta"] = json.dumps(meta)
    return f"job:{job_id}:meta"


def args(**overrides) -> argparse.Namespace:
    base = {
        "redis_url": "redis://fake:6379/0",
        "stale_after": 3600,
        "kind": None,
        "limit": 100,
        "fail": False,
        "json": False,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


# ── find_stranded_jobs ────────────────────────────────────────────


class TestFindStrandedJobs:
    def test_detects_stale(self, fake_redis):
        seed_job(fake_redis, "old-job", created_at=NOW - timedelta(hours=2))
        seed_job(fake_redis, "fresh-job", created_at=NOW - timedelta(minutes=10))
        stranded, skipped = MODULE.find_stranded_jobs(fake_redis, 3600, now=NOW)
        assert [j["id"] for j in stranded] == ["old-job"]
        assert skipped == []

    def test_boundary_is_exclusive(self, fake_redis):
        # Age exactly at the threshold is NOT stale; just over IS.
        seed_job(fake_redis, "at-boundary", created_at=NOW - timedelta(seconds=3600))
        seed_job(fake_redis, "just-over", created_at=NOW - timedelta(seconds=3601))
        stranded, _ = MODULE.find_stranded_jobs(fake_redis, 3600, now=NOW)
        assert [j["id"] for j in stranded] == ["just-over"]

    def test_excludes_terminal_statuses(self, fake_redis):
        seed_job(
            fake_redis, "done", status="completed", created_at=NOW - timedelta(hours=5)
        )
        seed_job(
            fake_redis, "dead", status="failed", created_at=NOW - timedelta(hours=5)
        )
        seed_job(
            fake_redis,
            "halted",
            status="cancelled",
            created_at=NOW - timedelta(hours=5),
        )
        stranded, _ = MODULE.find_stranded_jobs(fake_redis, 3600, now=NOW)
        assert stranded == []

    def test_kind_filter(self, fake_redis):
        seed_job(
            fake_redis, "crawl-job", kind="crawl", created_at=NOW - timedelta(hours=2)
        )
        seed_job(
            fake_redis, "agent-job", kind="agent", created_at=NOW - timedelta(hours=2)
        )
        stranded, _ = MODULE.find_stranded_jobs(fake_redis, 3600, now=NOW, kind="agent")
        assert [j["id"] for j in stranded] == ["agent-job"]

    def test_limit(self, fake_redis):
        for i in range(5):
            seed_job(fake_redis, f"job-{i}", created_at=NOW - timedelta(hours=2))
        stranded, _ = MODULE.find_stranded_jobs(fake_redis, 3600, now=NOW, limit=3)
        assert len(stranded) == 3

    def test_unparseable_created_at_is_skipped_not_stranded(self, fake_redis):
        seed_job(fake_redis, "broken", created_at="not-a-date")
        stranded, skipped = MODULE.find_stranded_jobs(fake_redis, 3600, now=NOW)
        assert stranded == []
        assert [j["id"] for j in skipped] == ["broken"]

    def test_corrupt_record_is_skipped_not_crash(self, fake_redis):
        fake_redis._store["job:corrupt:meta"] = "{not valid json"
        stranded, skipped = MODULE.find_stranded_jobs(fake_redis, 3600, now=NOW)
        assert stranded == []
        assert [j["id"] for j in skipped] == ["corrupt"]

    def test_multiple_scan_pages_are_followed(self):
        class PagedRedis:
            """Two-page scan fake: cursor 1 then 0."""

            def __init__(self):
                self._store = {
                    "job:one:meta": json.dumps(
                        {
                            "id": "one",
                            "kind": "crawl",
                            "status": "processing",
                            "created_at": _iso(NOW - timedelta(hours=2)),
                            "payload": {},
                        }
                    ),
                    "job:two:meta": json.dumps(
                        {
                            "id": "two",
                            "kind": "crawl",
                            "status": "processing",
                            "created_at": _iso(NOW - timedelta(hours=2)),
                            "payload": {},
                        }
                    ),
                }

            def scan(self, cursor=0, match=None, count=10):
                if cursor == 0:
                    return (1, ["job:one:meta"])
                return (0, ["job:two:meta"])

            def get(self, key):
                return self._store.get(key)

        stranded, _ = MODULE.find_stranded_jobs(PagedRedis(), 3600, now=NOW)
        assert {j["id"] for j in stranded} == {"one", "two"}


# ── fail_jobs ─────────────────────────────────────────────────────


class TestFailJobs:
    def test_transitions_only_processing(self, fake_redis):
        seed_job(fake_redis, "stale", created_at=NOW - timedelta(hours=2))
        seed_job(
            fake_redis, "done", status="completed", created_at=NOW - timedelta(hours=2)
        )
        seed_job(
            fake_redis,
            "halted",
            status="cancelled",
            created_at=NOW - timedelta(hours=2),
        )
        jobs = [
            {"id": "stale"},
            {"id": "done"},
            {"id": "halted"},
        ]
        count = MODULE.fail_jobs(fake_redis, jobs)
        assert count == 1
        stale = json.loads(fake_redis._store["job:stale:meta"])
        assert stale["status"] == "failed"
        assert stale["error"] == MODULE.RECONCILE_ERROR
        assert "completed_at" in stale
        assert json.loads(fake_redis._store["job:done:meta"])["status"] == "completed"
        assert json.loads(fake_redis._store["job:halted:meta"])["status"] == "cancelled"

    def test_missing_record_is_ignored(self, fake_redis):
        count = MODULE.fail_jobs(fake_redis, [{"id": "ghost"}])
        assert count == 0


# ── run (dry-run / reconcile / json / exit codes) ─────────────────


class TestRun:
    def test_dry_run_returns_1_and_modifies_nothing(self, fake_redis, capsys):
        seed_job(fake_redis, "stale", created_at=NOW - timedelta(hours=2))
        rc = MODULE.run(fake_redis, args(), now=NOW)
        assert rc == 1
        meta = json.loads(fake_redis._store["job:stale:meta"])
        assert meta["status"] == "processing"
        assert "Dry run" in capsys.readouterr().out

    def test_no_stranded_returns_0(self, fake_redis):
        seed_job(fake_redis, "fresh", created_at=NOW - timedelta(minutes=1))
        assert MODULE.run(fake_redis, args(), now=NOW) == 0

    def test_fail_reconciles_and_returns_0(self, fake_redis):
        seed_job(fake_redis, "stale", created_at=NOW - timedelta(hours=2))
        rc = MODULE.run(fake_redis, args(fail=True), now=NOW)
        assert rc == 0
        assert json.loads(fake_redis._store["job:stale:meta"])["status"] == "failed"

    def test_fail_is_idempotent(self, fake_redis):
        seed_job(fake_redis, "stale", created_at=NOW - timedelta(hours=2))
        first = MODULE.run(fake_redis, args(fail=True), now=NOW)
        second = MODULE.run(fake_redis, args(fail=True), now=NOW)
        assert first == 0
        assert second == 0
        assert json.loads(fake_redis._store["job:stale:meta"])["status"] == "failed"

    def test_json_output(self, fake_redis, capsys):
        seed_job(
            fake_redis,
            "stale",
            created_at=NOW - timedelta(hours=2),
            payload={"url": "https://example.com/x"},
        )
        rc = MODULE.run(fake_redis, args(json=True), now=NOW)
        assert rc == 1
        payload = json.loads(capsys.readouterr().out)
        assert payload["dry_run"] is True
        assert payload["failed"] == 0
        assert payload["stranded"][0]["id"] == "stale"
        assert payload["stranded"][0]["url"] == "https://example.com/x"
        assert payload["remaining"] == ["stale"]
        assert payload["skipped"] == []


# ── main (argv / env resolution / exit codes) ─────────────────────


class TestMain:
    def _patch_client(self, fake_redis, monkeypatch):
        monkeypatch.setattr(MODULE, "create_client", lambda url: fake_redis)

    def test_redis_url_flag_overrides_env(self, fake_redis, monkeypatch):
        monkeypatch.setenv("VALKEY_URL", "redis://from-env:6379/0")
        seen: list[str] = []

        def factory(url: str):
            seen.append(url)
            return fake_redis

        monkeypatch.setattr(MODULE, "create_client", factory)
        MODULE.main(["--redis-url", "redis://explicit:6379/0", "--json"])
        assert seen == ["redis://explicit:6379/0"]

    def test_valkey_url_env_fallback(self, fake_redis, monkeypatch):
        monkeypatch.setenv("VALKEY_URL", "redis://from-env:6379/0")
        seen: list[str] = []

        def factory(url: str):
            seen.append(url)
            return fake_redis

        monkeypatch.setattr(MODULE, "create_client", factory)
        MODULE.main(["--json"])
        assert seen == ["redis://from-env:6379/0"]

    def test_default_url_when_env_unset(self, fake_redis, monkeypatch):
        monkeypatch.delenv("VALKEY_URL", raising=False)
        seen: list[str] = []

        def factory(url: str):
            seen.append(url)
            return fake_redis

        monkeypatch.setattr(MODULE, "create_client", factory)
        MODULE.main(["--json"])
        assert seen == ["redis://localhost:6379/0"]

    def test_main_exit_1_on_stranded(self, fake_redis, monkeypatch):
        self._patch_client(fake_redis, monkeypatch)
        seed_job(fake_redis, "stale", created_at=datetime.now(UTC) - timedelta(hours=2))
        assert MODULE.main(["--json"]) == 1

    def test_main_exit_0_when_clean(self, fake_redis, monkeypatch):
        self._patch_client(fake_redis, monkeypatch)
        seed_job(
            fake_redis, "fresh", created_at=datetime.now(UTC) - timedelta(minutes=1)
        )
        assert MODULE.main(["--json"]) == 0

    def test_main_exit_2_on_connection_error(self, monkeypatch):
        def boom(url: str):
            raise RuntimeError("connection refused")

        monkeypatch.setattr(MODULE, "create_client", boom)
        assert MODULE.main(["--json"]) == 2

    def test_main_exit_2_on_runtime_redis_error(self, fake_redis, monkeypatch):
        """Redis.from_url is lazy: a Valkey outage surfaces on the first
        scan inside run() and must map to exit 2, not exit 1."""
        self._patch_client(fake_redis, monkeypatch)
        fake_redis.scan.side_effect = redis.exceptions.ConnectionError("down")
        assert MODULE.main(["--json"]) == 2
