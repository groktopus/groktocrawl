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
async def test_tokens_arrive_before_provider_finishes_and_equal_done(
    synthesis_clients, kind, style
):
    llm, search, scraper = synthesis_clients
    release = asyncio.Event()

    async def provider(**kwargs):
        yield {"type": "token", "content": "Claim [1,"}
        await release.wait()
        yield {"type": "token", "content": " 2] is supported."}
        yield {"type": "done", "full_content": "Claim [1, 2] is supported."}

    llm.generate_stream = MagicMock(side_effect=provider)
    stream = synthesis(kind, style)
    events = []
    while not events or events[-1]["type"] != "token":
        events.append(await asyncio.wait_for(anext(stream), 2))
    assert not release.is_set()
    assert events[-1]["content"] == "Claim "
    assert any(event["type"] == "sources" for event in events)
    release.set()
    events.extend([event async for event in stream])
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
    llm.generate_stream.assert_called_once()


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
    assert "".join(e["content"] for e in events if e["type"] == "token") == "Claim "
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
async def test_sync_answer_rejects_invalid_citations_without_retry(synthesis_clients):
    llm, _, _ = synthesis_clients
    llm.generate = AsyncMock(return_value="Claim [3].")

    with pytest.raises(CitationValidationError) as exc:
        await run_answer("question", llm_model="fixture")

    assert getattr(exc.value, "error_code", None) == "CITATION_INVALID"
    llm.generate.assert_awaited_once()


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
