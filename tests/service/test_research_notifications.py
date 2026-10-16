"""Terminal callbacks survive restarts with stable IDs and bounded retries."""

import json
import os
from uuid import uuid4

import pytest
from agent.experimental import research_notifications
from agent.experimental.durable_research import DurableResearchLedger


@pytest.fixture
def ledger():
    url = os.environ["DURABLE_RESEARCH_REDIS_URL"]
    namespace = f"groktocrawl:test:notifications:{uuid4()}"
    value = DurableResearchLedger(url, namespace=namespace)
    try:
        yield value
    finally:
        for key in value.redis.scan_iter(match=f"{namespace}:*"):
            if key.startswith(namespace + ":"):
                value.redis.delete(key)
        value.redis.close()


def terminal(ledger, state="completed"):
    run = ledger.admit(
        "owner",
        str(uuid4()),
        "digest",
        payload={
            "research_id": str(uuid4()),
            "webhook": "https://callback.example/receive",
        },
    )
    assert ledger.claim_notification(run.run_id, "too-early") is None
    owned = ledger.claim(run.run_id, "worker")
    if state == "completed":
        ledger.commit_result(
            run.run_id,
            "worker",
            owned.owner_generation,
            "result",
            terminal_payload={"result": {"coverage": "complete"}},
        )
    elif state == "failed":
        ledger.fail(
            run.run_id,
            "worker",
            owned.owner_generation,
            {"error": {"code": "outcome_unknown"}},
        )
    else:
        ledger.cancel(run.run_id)
    return run


def release_attempt(ledger, run_id):
    """Simulate time passing at the crash boundary, without slow wall-clock waits."""
    key = ledger._run_key(run_id)
    record = json.loads(ledger.redis.get(key))
    record["notification"]["lease_until"] = 0
    record["notification"]["available_at"] = 0
    ledger.redis.set(key, json.dumps(record), px=ledger.retention_ms)


@pytest.mark.parametrize("state", ["completed", "failed", "cancelled"])
def test_terminal_callbacks_have_stable_exact_bytes_and_exclusive_ownership(
    ledger, state
):
    run = terminal(ledger, state)
    first = ledger.claim_notification(run.run_id, "first")
    assert first is not None
    assert ledger.claim_notification(run.run_id, "competitor") is None
    body = json.loads(first[1])
    assert body["type"] == f"research.{state}"
    assert body["webhookId"] == body["id"] == run.run_id
    assert body["success"] is (state == "completed")
    assert body["data"]["research_id"] == run.payload["research_id"]
    # Crash after the receiver accepted the bytes but before our ACK persisted.
    release_attempt(ledger, run.run_id)
    reopened = DurableResearchLedger(
        os.environ["DURABLE_RESEARCH_REDIS_URL"], namespace=ledger.namespace
    )
    try:
        second = reopened.claim_notification(run.run_id, "after-restart")
        assert second == first
        reopened.finish_notification(run.run_id, "first", "delivered")
        assert reopened.get(run.run_id).notification["state"] == "sending"
        reopened.finish_notification(run.run_id, "after-restart", "delivered")
        assert reopened.get(run.run_id).notification["state"] == "delivered"
        assert reopened.claim_notification(run.run_id, "never-again") is None
    finally:
        reopened.redis.close()


def test_retry_budget_truthfully_reports_unconfirmed_and_delete_fences_pending(ledger):
    run = terminal(ledger)
    bodies = []
    for attempt in range(3):
        owner = f"sender-{attempt}"
        bodies.append(ledger.claim_notification(run.run_id, owner)[1])
        ledger.finish_notification(run.run_id, owner, "retry")
        assert ledger.claim_notification(run.run_id, "immediate") is None
        release_attempt(ledger, run.run_id)
    assert len(set(bodies)) == 1
    assert ledger.get(run.run_id).notification["state"] == "unconfirmed"
    assert ledger.claim_notification(run.run_id, "fourth") is None
    deleted = terminal(ledger)
    ledger.delete(deleted.run_id)
    assert ledger.claim_notification(deleted.run_id, "sender") is None


def test_crashed_final_attempt_is_unconfirmed_without_fourth_dispatch(ledger):
    run = terminal(ledger)
    for attempt in range(3):
        assert ledger.claim_notification(run.run_id, f"lost-{attempt}") is not None
        release_attempt(ledger, run.run_id)
    assert ledger.claim_notification(run.run_id, "fourth") is None
    note = ledger.get(run.run_id).notification
    assert note["state"] == "unconfirmed"
    assert note["attempts"] == 3


@pytest.mark.asyncio
async def test_worker_recovers_queued_terminal_without_research_execution(
    ledger, monkeypatch
):
    run = terminal(ledger, "failed")
    observed = []

    async def send(url, body):
        observed.append((url, json.loads(body)))
        return "delivered"

    monkeypatch.setattr(research_notifications, "deliver_recorded_webhook", send)
    snapshot = ledger.export_snapshot()
    for key in ledger.redis.scan_iter(match=f"{ledger.namespace}:*"):
        ledger.redis.delete(key)
    ledger.restore_snapshot(snapshot)
    await research_notifications.deliver_notifications(ledger)
    assert len(observed) == 1
    assert observed[0][1]["error"]["code"] == "outcome_unknown"
    assert ledger.get(run.run_id).notification["state"] == "delivered"
