"""Final-only synthesis ordering, evidence, errors, and cancellation."""

import asyncio
from unittest.mock import patch

import pytest
from agent.llm import ProviderOutputError
from agent.research.loop import _run_research_events, run_research

from tests.service.test_issue624_source_registry import (
    _patch_research_clients,
    _research_clients,
)


def clients():
    return _research_clients(
        {
            "first-a": [{"url": "https://example.com/first"}],
            "first-b": [],
            "gap": [{"url": "https://example.com/second"}],
        }
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stream,schema", [(False, None), (True, None), (True, {"type": "object"})]
)
async def test_coverage_and_followup_finish_before_single_final_synthesis(
    stream, schema
):
    searxng, scraper, llm = all_clients = clients()
    synth_contexts = []

    async def generate(**kwargs):
        assert searxng.search.await_count == 3
        synth_contexts.append(kwargs["context"])
        return "{}" if schema else "final [1] [2]"

    async def generate_stream(**kwargs):
        answer = await generate(**kwargs)
        yield {"type": "token", "content": answer}
        yield {"type": "done", "full_content": answer}

    llm.generate.side_effect = generate
    llm.generate_stream = generate_stream
    with _patch_research_clients(*all_clients):
        events = [
            event
            async for event in _run_research_events(
                prompt="question",
                llm_model="fixture",
                stream_tokens=stream,
                schema=schema,
            )
        ]
    assert len(synth_contexts) == 1
    assert "first" in synth_contexts[0] and "second" in synth_contexts[0]
    assert events[-1]["type"] == "done"
    assert len(events[-1]["sources"]) == 2
    tokens = [e["content"] for e in events if e["type"] == "token"]
    if stream and not schema:
        assert "".join(tokens) == events[-1]["result"]
    else:
        assert tokens == []
    scraper.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_final_provider_error_has_no_successful_terminal_event():
    all_clients = clients()
    llm = all_clients[-1]
    llm.generate.side_effect = ProviderOutputError("bad output")
    with _patch_research_clients(*all_clients):
        events = [
            event
            async for event in _run_research_events(
                prompt="q",
                llm_model="fixture",
                stream_tokens=True,
                schema={"type": "object"},
            )
        ]
    assert events[-1]["type"] == "error"
    assert not any(e["type"] == "done" for e in events)
    assert llm.generate.await_count == 1


@pytest.mark.asyncio
async def test_cancelling_gap_analysis_prevents_synthesis_and_closes_clients():
    all_clients = clients()
    started, cancelled = asyncio.Event(), asyncio.Event()

    async def gaps(*args, **kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    with (
        _patch_research_clients(*all_clients),
        patch("agent.research.loop._detect_gaps", side_effect=gaps),
    ):
        task = asyncio.create_task(run_research(prompt="q", llm_model="fixture"))
        await asyncio.wait_for(started.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert cancelled.is_set()
    all_clients[-1].generate.assert_not_awaited()
    for client in all_clients:
        client.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_no_gap_generates_once_without_followup_search():
    all_clients = clients()
    with _patch_research_clients(*all_clients, gaps=False):
        await run_research(prompt="q", llm_model="fixture")
    assert all_clients[0].search.await_count == 2
    all_clients[-1].generate.assert_awaited_once()
