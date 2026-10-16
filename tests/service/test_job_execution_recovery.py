"""Restart-boundary checks for the Valkey legacy-job execution lease."""

from __future__ import annotations

import contextlib
import inspect
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests.outcome_governance import governed_skip


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture
def valkey_url(tmp_path: Path):
    executable = shutil.which("valkey-server") or shutil.which("redis-server")
    if executable is None:
        governed_skip(
            "Requires a local Valkey/Redis server for Lua lease validation",
            owner="repository-maintainer",
            issue="ADR-0072",
            classification="retained",
            environment="local Valkey/Redis server unavailable",
        )
    port = _free_port()
    process = subprocess.Popen(
        [
            executable,
            "--port",
            str(port),
            "--bind",
            "127.0.0.1",
            "--save",
            "",
            "--appendonly",
            "no",
            "--dir",
            str(tmp_path),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    url = f"redis://127.0.0.1:{port}/0"
    try:
        from redis import Redis

        client = Redis.from_url(url, decode_responses=True)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                if client.ping():
                    break
            except Exception:
                time.sleep(0.05)
        else:
            pytest.fail("isolated Valkey server did not become ready")
        yield url
    finally:
        process.terminate()
        process.wait(timeout=5)


def test_fresh_process_fails_interrupted_jobs_without_replaying(
    valkey_url: str, tmp_path: Path
) -> None:
    """A new process truthfully closes stale work across all six families."""
    from agent.store import JobStore
    from redis import Redis

    store = JobStore(valkey_url)
    families = ("crawl", "agent", "extract", "batch_scrape", "llmstxt", "plan_execute")
    job_ids = []
    for family in families:
        job_id = store.create_job(family, {"recovery_test": True})
        record = store.get_job(job_id)
        assert record is not None
        record["execution"].update(
            {
                "state": "running",
                "lease_owner": "dead-process",
                "lease_expires_at": time.time() - 1,
                "pending_operation": "worker_execution",
                "checkpoint": {"kind": "worker_started"},
            }
        )
        Redis.from_url(valkey_url, decode_responses=True).set(
            f"job:{job_id}:meta", json.dumps(record), ex=86400
        )
        Redis.from_url(valkey_url, decode_responses=True).set(
            f"job:{job_id}:data",
            json.dumps(
                {
                    "pages": [
                        {"url": f"https://example.test/{family}", "markdown": "partial"}
                    ],
                    "completed": 1,
                }
            ),
            ex=86400,
        )
        job_ids.append((job_id, family))

    # This subprocess represents a fresh API process. It can only inspect
    # persisted state; it has no worker invocation or provider adapter.
    code = r"""
import asyncio, sys
from types import SimpleNamespace
from agent.store import JobStore
from agent.tasks import TaskTracker
from agent.job_execution import JobExecutionCoordinator
async def main():
    store = JobStore(sys.argv[1])
    coordinator = JobExecutionCoordinator(store, TaskTracker(), SimpleNamespace(state=SimpleNamespace()))
    await coordinator.recover_queued_jobs()
    await asyncio.sleep(0.05)
asyncio.run(main())
"""
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(Path(__file__).parents[2] / "agent-svc"), env.get("PYTHONPATH", "")]
    )
    subprocess.run(
        [sys.executable, "-c", code, valkey_url],
        check=True,
        env=env,
        timeout=10,
    )

    for job_id, family in job_ids:
        record = store.get_job(job_id)
        assert record is not None
        assert record["status"] == "failed"
        assert record["error_code"] == "OUTCOME_UNKNOWN"
        assert record["execution"]["state"] == "outcome_unknown"
        assert record["data"]["pages"] == [
            {"url": f"https://example.test/{family}", "markdown": "partial"}
        ]
    store.redis.close()


def test_queued_payloads_rebuild_all_supported_worker_families(valkey_url: str) -> None:
    """Queued, never-claimed jobs reconstruct without redoing NL planning."""
    from types import SimpleNamespace

    from agent.job_execution import JobExecutionCoordinator
    from agent.store import JobStore
    from agent.tasks import TaskTracker

    tracker = TaskTracker()
    state = SimpleNamespace(
        scraper_url="http://scraper.invalid",
        searxng_url="http://search.invalid",
        llm_base_url="http://llm.invalid/v1",
        llm_api_key="test-key",
        llm_model="test-model",
        research_memory=object(),
        max_searches_per_request=1,
    )
    coordinator = JobExecutionCoordinator(
        JobStore(valkey_url), tracker, SimpleNamespace(state=state)
    )
    common = {
        "agent": {"prompt": "question"},
        "crawl": {
            "url": "https://example.org",
            "effective_max_pages": 2,
            "max_depth": 1,
        },
        "extract": {"urls": ["https://example.org"], "prompt": "extract"},
        "batch_scrape": {"urls": ["https://example.org"]},
        "llmstxt": {"url": "https://example.org", "max_pages": 2},
        "plan_execute": {"prompt": "question", "plan": {"phases": []}},
    }
    for family, payload in common.items():
        coroutine = coordinator._rebuild(
            {"id": f"job-{family}", "kind": family, "payload": payload}
        )
        assert inspect.iscoroutine(coroutine)
        coroutine.close()


def test_fresh_process_dispatches_never_started_jobs_once(valkey_url: str) -> None:
    """A restart replays queued jobs through each real worker adapter once."""
    from agent.store import JobStore

    store = JobStore(valkey_url)
    payloads = {
        "agent": {"prompt": "question"},
        "crawl": {
            "url": "https://example.org",
            "effective_max_pages": 2,
            "max_depth": 1,
        },
        "extract": {"urls": ["https://example.org"], "prompt": "extract"},
        "batch_scrape": {"urls": ["https://example.org"]},
        "llmstxt": {"url": "https://example.org", "max_pages": 2},
        "plan_execute": {"prompt": "question", "plan": {"phases": []}},
    }
    jobs = {kind: store.create_job(kind, payload) for kind, payload in payloads.items()}
    code = r"""
import asyncio, sys
from types import SimpleNamespace
from agent.store import JobStore
from agent.tasks import TaskTracker
from agent.job_execution import JobExecutionCoordinator
from agent import worker
async def main():
    url, *job_ids = sys.argv[1:]
    store = JobStore(url)
    tracker = TaskTracker()
    state = SimpleNamespace(scraper_url='http://scraper.invalid', searxng_url='http://search.invalid', llm_base_url='http://llm.invalid/v1', llm_api_key='test-key', llm_model='test-model', research_memory=object(), max_searches_per_request=1)
    app = SimpleNamespace(state=state)
    coordinator = JobExecutionCoordinator(store, tracker, app)
    state.job_store = store
    state.task_tracker = tracker
    for name in ('_process_agent_async', '_process_crawl_async', '_process_extract_async', '_process_batch_scrape_async', '_process_llmstxt_async', '_process_plan_execution_async'):
        async def fake(job_id, *args, **kwargs):
            await asyncio.sleep(0)
            store.complete_job(job_id, {'worker': 'test-double'})
        setattr(worker, name, fake)
    await coordinator.recover_queued_jobs()
    deadline = asyncio.get_running_loop().time() + 5
    while asyncio.get_running_loop().time() < deadline:
        records = [store.get_job(job_id) for job_id in job_ids]
        if all(record and record['status'] == 'completed' for record in records):
            await asyncio.sleep(0.05)
            return
        await asyncio.sleep(0.01)
    raise AssertionError([(job_id, store.get_job(job_id)) for job_id in job_ids])
asyncio.run(main())
"""
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(Path(__file__).parents[2] / "agent-svc"), env.get("PYTHONPATH", "")]
    )
    subprocess.run(
        [sys.executable, "-c", code, valkey_url, *jobs.values()],
        check=True,
        env=env,
        timeout=10,
    )
    assert all(
        store.get_job(job_id)["status"] == "completed" for job_id in jobs.values()
    )


def test_webhook_outbox_deduplicates_and_releases_leases(valkey_url: str) -> None:
    """Repeated event intent reuses its UUIDv4 and delivery claim is fenced."""
    import uuid

    from agent.store import JobStore

    store = JobStore(valkey_url)
    job_id = store.create_job("agent", {})
    event = {
        "webhookId": str(uuid.uuid4()),
        "url": "https://example.org/hook",
        "event": "completed",
        "body": '{"type":"completed"}',
        "headers": {"Content-Type": "application/json"},
    }
    first = store.enqueue_webhook(job_id, "same-event-fingerprint", event)
    duplicate = store.enqueue_webhook(
        job_id,
        "same-event-fingerprint",
        {**event, "webhookId": str(uuid.uuid4())},
    )
    assert duplicate["webhookId"] == first["webhookId"]
    assert uuid.UUID(first["webhookId"]).version == 4

    assert store.claim_webhook(job_id, first["webhookId"], "owner-a", lease=60)
    assert store.claim_webhook(job_id, first["webhookId"], "owner-b", lease=60) is None
    assert not store.finish_webhook(job_id, first["webhookId"], "owner-b", "delivered")
    assert store.finish_webhook(
        job_id, first["webhookId"], "owner-a", "pending", "timeout"
    )
    assert store.claim_webhook(job_id, first["webhookId"], "owner-b", lease=60)
    assert store.finish_webhook(job_id, first["webhookId"], "owner-b", "delivered")
    assert store.claim_webhook(job_id, first["webhookId"], "owner-c", lease=60) is None
    assert store.list_pending_webhooks() == []


@pytest.mark.parametrize("key_type", [str, bytes])
def test_pending_webhook_job_id_is_text_for_scan_key_types(key_type) -> None:
    """Redis clients may return scan keys as bytes or decoded strings."""
    from agent.store import JobStore

    job_id = "job-123"
    key = f"job:{job_id}:webhooks"

    class RedisStub:
        def scan(self, *, cursor, match, count):
            assert cursor == 0
            assert match == "job:*:webhooks"
            return 0, [key_type(key.encode() if key_type is bytes else key)]

        def hgetall(self, scanned_key):
            assert scanned_key == key_type(key.encode() if key_type is bytes else key)
            return {
                b"event-1": json.dumps({"status": "pending", "webhookId": "event-1"})
            }

    store = JobStore.__new__(JobStore)
    store.redis = RedisStub()

    events = store.list_pending_webhooks()

    assert events == [{"status": "pending", "webhookId": "event-1", "job_id": job_id}]
    assert isinstance(events[0]["job_id"], str)


def test_restart_replays_pending_webhook_with_original_id(
    valkey_url: str, monkeypatch
) -> None:
    """Recovery reuses persisted body/id and marks delivery after the receipt."""
    import asyncio
    import uuid

    from agent import webhook
    from agent.store import JobStore

    store = JobStore(valkey_url)
    job_id = store.create_job("agent", {})
    event = {
        "webhookId": str(uuid.uuid4()),
        "url": "https://example.org/hook",
        "event": "completed",
        "body": '{"webhookId":"retained-id"}',
        "headers": {"Content-Type": "application/json"},
    }
    persisted = store.enqueue_webhook(job_id, "retained-event", event)
    observed = []

    async def acknowledge(record, recovered_job_id):
        observed.append((record["webhookId"], record["body"], recovered_job_id))
        return "delivered"

    monkeypatch.setattr(webhook, "_send_outbox_event", acknowledge)
    asyncio.run(webhook.replay_pending_webhooks(store))
    assert observed == [(persisted["webhookId"], persisted["body"], job_id)]
    assert store.list_pending_webhooks() == []


def test_terminal_job_and_webhook_intent_commit_together(valkey_url: str) -> None:
    """A crash after terminalization cannot lose its webhook intent."""
    import uuid

    from agent.store import _EXECUTION_CONTEXT, JobStore

    store = JobStore(valkey_url)
    job_id = store.create_job("agent", {})
    execution = json.loads(store.redis.get(f"job:{job_id}:meta"))["execution"]
    execution.update(
        {
            "state": "running",
            "lease_owner": "terminal-owner",
            "lease_expires_at": time.time() + 60,
            "fence": 9,
        }
    )
    meta = json.loads(store.redis.get(f"job:{job_id}:meta"))
    meta["execution"] = execution
    store.redis.set(f"job:{job_id}:meta", json.dumps(meta), ex=86400)
    event = {
        "fingerprint": "terminal-fingerprint",
        "event": {
            "webhookId": str(uuid.uuid4()),
            "url": "https://example.org/hook",
            "event": "completed",
            "body": '{"type":"completed"}',
            "headers": {"Content-Type": "application/json"},
            "status": "pending",
            "attempts": 0,
        },
    }
    token = _EXECUTION_CONTEXT.set((job_id, "terminal-owner", 9))
    try:
        assert store.complete_job(job_id, {"answer": "done"}, event)
    finally:
        _EXECUTION_CONTEXT.reset(token)
    assert store.get_job(job_id)["status"] == "completed"
    outbox = store.redis.hget(f"job:{job_id}:webhooks", event["event"]["webhookId"])
    assert json.loads(outbox)["body"] == event["event"]["body"]


def test_plan_consumption_and_execution_job_creation_are_atomic(
    valkey_url: str,
) -> None:
    """A process exit cannot consume a plan without leaving a queued job."""
    from agent.planner import PlanStore
    from agent.store import JobStore

    plans = PlanStore(redis_url=valkey_url)
    jobs = JobStore(valkey_url)
    plan_id = plans.create(
        prompt="research question",
        plan={"phases": [{"action": "search"}]},
    )
    payload = {
        "plan_id": plan_id,
        "prompt": "research question",
        "plan": {"phases": [{"action": "search"}]},
        "modifications": None,
        "webhook": None,
    }
    job_id = jobs.create_plan_execution_job(plan_id, payload)
    assert job_id is not None
    record = jobs.get_job(job_id)
    assert record["kind"] == "plan_execute"
    assert record["execution"]["state"] == "queued"
    assert plans.get(plan_id) is None
    assert jobs.create_plan_execution_job(plan_id, payload) is None


def test_cancel_revokes_owner_and_commits_crawl_webhook(valkey_url: str) -> None:
    """Cancellation wins against a live owner and retains its event intent."""
    import uuid
    from types import SimpleNamespace

    from agent.job_execution import JobExecutionCoordinator
    from agent.store import _EXECUTION_CONTEXT, JobStore
    from agent.tasks import TaskTracker

    store = JobStore(valkey_url)
    job_id = store.create_job("crawl", {"webhook": {"url": "https://example.org/hook"}})
    coordinator = JobExecutionCoordinator(
        store, TaskTracker(), SimpleNamespace(state=None)
    )
    claim = coordinator.claim(job_id)
    assert claim is not None
    owner, fence = claim
    event = {
        "fingerprint": "crawl-cancel-fingerprint",
        "event": {
            "webhookId": str(uuid.uuid4()),
            "url": "https://example.org/hook",
            "event": "crawl.completed",
            "body": '{"type":"crawl.completed"}',
            "headers": {"Content-Type": "application/json"},
            "status": "pending",
            "attempts": 0,
        },
    }
    assert store.cancel_job(job_id, event)
    record = store.get_job(job_id)
    assert record["status"] == "cancelled"
    assert record["execution"]["fence"] == fence + 1
    token = _EXECUTION_CONTEXT.set((job_id, owner, fence))
    try:
        assert not store.complete_job(job_id, {"late": True})
    finally:
        _EXECUTION_CONTEXT.reset(token)
    assert store.redis.hget(f"job:{job_id}:webhooks", event["event"]["webhookId"])


def test_outcome_unknown_is_exposed_by_each_async_status_model() -> None:
    """Callers can distinguish an ambiguous outcome from a generic failure."""
    from agent.models import (
        AgentStatusResponse,
        BatchScrapeStatusResponse,
        CrawlStatusResponse,
        ExtractStatusResponse,
        LLMsTextStatusResponse,
    )

    for response_type in (
        AgentStatusResponse,
        CrawlStatusResponse,
        ExtractStatusResponse,
        BatchScrapeStatusResponse,
        LLMsTextStatusResponse,
    ):
        response = response_type(status="failed", error_code="OUTCOME_UNKNOWN")
        assert response.model_dump(by_alias=True)["errorCode"] == "OUTCOME_UNKNOWN"


def test_public_status_routes_serialize_outcome_unknown_for_all_job_families() -> None:
    """The real polling endpoints expose the public errorCode field."""
    from agent.routes import router
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    class FailedJobStore:
        def get_job(self, job_id: str) -> dict:
            return {
                "status": "failed",
                "error": "Worker stopped before a durable completion record.",
                "error_code": "OUTCOME_UNKNOWN",
                "data": {
                    "pages": [
                        {"url": "https://example.test/partial", "markdown": "kept"}
                    ],
                    "completed": 1,
                    "total": 2,
                },
                "created_at": "2026-10-15T00:00:00+00:00",
                "completed_at": "2026-10-15T00:01:00+00:00",
            }

    app = FastAPI()
    app.include_router(router)
    app.state.job_store = FailedJobStore()
    paths = (
        "/v2/crawl/crawl-job",
        "/v2/agent/agent-job",
        "/v2/extract/extract-job",
        "/v2/batch/scrape/batch-job",
        "/v2/generate-llmstxt/llmstxt-job",
        # Plan executions share the agent job polling endpoint after admission.
        "/v2/agent/plan-execution-job",
    )

    with TestClient(app) as client:
        for path in paths:
            response = client.get(path)
            assert response.status_code == 200, path
            body = response.json()
            assert body["status"] == "failed", path
            assert body["errorCode"] == "OUTCOME_UNKNOWN", path
        for path in (paths[0], paths[3]):
            data = client.get(path).json()["data"]
            assert data[0]["markdown"] == "kept"


def test_task_tracker_does_not_replace_a_jobs_cancellation_owner() -> None:
    """The route and startup scanner cannot register duplicate job workers."""
    import asyncio

    from agent.tasks import TaskTracker

    async def exercise() -> None:
        tracker = TaskTracker()
        release = asyncio.Event()
        second_started = False

        async def owner() -> None:
            await release.wait()

        async def duplicate() -> None:
            nonlocal second_started
            second_started = True

        first_task = tracker.create_background_task(owner(), job_id="shared-job")
        returned_task = tracker.create_background_task(duplicate(), job_id="shared-job")
        assert returned_task is first_task
        assert tracker.cancel_job("shared-job")
        with contextlib.suppress(asyncio.CancelledError):
            await first_task
        assert second_started is False

    asyncio.run(exercise())
