"""Cache candidates are not accepted replays or completed live jobs."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from agent.models import AgentRequest, CitationStyle
from agent.research.streaming import stream_cached_artifact
from agent.research_cache_metrics import record_execution
from agent.routes.agent import _lookup_agent_cache

from common.metrics import METRICS
from tests.service.test_research_memory import (
    TestWorkerStaleWhileRevalidate as WorkerFixture,
)
from tests.service.test_research_memory import (
    _make_memory,
    _result,
    _stored_entry,
)


def count(name, **labels):
    label_text = ",".join(f'{k}="{v}"' for k, v in sorted(labels.items()))
    key = f"groktocrawl_{name}{{{label_text}}}"
    for line in METRICS.generate_openmetrics().splitlines():
        if line.startswith(key + " "):
            return float(line.split()[-1])
    return 0


@pytest.mark.asyncio
@pytest.mark.parametrize("age,outcome", [(1, "fresh"), (50, "aging"), (100, "stale")])
async def test_lookup_is_not_replay(age, outcome):
    entry = _stored_entry("m", age_hours=age)
    memory, _ = _make_memory({"memory:m:data": json.dumps(entry)}, [_result("m")])
    before = count("research_memory_lookup_total", outcome=outcome)
    with patch("agent.research_cache_metrics.inc_counter") as decision:
        result = await memory.query("q")
    assert result["freshness"] == outcome
    assert count("research_memory_lookup_total", outcome=outcome) == before + 1
    decision.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reason",
    [
        "similarity",
        "missing_id",
        "missing_artifact",
        "invalid_artifact",
        "compatibility",
        "freshness",
        "max_age",
    ],
)
async def test_rejected_candidate_is_one_miss(reason):
    entry = _stored_entry("m", fingerprint="fp")
    results = [_result("m")]
    kwargs = {}
    if reason == "similarity":
        results = [_result("m", score=0.1)]
    elif reason == "missing_id":
        results = [_result("")]
    elif reason == "compatibility":
        kwargs["fingerprint"] = "other"
    elif reason == "freshness":
        entry["created_at"] = "invalid"
    elif reason == "max_age":
        kwargs["max_age_hours"] = 0.1
    raw = "invalid" if reason == "invalid_artifact" else json.dumps(entry)
    store = {} if reason == "missing_artifact" else {"memory:m:data": raw}
    memory, _ = _make_memory(store, results)
    before = count("research_memory_lookup_total", outcome="miss")
    rejected = count(
        "research_memory_rejections_total", boundary="query", reason=reason
    )
    assert not (await memory.query("q", **kwargs))["hit"]
    assert count("research_memory_lookup_total", outcome="miss") == before + 1
    assert (
        count("research_memory_rejections_total", boundary="query", reason=reason)
        == rejected + 1
    )


@pytest.mark.asyncio
async def test_unexpected_lookup_error_is_not_miss():
    memory, _ = _make_memory({}, [{"score": "malformed"}])
    before = count("research_memory_lookup_total", outcome="error")
    with pytest.raises(ValueError):
        await memory.query("q")
    assert count("research_memory_lookup_total", outcome="error") == before + 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,reason",
    [("force_fresh", "force_fresh"), ("include_source_content", "source_content")],
)
async def test_stream_bypass_has_no_lookup(field, reason):
    request = MagicMock()
    request.app.state.research_memory.query = AsyncMock()
    before = count("research_memory_bypass_total", mode="stream", reason=reason)
    assert (
        await _lookup_agent_cache(
            request, AgentRequest(prompt="q", **{field: True}), "fp"
        )
        is None
    )
    request.app.state.research_memory.query.assert_not_awaited()
    assert (
        count("research_memory_bypass_total", mode="stream", reason=reason)
        == before + 1
    )


@pytest.mark.asyncio
async def test_stream_stale_policy_rejection():
    request = MagicMock()
    request.app.state.research_memory.query = AsyncMock(
        return_value={"hit": True, "freshness": "stale"}
    )
    with patch("agent.routes.agent.record_rejection") as rejection:
        assert (
            await _lookup_agent_cache(request, AgentRequest(prompt="q"), "fp") is None
        )
    rejection.assert_called_once_with("stream", "stale_policy")


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", [False, True])
async def test_stream_replay_only_after_citation_validation(invalid):
    with (
        patch("agent.research.streaming.record_execution") as execution,
        patch("agent.research.streaming.record_rejection") as rejection,
    ):
        events = [
            event
            async for event in stream_cached_artifact(
                artifact_text="answer [99]" if invalid else "answer [1]",
                sources=[{"url": "https://example.com"}],
                memory_id="private-id",
                freshness="fresh",
                similarity=0.99,
                citation_style=CitationStyle.inline,
                has_schema=False,
            )
        ]
    if invalid:
        execution.assert_not_called()
        rejection.assert_called_once_with("stream", "citation")
        assert '"type": "error"' in events[0]
    else:
        execution.assert_called_once()
        assert execution.call_args.args == ("stream", "replay", "fresh")
        rejection.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("swr", [False, True])
async def test_job_stale_replay_and_live_are_distinct(swr):
    with patch("agent.worker.record_execution") as execution:
        await WorkerFixture()._run_stale(stale_while_revalidate=swr, swr_eligible=True)
    execution.assert_called_once()
    assert execution.call_args.args == (
        ("job", "replay", "stale") if swr else ("job", "live")
    )


def test_freshness_labels_cannot_leak_artifact_content():
    with patch("agent.research_cache_metrics.inc_counter") as counter:
        record_execution("stream", "replay", "private artifact text")
    assert counter.call_args.args[2] == {
        "mode": "stream",
        "path": "replay",
        "freshness": "none",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    [
        "fresh",
        "aging",
        "citation",
        "publication",
        "force_fresh",
        "source_content",
        "unavailable",
    ],
)
async def test_job_acceptance_boundary(case):
    from agent.worker import _process_agent_async

    store = MagicMock()
    store.complete_job.return_value = case != "publication"
    store.get_job.return_value = {"status": "running"}
    memory = MagicMock()
    memory.query = AsyncMock(
        return_value={
            "hit": True,
            "freshness": "aging" if case == "aging" else "fresh",
            "artifact": {
                "artifact": "answer [99]" if case == "citation" else "answer [1]",
                "sources": [{"url": "https://example.com"}],
            },
        }
    )
    memory.store = AsyncMock(return_value=None)
    live = AsyncMock(return_value={"result": "live", "source_details": []})
    with (
        patch("agent.worker.JobStore", return_value=store),
        patch("agent.worker.run_research", live),
        patch("agent.worker.deliver_webhook", AsyncMock()),
        patch("agent.worker.record_execution") as execution,
        patch("agent.worker.record_rejection") as rejection,
        patch("agent.worker.record_bypass") as bypass,
    ):
        await _process_agent_async(
            job_id="fixture",
            prompt="question",
            urls=None,
            schema_=None,
            llm_base_url="http://llm",
            llm_api_key="fixture",
            llm_model="fixture",
            searxng_url="http://search",
            scraper_url="http://scraper",
            research_memory=None if case == "unavailable" else memory,
            force_fresh=case == "force_fresh",
            include_source_content=case == "source_content",
        )
    if case in {"fresh", "aging"}:
        assert execution.call_args.args == ("job", "replay", case)
        assert execution.call_count == 1
        live.assert_not_awaited()
        rejection.assert_not_called()
    elif case == "publication":
        execution.assert_not_called()
        live.assert_not_awaited()
        rejection.assert_called_once_with("job", "publication")
    else:
        execution.assert_called_once_with("job", "live")
        live.assert_awaited_once()
        if case == "citation":
            rejection.assert_called_once_with("job", "citation")
        else:
            bypass.assert_called_once_with("job", case)
            memory.query.assert_not_awaited()


@pytest.mark.asyncio
async def test_stream_live_count_is_at_generator_consumption():
    from agent.research.streaming import stream_research_live

    async def empty_pipeline(**kwargs):
        if False:
            yield {}

    with (
        patch("agent.research.streaming.run_research_stream", empty_pipeline),
        patch("agent.research.streaming.record_execution") as execution,
    ):
        stream = stream_research_live(
            prompt="q",
            urls=None,
            schema=None,
            searxng_url="http://search",
            scraper_url="http://scraper",
            llm_base_url="http://llm",
            llm_api_key="fixture",
            llm_model="fixture",
            requested_model=None,
            max_searches_per_request=1,
            include_images=False,
            citation_style=CitationStyle.inline,
        )
        execution.assert_not_called()
        _ = [event async for event in stream]
        execution.assert_called_once_with("stream", "live")


@pytest.mark.asyncio
async def test_stream_without_memory_is_bypass():
    request = MagicMock()
    request.app.state.research_memory = None
    with patch("agent.routes.agent.record_bypass") as bypass:
        assert (
            await _lookup_agent_cache(request, AgentRequest(prompt="q"), "fp") is None
        )
    bypass.assert_called_once_with("stream", "unavailable")


def test_replay_counter_and_histogram_share_accepted_boundary():
    import time

    labels = {"mode": "job", "freshness": "aging"}
    before = count("research_memory_replay_seconds_count", **labels)
    accepted = count("agent_execution_total", path="replay", **labels)
    record_execution("job", "replay", "aging", started=time.monotonic())
    assert count("research_memory_replay_seconds_count", **labels) == before + 1
    assert count("agent_execution_total", path="replay", **labels) == accepted + 1


@pytest.mark.asyncio
async def test_cancelled_lookup_preserves_cancellation_without_false_miss():
    memory, _ = _make_memory({}, [])
    memory._embed = AsyncMock(side_effect=asyncio.CancelledError())
    before = count("research_memory_lookup_total", outcome="cancelled")
    missed = count("research_memory_lookup_total", outcome="miss")
    with pytest.raises(asyncio.CancelledError):
        await memory.query("q")
    assert count("research_memory_lookup_total", outcome="cancelled") == before + 1
    assert count("research_memory_lookup_total", outcome="miss") == missed


@pytest.mark.asyncio
async def test_stream_serialization_failure_is_not_accepted_replay():
    with patch("agent.research.streaming.record_execution") as execution:
        with pytest.raises(TypeError):
            _ = [
                event
                async for event in stream_cached_artifact(
                    artifact_text="answer [1]",
                    sources=[{"url": "https://example.com"}],
                    memory_id="fixture",
                    freshness="fresh",
                    similarity=0.9,
                    citation_style=CitationStyle.inline,
                    has_schema=False,
                    coverage={"not_serializable": object()},
                )
            ]
        execution.assert_not_called()
