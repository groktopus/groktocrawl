"""Crash boundaries use isolated Valkey and controlled adapters, never providers."""

import asyncio
import hashlib
import json
import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from agent.experimental import live_runner
from agent.experimental.durable_research import (
    DurableConflictError,
    DurableResearchLedger,
    OutcomeUnknownError,
)
from agent.experimental.model_review import ModelReply, ReviewRequest
from agent.routes import experimental_research as routes


@pytest.fixture
def ledger():
    url = os.environ.get("DURABLE_RESEARCH_REDIS_URL") or os.environ.get("VALKEY_URL")
    if not url:
        raise RuntimeError("isolated durable Valkey URL required")
    namespace = f"groktocrawl:test:live-recovery:{uuid4()}"
    value = DurableResearchLedger(url, namespace=namespace)
    try:
        yield value
    finally:
        for key in value.redis.scan_iter(match=f"{namespace}:*"):
            if key.startswith(namespace + ":"):
                value.redis.delete(key)
        value.redis.close()


def admitted(ledger, policy=None, max_tokens=2048):
    value = ledger.admit(
        "test-owner",
        str(uuid4()),
        "input-digest",
        payload={
            "objective": "Bounded test question",
            "research_id": str(uuid4()),
            "execution_mode": "live",
            "model": "configured-test-model",
            "token_params": {"max_tokens": max_tokens},
            "evaluation_time": datetime.now(UTC).isoformat(),
            "policy_version": policy,
        },
    )
    record = routes._restore_durable_record(value)
    record.durable_ledger = ledger
    record.app = SimpleNamespace(
        state=SimpleNamespace(
            searxng_url="http://local-search.test",
            llm_base_url="http://local-model.test",
            llm_api_key="test-only-key",
            llm_model="configured-test-model",
        )
    )
    return record


def claim(record):
    owned = record.durable_ledger.claim(
        record.run_id, owner_id=str(uuid4()), attempt_id=str(uuid4())
    )
    record.durable_owner_id = owned.owner_id
    record.durable_generation = owned.owner_generation


@pytest.mark.asyncio
async def test_restart_reuses_exact_external_receipts(ledger, monkeypatch):
    record = admitted(ledger)
    calls = {"search": 0, "acquire": 0, "model": 0}

    class Search:
        def __init__(self, *_args, **_kwargs):
            pass

        async def search(self, *_args, **_kwargs):
            calls["search"] += 1
            return [{"url": "https://source.test/one"}], SimpleNamespace(
                empty_result=False
            )

        async def close(self):
            pass

    class Scraper:
        async def scrape(self, *_args, **_kwargs):
            calls["acquire"] += 1
            return {
                "success": True,
                "data": {"markdown": "Source-specific retained bytes"},
            }

    class Transport:
        def __init__(self, *_args, **_kwargs):
            pass

        async def __call__(self, request):
            calls["model"] += 1
            return ModelReply(
                b'{"answer":"from source"}', request.requested_model, None, 7
            )

    first = True
    observed = []

    async def pipeline(objective, *, search, acquire, complete, **kwargs):
        nonlocal first
        urls = await search(objective, 3)
        source = await acquire(urls[0])
        reply = await complete(
            ReviewRequest(
                "instruction", b'{"input":"stable"}', "configured-test-model", 2048
            )
        )
        observed.append((source, reply, {**kwargs, "clock": kwargs["clock"]()}))
        if first:
            first = False
            raise asyncio.CancelledError  # process lost after durable model receipt
        return "completed without new external effects"

    monkeypatch.setattr(live_runner, "SearXNGClient", Search)
    monkeypatch.setattr(live_runner, "ReviewTransport", Transport)
    monkeypatch.setattr(live_runner, "run_query", pipeline)
    record.app.state.scraper_client = Scraper()
    claim(record)
    with pytest.raises(asyncio.CancelledError):
        await live_runner.run_live(record, record.app)
    ledger.redis.delete(ledger._lease_key(record.run_id))
    recovered = routes._restore_durable_record(ledger.get(record.run_id))
    recovered.durable_ledger = ledger
    recovered.app = record.app
    claim(recovered)
    assert (
        await live_runner.run_live(recovered, recovered.app)
        == "completed without new external effects"
    )
    assert calls == {"search": 1, "acquire": 1, "model": 1}
    assert observed[0] == observed[1]
    assert observed[1][1].input_tokens is None  # unknown usage never becomes zero


@pytest.mark.asyncio
async def test_restart_does_not_replay_dispatch_without_receipt(ledger, monkeypatch):
    record = admitted(ledger)
    claim(record)
    inputs = json.dumps(
        {"objective": record.objective, "limit": 3},
        sort_keys=True,
        separators=(",", ":"),
    )
    ledger.step(
        record.run_id,
        record.durable_owner_id,
        record.durable_generation,
        "search",
        hashlib.sha256(inputs.encode()).hexdigest(),
    )
    ledger.redis.delete(ledger._lease_key(record.run_id))
    claim(record)

    async def pipeline(objective, *, search, **_kwargs):
        await search(objective, 3)
        raise AssertionError("ambiguous dispatch was replayed")

    def unexpected_dispatch(*_args, **_kwargs):
        raise AssertionError("external client reached after ambiguous intent")

    monkeypatch.setattr(live_runner, "run_query", pipeline)
    monkeypatch.setattr(live_runner, "SearXNGClient", unexpected_dispatch)
    with pytest.raises(OutcomeUnknownError):
        await live_runner.run_live(record, record.app)


class EmptyAuthority:
    def __init__(self):
        self.lock = asyncio.Lock()
        self.commits = 0

    @asynccontextmanager
    async def execution_lock(self, *_args):
        async with self.lock:
            yield

    async def find_run(self, *_args):
        return None

    async def commit(self, *_args, **_kwargs):
        self.commits += 1
        raise AssertionError("cancelled or failed worker tried publishing")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure,code",
    [
        (ValueError("bad evidence"), "research_run_failed"),
        (OutcomeUnknownError("lost receipt"), "outcome_unknown"),
    ],
)
async def test_terminal_failure_survives_worker_loss(
    ledger, monkeypatch, failure, code
):
    record = admitted(ledger)
    record.artifact_authority = EmptyAuthority()

    async def fail(*_args):
        raise failure

    monkeypatch.setattr(routes, "run_live", fail)
    await routes._execute_run(record)
    restored = routes._restore_durable_record(ledger.get(record.run_id))
    assert restored.state == "failed"
    assert restored.error == {"code": code, "retryable": False}
    assert restored.events[-1].event == "error"
    assert record.artifact_authority.commits == 0


@pytest.mark.asyncio
async def test_heartbeat_and_remote_cancel_fence_publication(ledger, monkeypatch):
    record = admitted(ledger)
    record.artifact_authority = EmptyAuthority()
    started, release = asyncio.Event(), asyncio.Event()

    async def blocked(*_args):
        started.set()
        await release.wait()
        return object()

    monkeypatch.setattr(routes, "run_live", blocked)
    record.task = asyncio.create_task(routes._execute_run(record))
    await asyncio.wait_for(started.wait(), timeout=2)
    await asyncio.sleep(2.2)  # longer than the actual default lease
    with pytest.raises(DurableConflictError):
        ledger.claim(record.run_id, owner_id="other-process", attempt_id="duplicate")
    ledger.cancel(record.run_id, terminal_payload={"research_id": record.research_id})
    release.set()
    await asyncio.wait_for(record.task, timeout=2)
    routes._check_retained_ledger(record)
    assert record.state == "cancelled"
    assert record.artifact_authority.commits == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "policy",
    [None, "durable-live/2", "durable-live/3", "durable-live/4", "durable-live/5"],
)
async def test_real_construction_and_review_replay_without_new_model_calls(
    ledger, monkeypatch, policy
):
    from agent.experimental import real_journey

    from tests.unit.test_query_construction import TEXT, batch_decisions, proposal

    record = admitted(ledger, policy, max_tokens=8192)
    model_calls = []
    search_calls = []

    class Search:
        def __init__(self, *_args, **_kwargs):
            pass

        async def search(self, query, **_kwargs):
            search_calls.append(query)
            return [{"url": "https://source.test/pilot"}], SimpleNamespace(
                empty_result=False
            )

        async def close(self):
            pass

    class Scraper:
        async def scrape(self, *_args, **_kwargs):
            return {"success": True, "data": {"markdown": TEXT}}

    class Transport:
        def __init__(self, *_args, **kwargs):
            assert kwargs.get("timeout", 90) == (
                300
                if policy == "durable-live/5"
                else 180
                if policy == "durable-live/4"
                else 90
            )

        async def __call__(self, request):
            assert request.max_output_tokens == (
                8192
                if policy in {"durable-live/3", "durable-live/4", "durable-live/5"}
                else 2048
            )
            value = json.loads(request.payload)
            if "schema" in value:
                assert all(
                    ("omitted_lines" in source)
                    == (
                        policy in {"durable-live/3", "durable-live/4", "durable-live/5"}
                    )
                    for source in value["sources"]
                )
            if "schema" in value:
                assert (value.get("answer_basis") == "captured_sources") == (
                    policy in {"durable-live/4", "durable-live/5"}
                )
            model_calls.append(value)
            reply = (
                {
                    "reasoning": "Bounded controlled plan",
                    "research_strategy": "deep",
                    "focused_queries": [record.objective, "replication", "limitations"],
                }
                if value.get("planning")
                else proposal()
                if "schema" in value
                else batch_decisions(value)
            )
            return ModelReply(json.dumps(reply).encode(), "configured-test-model", 1, 1)

    original = real_journey.ConsolidatedJourney
    completed = []

    class InterruptAfterReview:
        def __init__(self, **kwargs):
            self.inner = original(**kwargs)

        async def run(self):
            result = await self.inner.run()
            completed.append(result)
            if len(completed) == 1:
                raise asyncio.CancelledError
            return result

    monkeypatch.setattr(live_runner, "SearXNGClient", Search)
    monkeypatch.setattr(live_runner, "ReviewTransport", Transport)
    monkeypatch.setattr(real_journey, "ConsolidatedJourney", InterruptAfterReview)
    record.app.state.scraper_client = Scraper()
    claim(record)
    with pytest.raises(asyncio.CancelledError):
        await live_runner.run_live(record, record.app)
    ledger.redis.delete(ledger._lease_key(record.run_id))
    claim(record)
    result = await live_runner.run_live(record, record.app)
    assert len(model_calls) == (3 if policy else 2)
    assert search_calls == (
        [record.objective, "replication", "limitations"]
        if policy
        else [record.objective]
    )
    assert result.candidate.admitted.manifest.research_id == record.research_id
    assert result.sources == completed[0].sources
    assert [report.body for report in result.reports] == [
        report.body for report in completed[0].reports
    ]
    assert not result.candidate.fixture_only


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", [False, True])
async def test_selected_root_liveness_rechecked_before_external_work(
    ledger, monkeypatch, changed
):
    from agent.experimental.source_store import StorageConflictError

    record = admitted(ledger)
    durable = ledger.get(record.run_id)
    body = b"Selected retained source"
    durable.payload["selected_sources"] = [
        {
            "url": None,
            "text": body.decode(),
            "retrieved_at": None,
            "origin_id": f"root:{uuid4()}:source-1",
            "lineage_id": hashlib.sha256(body).hexdigest(),
        }
    ]
    raw = json.loads(ledger.redis.get(ledger._run_key(record.run_id)))
    raw["payload"] = durable.payload
    ledger.redis.set(
        ledger._run_key(record.run_id), json.dumps(raw), px=ledger.retention_ms
    )

    class Authority(EmptyAuthority):
        async def read_evidence(self, *_args):
            if changed:
                return b"Changed retained source", "text/plain"
            raise StorageConflictError("selected root deleted")

    record.artifact_authority = Authority()

    def unexpected(*_args, **_kwargs):
        raise AssertionError("deleted/changed source reached model transport")

    monkeypatch.setattr(live_runner, "ReviewTransport", unexpected)
    claim(record)
    with pytest.raises((ValueError, StorageConflictError)):
        await live_runner.run_live(record, record.app)


@pytest.mark.asyncio
@pytest.mark.parametrize("freshness", [True, False])
async def test_evidence_failure_keeps_safe_terminal_details(
    ledger, monkeypatch, freshness
):
    from agent.experimental.publication_gate import UnverifiedResearchError

    record = admitted(ledger)
    record.artifact_authority = EmptyAuthority()
    failure = UnverifiedResearchError(freshness=freshness)

    async def fail(*_args):
        raise failure

    monkeypatch.setattr(routes, "run_live", fail)
    await routes._execute_run(record)
    restored = routes._restore_durable_record(ledger.get(record.run_id))
    assert restored.state == "failed"
    assert restored.error == {
        "code": "evidence_not_verified",
        "reason": failure.reason,
        "message": failure.message,
        "retryable": False,
    }
    assert restored.events[-1].error == restored.error
    assert record.artifact_authority.commits == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("blocked_phase", ["planning", "construction", "review"])
@pytest.mark.parametrize(
    "policy,budget", [("durable-live/4", 180), ("durable-live/5", 300)]
)
async def test_admitted_deadline_cancels_without_replaying_model(
    ledger, monkeypatch, blocked_phase, policy, budget
):
    from tests.unit.test_query_construction import TEXT, batch_decisions, proposal

    record = admitted(ledger, policy)
    saved = ledger.get(record.run_id)
    fixed_now = datetime.fromtimestamp(saved.created_at_ms / 1000 + budget - 0.5, UTC)
    monkeypatch.setattr(
        live_runner,
        "datetime",
        SimpleNamespace(
            now=lambda _tz: fixed_now,
            fromisoformat=datetime.fromisoformat,
        ),
    )
    calls = []

    class Search:
        def __init__(self, *_args, **_kwargs):
            pass

        async def search(self, *_args, **_kwargs):
            return [{"url": "https://source.test/pilot"}], SimpleNamespace(
                empty_result=False
            )

        async def close(self):
            pass

    class Scraper:
        async def scrape(self, *_args, **_kwargs):
            return {"success": True, "data": {"markdown": TEXT}}

    class Transport:
        def __init__(self, *_args, **kwargs):
            assert kwargs["timeout"] == budget

        async def __call__(self, request):
            payload = json.loads(request.payload)
            phase = (
                "planning"
                if payload.get("planning")
                else "construction"
                if "schema" in payload
                else "review"
            )
            calls.append(phase)
            if phase == blocked_phase:
                await asyncio.Event().wait()
            reply = (
                {
                    "reasoning": "Controlled plan",
                    "research_strategy": "deep",
                    "focused_queries": [record.objective, "replication", "limitations"],
                }
                if phase == "planning"
                else proposal()
                if phase == "construction"
                else batch_decisions(payload)
            )
            return ModelReply(json.dumps(reply).encode(), "test-model", 1, 1)

    monkeypatch.setattr(live_runner, "ReviewTransport", Transport)
    monkeypatch.setattr(live_runner, "SearXNGClient", Search)
    record.app.state.scraper_client = Scraper()
    claim(record)
    with pytest.raises(TimeoutError):
        await live_runner.run_live(record, record.app)
    expected = ["planning", "construction", "review"]
    assert calls == expected[: expected.index(blocked_phase) + 1]
    assert ledger.has_pending_dispatch(record.run_id)
    before = list(calls)
    # Review's public boundary sanitizes provider errors, including the ledger's
    # unknown-dispatch error. Both paths must refuse to call the model again.
    expected_error = ValueError if blocked_phase == "review" else OutcomeUnknownError
    with pytest.raises(expected_error):
        await live_runner.run_live(record, record.app)
    assert calls == before
    assert ledger.has_pending_dispatch(record.run_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("save_receipt", [True, False])
async def test_known_output_limit_receipt_survives_recovery_without_dispatch(
    ledger, monkeypatch, save_receipt
):
    from agent.experimental.model_failure import ModelOutputLimitError

    record = admitted(ledger)
    calls = []

    class Transport:
        def __init__(self, *_args, **_kwargs):
            pass

        async def __call__(self, request):
            calls.append(request)
            raise ModelOutputLimitError(
                http_status=200, input_tokens=10, output_tokens=8192
            )

    async def query(_objective, **kwargs):
        await kwargs["complete"](
            ReviewRequest("test", b"{}", "configured-test-model", 32)
        )

    monkeypatch.setattr(live_runner, "ReviewTransport", Transport)
    monkeypatch.setattr(live_runner, "run_query", query)
    claim(record)
    original_step = ledger.step

    def step(*args, **kwargs):
        if kwargs.get("complete") and not save_receipt:
            raise RuntimeError("simulated receipt write interruption")
        return original_step(*args, **kwargs)

    monkeypatch.setattr(ledger, "step", step)
    with pytest.raises(ModelOutputLimitError if save_receipt else RuntimeError):
        await live_runner.run_live(record, record.app)
    assert ledger.has_pending_dispatch(record.run_id) is not save_receipt
    if not save_receipt:
        with pytest.raises(OutcomeUnknownError):
            await live_runner.run_live(record, record.app)
        assert len(calls) == 1
        return
    retained = ledger.get(record.run_id)
    assert (
        json.loads(ledger.redis.get(ledger._run_key(record.run_id)))["operations"][
            "model:1"
        ]["state"]
        == "receipt"
    )
    recovered = routes._restore_durable_record(retained)
    recovered.durable_ledger = ledger
    recovered.app = record.app
    recovered.durable_owner_id = record.durable_owner_id
    recovered.durable_generation = record.durable_generation
    with pytest.raises(ModelOutputLimitError) as caught:
        await live_runner.run_live(recovered, recovered.app)
    assert caught.value.diagnostics["output_tokens"] == 8192
    assert len(calls) == 1
    assert not ledger.has_pending_dispatch(record.run_id)
