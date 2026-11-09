"""Citation validation at the actual synthesis and public SSE boundaries."""

import asyncio
import json
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from agent.exceptions import CitationValidationError
from agent.models import CitationStyle
from agent.research.loop import _run_research_events, run_answer, run_answer_stream

SOURCES = [
    {"url": "https://example.com/one", "title": "Example one", "source": "fixture"},
    {"url": "https://example.com/two", "title": "Example two", "source": "fixture"},
]


@pytest.fixture
def synthesis_clients():
    search = MagicMock()
    search.search = AsyncMock(return_value=(SOURCES, {"status": "ok"}))
    search.close = AsyncMock()
    scraper = MagicMock()
    scraper.close = AsyncMock()
    llm = MagicMock()
    llm.close = AsyncMock()
    with ExitStack() as stack:
        for name, client in (
            ("SearXNGClient", search),
            ("ScraperClient", scraper),
            ("LLMClient", llm),
        ):
            stack.enter_context(
                patch(f"agent.research.loop.{name}", return_value=client)
            )
        stack.enter_context(
            patch(
                "agent.research.loop._generate_research_plan",
                AsyncMock(
                    return_value={
                        "focused_queries": ["question"],
                        "research_strategy": "focused",
                        "reasoning": "fixture",
                    }
                ),
            )
        )
        stack.enter_context(
            patch(
                "agent.research.loop._run_research_discover_and_scrape",
                AsyncMock(
                    return_value={
                        "context": "source context",
                        "source_details": SOURCES,
                        "search_results": SOURCES,
                    }
                ),
            )
        )
        stack.enter_context(
            patch(
                "agent.research.loop._scrape_answer_sources", AsyncMock(return_value=[])
            )
        )
        stack.enter_context(
            patch(
                "agent.research.loop._build_answer_context",
                return_value={
                    "context": "source context",
                    "source_map": SOURCES,
                    "evidence_coverage": {},
                },
            )
        )
        stack.enter_context(
            patch(
                "agent.research.loop._run_answer_discover_and_scrape",
                AsyncMock(
                    return_value={
                        "context": "source context",
                        "source_map": SOURCES,
                        "evidence_coverage": {},
                    }
                ),
            )
        )
        yield llm, search, scraper


def synthesis(kind, style=CitationStyle.compact, schema=None):
    if kind == "answer":
        return run_answer_stream(
            "question", llm_model="fixture", citation_style=style, output_schema=schema
        )
    return _run_research_events(
        "question",
        llm_model="fixture",
        citation_style=style,
        schema=schema,
        search_type="focused",
        stream_tokens=True,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["answer", "research"])
@pytest.mark.parametrize("style", list(CitationStyle))
async def test_sources_arrive_live_but_answer_waits_for_validation_and_equals_done(
    synthesis_clients, kind, style
):
    llm, search, scraper = synthesis_clients
    release = asyncio.Event()
    started = asyncio.Event()

    async def provider(**kwargs):
        started.set()
        yield {"type": "token", "content": "Claim [1,"}
        await release.wait()
        yield {"type": "token", "content": " 2] is supported."}
        yield {"type": "done", "full_content": "Claim [1, 2] is supported."}

    llm.generate_stream = MagicMock(side_effect=provider)
    stream = synthesis(kind, style)
    events = []

    async def consume():
        async for event in stream:
            events.append(event)

    task = asyncio.create_task(consume())
    await asyncio.wait_for(started.wait(), 2)
    assert not any(event["type"] == "token" for event in events)
    assert any(event["type"] == "sources" for event in events)
    release.set()
    await asyncio.wait_for(task, 2)
    expected = (
        "Claim [1](https://example.com/one)[2](https://example.com/two) is supported."
        if style == CitationStyle.compact
        else "Claim [1][2] is supported."
    )
    assert (
        "".join(event["content"] for event in events if event["type"] == "token")
        == expected
    )
    assert events[-1]["answer" if kind == "answer" else "result"] == expected
    llm.generate_stream.assert_called_once()
    for client in (llm, search, scraper):
        client.close.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["answer", "research"])
@pytest.mark.parametrize(
    "text,full",
    [
        ("Claim [3].", "Claim [3]."),
        ("Claim [1", "Claim [1"),
        ("Claim [1-2].", "Claim [1-2]."),
        ("Claim [1](https://wrong.example).", "Claim [1](https://wrong.example)."),
        ("Claim [1].", "A different [1]."),
    ],
)
async def test_invalid_citations_terminate_public_sse(
    synthesis_clients, kind, text, full
):
    from agent.research.streaming import stream_research_live
    from agent.routes.agent import _serialize_answer_stream

    llm, _, _ = synthesis_clients

    async def provider(**kwargs):
        for char in text:
            yield {"type": "token", "content": char}
        yield {"type": "done", "full_content": full}

    llm.generate_stream = MagicMock(side_effect=provider)
    memory = MagicMock(store=AsyncMock())
    if kind == "answer":
        stream = _serialize_answer_stream(synthesis(kind))
    else:
        stream = stream_research_live(
            "question",
            None,
            None,
            "http://search",
            "http://scraper",
            "http://llm",
            "key",
            "fixture",
            None,
            5,
            False,
            CitationStyle.compact,
            search_type="focused",
            research_memory=memory,
        )
    frames = [frame async for frame in stream]
    events = [json.loads(frame.removeprefix("data: ")) for frame in frames]
    assert events[-1]["type"] == "error"
    assert events[-1]["error_code"] == "CITATION_INVALID"
    assert events[-1]["classification"] == "non_retryable"
    assert not any(event["type"] == "done" for event in events)
    assert "data: [DONE]\n\n" not in frames
    memory.store.assert_not_called()
    assert llm.generate_stream.call_count == (2 if text == full else 1)
    assert not any(event["type"] == "token" for event in events)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["answer", "research"])
async def test_provider_error_does_not_flush_pending_citation(synthesis_clients, kind):
    llm, _, _ = synthesis_clients

    async def provider(**kwargs):
        yield {"type": "token", "content": "Claim [1"}
        yield {
            "type": "error",
            "content": "provider failed",
            "classification": "non_retryable",
        }

    llm.generate_stream = provider
    events = [event async for event in synthesis(kind)]
    assert events[-1]["type"] == "error"
    assert "".join(e["content"] for e in events if e["type"] == "token") == ""
    assert not any(e["type"] == "done" for e in events)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["answer", "research"])
async def test_cancellation_propagates_and_closes_clients(synthesis_clients, kind):
    llm, search, scraper = synthesis_clients
    blocked = asyncio.Event()
    cancelled = asyncio.Event()

    async def provider(**kwargs):
        yield {"type": "token", "content": "Claim [1"}
        blocked.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    llm.generate_stream = provider
    events = []

    async def consume():
        async for event in synthesis(kind):
            events.append(event)

    task = asyncio.create_task(consume())
    await asyncio.wait_for(blocked.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancelled.is_set()
    assert not any(e["type"] == "done" for e in events)
    for client in (llm, search, scraper):
        client.close.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["answer", "research"])
async def test_schema_json_never_enters_citation_grammar(synthesis_clients, kind):
    llm, _, _ = synthesis_clients
    raw = '{"example":"[1]", "items":[1,2]}'
    llm.generate = AsyncMock(return_value=raw)
    events = [e async for e in synthesis(kind, schema={"type": "object"})]
    assert events[-1]["answer" if kind == "answer" else "result"] == raw
    assert not any(e["type"] == "token" for e in events)
    if kind == "answer":
        assert events[-1]["citations"] == []
    llm.generate.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("schema", [False, True])
async def test_cache_replay_validates_markdown_but_not_json(schema):
    from agent.research.streaming import stream_cached_artifact

    raw = '{"example":"[999]", "items":[1,2]}'
    frames = [
        frame
        async for frame in stream_cached_artifact(
            raw,
            SOURCES,
            "id",
            "fresh",
            1.0,
            CitationStyle.compact,
            schema,
        )
    ]
    events = [
        json.loads(frame.removeprefix("data: "))
        for frame in frames
        if "[DONE]" not in frame
    ]
    if schema:
        assert events[0]["type"] == "done"
        assert events[0]["result"] == raw
    else:
        assert events == [
            {
                "type": "error",
                "content": events[0]["content"],
                "error_code": "CITATION_INVALID",
                "classification": "non_retryable",
            }
        ]
        assert "data: [DONE]\n\n" not in frames


@pytest.mark.asyncio
async def test_finalize_schema_preserves_json_in_memory():
    from agent.research.memory import finalize_and_admit

    raw = '{"example":"[999]", "items":[1,2]}'
    memory = MagicMock(store=AsyncMock(return_value="id"))
    result = await finalize_and_admit(
        memory,
        prompt="question",
        result={"result": raw, "source_details": SOURCES},
        llm_model="fixture",
        citation_style=CitationStyle.compact,
        has_schema=True,
    )
    assert result["result"] == raw
    assert memory.store.call_args.kwargs["artifact"] == raw


@pytest.mark.asyncio
async def test_sync_answer_rejects_invalid_citations_after_one_correction(
    synthesis_clients,
):
    llm, _, _ = synthesis_clients
    llm.generate = AsyncMock(return_value="Claim [3].")

    with pytest.raises(CitationValidationError) as exc:
        await run_answer("question", llm_model="fixture")

    assert getattr(exc.value, "error_code", None) == "CITATION_INVALID"
    assert llm.generate.await_count == 2


@pytest.mark.asyncio
async def test_sync_answer_schema_keeps_json_opaque(synthesis_clients):
    llm, _, _ = synthesis_clients
    raw = '{"citation":"[1]"}'
    llm.generate = AsyncMock(return_value=raw)

    result = await run_answer(
        "question",
        llm_model="fixture",
        citation_style=CitationStyle.compact,
        output_schema={"type": "object"},
    )

    assert result["answer"] == raw
    assert result["citations"] == []


@pytest.mark.asyncio
async def test_sync_answer_corrects_citations_once_with_same_evidence(
    synthesis_clients,
):
    llm, _, _ = synthesis_clients
    llm.generate = AsyncMock(side_effect=["Wrong [3].", "Correct [1]."])
    result = await run_answer("question", llm_model="fixture")
    assert result["answer"] == "Correct [1]."
    assert llm.generate.await_count == 2
    first, second = [call.kwargs for call in llm.generate.await_args_list]
    assert first["context"] == second["context"] == "source context"
    assert first["user_prompt"] in second["user_prompt"]
    assert "citation" in second["user_prompt"].lower()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["answer", "research"])
@pytest.mark.parametrize("style", list(CitationStyle))
async def test_stream_corrects_completed_invalid_candidate_without_leaking_text(
    synthesis_clients, kind, style
):
    llm, _, _ = synthesis_clients
    attempts = []

    async def provider(**kwargs):
        attempts.append(kwargs)
        text = "Failed draft [3]." if len(attempts) == 1 else "Accepted [1]."
        for char in text:
            yield {"type": "token", "content": char}
        yield {"type": "done", "full_content": text}

    llm.generate_stream = provider
    events = [event async for event in synthesis(kind, style)]
    assert len(attempts) == 2
    assert attempts[0]["context"] == attempts[1]["context"]
    assert attempts[0]["system_prompt"] == attempts[1]["system_prompt"]
    assert attempts[0]["user_prompt"] in attempts[1]["user_prompt"]
    assert "Failed draft" not in attempts[1]["user_prompt"]
    assert len([e for e in events if e["type"] == "sources"]) == 1
    text = "".join(e["content"] for e in events if e["type"] == "token")
    expected = (
        "Accepted [1](https://example.com/one)."
        if style == CitationStyle.compact
        else "Accepted [1]."
    )
    assert text == expected
    assert events[-1]["answer" if kind == "answer" else "result"] == expected
    assert not any(e["type"] == "error" for e in events)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["answer", "research"])
@pytest.mark.parametrize(
    "failure", ["missing_done", "mismatch", "transport", "rate_limit"]
)
async def test_invalid_early_marker_then_provider_failure_never_retries(
    synthesis_clients, kind, failure
):
    llm, _, _ = synthesis_clients

    async def provider(**kwargs):
        yield {"type": "token", "content": "Invalid [999]."}
        if failure == "mismatch":
            yield {"type": "done", "full_content": "Different [1]."}
        elif failure in {"transport", "rate_limit"}:
            yield {
                "type": "error",
                "content": "upstream failure",
                "classification": "retryable"
                if failure == "rate_limit"
                else "non_retryable",
                **({"retry_after_seconds": 5} if failure == "rate_limit" else {}),
            }

    llm.generate_stream = MagicMock(side_effect=provider)
    events = [event async for event in synthesis(kind)]
    llm.generate_stream.assert_called_once()
    assert events[-1]["type"] == "error"
    if failure == "rate_limit":
        assert events[-1]["classification"] == "retryable"
        assert events[-1]["retry_after_seconds"] == 5
    assert not any(e["type"] in {"token", "done"} for e in events)


@pytest.mark.asyncio
@pytest.mark.parametrize("successful", [False, True])
async def test_polling_research_corrects_only_synthesis(synthesis_clients, successful):
    llm, _, _ = synthesis_clients
    llm.generate = AsyncMock(
        side_effect=["Draft [3].", "Final [1]." if successful else "Bad [3]."]
    )
    stream = _run_research_events(
        "question",
        llm_model="fixture",
        search_type="focused",
        stream_tokens=False,
    )
    if successful:
        events = [event async for event in stream]
        assert events[-1]["result"] == "Final [1]."
    else:
        with pytest.raises(CitationValidationError):
            [event async for event in stream]
    assert llm.generate.await_count == 2
    assert all(
        call.kwargs["stage"] == "synthesis" for call in llm.generate.await_args_list
    )
    assert (
        llm.generate.await_args_list[0].kwargs["context"]
        == llm.generate.await_args_list[1].kwargs["context"]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["answer", "research"])
async def test_cancellation_during_corrective_attempt_discards_both_drafts(
    synthesis_clients, kind
):
    llm, search, scraper = synthesis_clients
    correcting = asyncio.Event()
    cancelled = asyncio.Event()
    attempts = 0

    async def provider(**kwargs):
        nonlocal attempts
        attempts += 1
        yield {"type": "token", "content": "Draft [3]."}
        if attempts == 1:
            yield {"type": "done", "full_content": "Draft [3]."}
        else:
            correcting.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

    llm.generate_stream = provider
    events = []

    async def consume():
        async for event in synthesis(kind):
            events.append(event)

    task = asyncio.create_task(consume())
    await asyncio.wait_for(correcting.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert attempts == 2
    assert cancelled.is_set()
    assert not any(e["type"] in {"token", "done"} for e in events)
    for client in (llm, search, scraper):
        client.close.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("recovered", [False, True])
async def test_answer_http_boundary_publishes_only_verified_candidate(
    synthesis_clients, streaming, recovered
):
    from types import SimpleNamespace

    import httpx
    from agent.app import groktocrawl_error_handler
    from agent.exceptions import GroktoCrawlError
    from agent.routes.agent import router
    from fastapi import FastAPI

    llm, _, _ = synthesis_clients
    responses = [
        "Private rejected draft [3].",
        "Accepted [1]." if recovered else "Still invalid [3].",
    ]
    llm.generate = AsyncMock(side_effect=responses)
    calls = 0

    async def provider(**kwargs):
        nonlocal calls
        text = responses[calls]
        calls += 1
        yield {"type": "token", "content": text}
        yield {"type": "done", "full_content": text}

    llm.generate_stream = provider
    app = FastAPI()
    app.include_router(router)
    app.add_exception_handler(GroktoCrawlError, groktocrawl_error_handler)
    for key, value in {
        "rate_limiter": SimpleNamespace(
            check=AsyncMock(return_value=(True, 9)), limit=10
        ),
        "max_searches_per_request": 5,
        "searxng_url": "http://search.fixture",
        "scraper_url": "http://scraper.fixture",
        "semantic_url": "http://semantic.fixture",
        "llm_base_url": "http://llm.fixture",
        "llm_api_key": "",
        "llm_model": "fixture",
    }.items():
        setattr(app.state, key, value)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/v2/answer", json={"query": "question", "stream": streaming}
        )
    assert "Private rejected draft" not in response.text
    assert (calls if streaming else llm.generate.await_count) == 2
    if streaming:
        assert response.status_code == 200
        events = [
            json.loads(line[6:])
            for line in response.text.splitlines()
            if line.startswith("data: ") and line != "data: [DONE]"
        ]
        assert events[-1]["type"] == ("done" if recovered else "error")
        if recovered:
            assert events[-1]["answer"] == "Accepted [1]."
            assert (
                "".join(e["content"] for e in events if e["type"] == "token")
                == "Accepted [1]."
            )
        else:
            assert events[-1]["error_code"] == "CITATION_INVALID"
            assert not any(e["type"] == "token" for e in events)
            assert "data: [DONE]" not in response.text
    else:
        assert response.status_code == (200 if recovered else 502)
        if recovered:
            assert response.json()["answer"] == "Accepted [1]."
        else:
            assert response.json()["error_code"] == "CITATION_INVALID"


@pytest.mark.asyncio
@pytest.mark.parametrize("error_kind", ["provider", "rate_limit"])
async def test_sync_provider_failures_are_not_citation_retries(
    synthesis_clients, error_kind
):
    from agent.exceptions import ProviderOutputError, RetryableRateLimitError

    llm, _, _ = synthesis_clients
    error = (
        ProviderOutputError()
        if error_kind == "provider"
        else RetryableRateLimitError(retry_after_seconds=5)
    )
    llm.generate = AsyncMock(side_effect=error)
    with pytest.raises(type(error)):
        await run_answer("question", llm_model="fixture")
    llm.generate.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "responses,outcome,calls",
    [
        (["Valid [1]."], None, 1),
        (["Invalid [3].", "Valid [1]."], "succeeded", 2),
        (["Invalid [3].", "Invalid [3]."], "exhausted", 2),
    ],
)
async def test_recovery_metrics_and_frozen_source_identity(
    monkeypatch, responses, outcome, calls
):
    from agent.research import citation_recovery

    from common.metrics import MetricsCollector

    collector = MetricsCollector()
    monkeypatch.setattr(citation_recovery, "METRICS", collector)
    citation_recovery.initialize_citation_metrics()
    sources = [dict(source) for source in SOURCES]
    count = 0

    async def generate(**kwargs):
        nonlocal count
        text = responses[count]
        count += 1
        sources[0]["url"] = "https://changed.invalid"
        return text

    llm = MagicMock(generate=AsyncMock(side_effect=generate))
    invocation = citation_recovery.generate_cited_answer(
        llm,
        system_prompt="grounded",
        user_prompt="question",
        context="evidence",
        source_map=sources,
        citation_style=CitationStyle.compact,
        stage="answer",
    )
    if outcome == "exhausted":
        with pytest.raises(CitationValidationError):
            await invocation
    else:
        answer, citations = await invocation
        assert answer == "Valid [1](https://example.com/one)."
        assert citations == [{"index": 1, "url": "https://example.com/one"}]
    assert llm.generate.await_count == calls
    metrics = collector.generate_openmetrics()
    assert (
        f'groktocrawl_citation_recovery_total{{outcome="attempted",stage="answer"}} {float(calls - 1)}'
        in metrics
    )
    for metric_outcome in ("succeeded", "exhausted"):
        assert (
            f'groktocrawl_citation_recovery_total{{outcome="{metric_outcome}",stage="answer"}} {float(outcome == metric_outcome)}'
            in metrics
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("successful", [False, True])
async def test_citation_correction_stays_one_live_execution(
    synthesis_clients, successful
):
    from agent.research.streaming import stream_research_live

    llm, _, _ = synthesis_clients
    answers = iter(["Draft [3].", "Final [1]." if successful else "Bad [3]."])

    async def provider(**kwargs):
        text = next(answers)
        yield {"type": "token", "content": text}
        yield {"type": "done", "full_content": text}

    llm.generate_stream = MagicMock(side_effect=provider)
    with patch("agent.research.streaming.record_execution") as execution:
        events = [
            event
            async for event in stream_research_live(
                prompt="question",
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
                search_type="focused",
            )
        ]
        execution.assert_called_once_with("stream", "live")
    assert llm.generate_stream.call_count == 2
    assert any('"type": "done"' in event for event in events) == successful
    assert any('"type": "error"' in event for event in events) != successful
