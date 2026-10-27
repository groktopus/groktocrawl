"""Gap-check failures must remain distinguishable from adequate evidence."""

from unittest.mock import AsyncMock, patch

import pytest
from agent.research import gaps
from agent.research.loop import _run_research_events

from tests.service.test_issue621_final_synthesis import clients
from tests.service.test_issue624_source_registry import _patch_research_clients


@pytest.mark.asyncio
@pytest.mark.parametrize("response", ["not json", "{}", "[1]", '[" "]', '["a", 2]'])
async def test_invalid_gap_output_is_not_adequate(response):
    llm = AsyncMock()
    llm.generate.return_value = response
    with pytest.raises(gaps.GapDetectionError):
        await gaps._detect_gaps("evidence", llm, "question")


@pytest.mark.asyncio
async def test_provider_failure_is_not_adequate():
    llm = AsyncMock()
    llm.generate.side_effect = RuntimeError("provider unavailable")
    with pytest.raises(RuntimeError, match="provider unavailable"):
        await gaps._detect_gaps("evidence", llm, "question")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response,expected", [("[]", []), ('["missing fact"]', ["missing fact"])]
)
async def test_valid_gap_output(response, expected):
    llm = AsyncMock()
    llm.generate.return_value = response
    assert await gaps._detect_gaps("evidence", llm, "question") == expected


@pytest.mark.asyncio
async def test_gap_failure_stops_without_followup_and_reports_unknown_coverage():
    all_clients = clients()
    with (
        _patch_research_clients(*all_clients),
        patch(
            "agent.research.loop._detect_gaps",
            side_effect=gaps.GapDetectionError("provider_error"),
        ),
    ):
        events = [
            event
            async for event in _run_research_events(
                prompt="q", llm_model="fixture", stream_tokens=True
            )
        ]
    assert events[-1]["coverage"]["gap_check"] == {"status": "failed"}
    assert events[-1]["type"] == "error"
    assert not any(event["type"] == "done" for event in events)
    assert all_clients[0].search.await_count == 2
    all_clients[-1].generate.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("response", ['["a", "b", "c", "d", "e", "f"]', "null"])
async def test_invalid_shape_or_excess_topics_fail(response):
    llm = AsyncMock()
    llm.generate.return_value = response
    with pytest.raises(gaps.GapDetectionError):
        await gaps._detect_gaps("evidence", llm, "question")


@pytest.mark.asyncio
async def test_polling_gap_failure_propagates_without_synthesis():
    all_clients = clients()
    with (
        _patch_research_clients(*all_clients),
        patch(
            "agent.research.loop._detect_gaps",
            side_effect=gaps.GapDetectionError("invalid output"),
        ),
        pytest.raises(gaps.GapDetectionError),
    ):
        async for _ in _run_research_events(
            prompt="q", llm_model="fixture", stream_tokens=False
        ):
            pass
    all_clients[-1].generate.assert_not_awaited()
    for client in all_clients:
        client.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_retryable_gap_failure_preserves_retry_delay():
    from agent.llm import RetryableRateLimitError

    all_clients = clients()
    with (
        _patch_research_clients(*all_clients),
        patch(
            "agent.research.loop._detect_gaps",
            side_effect=RetryableRateLimitError(retry_after_seconds=7),
        ),
    ):
        events = [
            event
            async for event in _run_research_events(
                prompt="q", llm_model="fixture", stream_tokens=True
            )
        ]
    assert events[-1]["type"] == "error"
    assert events[-1]["classification"] == "retryable"
    assert events[-1]["retry_after_seconds"] == 7
    all_clients[-1].generate.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("has_gaps,status", [(False, "adequate"), (True, "gaps")])
async def test_successful_gap_decision_is_explicit(has_gaps, status):
    all_clients = clients()
    with _patch_research_clients(*all_clients, gaps=has_gaps):
        events = [
            event
            async for event in _run_research_events(
                prompt="q", llm_model="fixture", stream_tokens=False
            )
        ]
    assert events[-1]["coverage"]["gap_check"] == {"status": status}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response,expected",
    [(' {"topics": []} ', []), ('{"topics": ["missing fact"]}', ["missing fact"])],
)
async def test_structured_gap_object_is_validated(response, expected):
    llm = AsyncMock()
    llm.generate.return_value = response
    assert await gaps._detect_gaps("evidence", llm, "question") == expected
    assert llm.generate.call_args.kwargs["schema"]["required"] == ["topics"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response", ['{"topics": [], "extra": true}', '{"topics": null}', '{"topics": [1]}']
)
async def test_invalid_structured_gap_object_fails(response):
    llm = AsyncMock()
    llm.generate.return_value = response
    with pytest.raises(gaps.GapDetectionError):
        await gaps._detect_gaps("evidence", llm, "question")


@pytest.mark.asyncio
async def test_real_client_default_twin_obeys_requested_gap_schema():
    import httpx
    from agent.llm import LLMClient
    from llm_svc.app import create_app

    llm = LLMClient("http://fixture/v1", "", "fixture-model")
    await llm._client.aclose()
    llm._client = httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()))
    try:
        topics = await gaps._detect_gaps("authoritative source text", llm, "question")
    finally:
        await llm.close()
    assert isinstance(topics, list) and all(isinstance(topic, str) for topic in topics)
