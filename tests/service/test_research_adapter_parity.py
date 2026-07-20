"""Intent coverage for #459: polling and live-SSE research adapters stay semantically aligned.

Cancellation, restart, and crash cases are intentionally excluded: research has no
common cancellation cut point and recovery is deferred by #458.
"""

from __future__ import annotations

import copy
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest


def _normalize(value: Any) -> Any:
    """Remove only token chunking and nondeterministic latency from event data."""
    if isinstance(value, list):
        return [
            _normalize(item)
            for item in value
            if not (isinstance(item, dict) and item.get("type") == "token")
        ]
    if isinstance(value, dict):
        return {
            key: _normalize(item) for key, item in value.items() if key != "latency_ms"
        }
    return value


def _semantic_memory_input(run: Any) -> dict[str, Any]:
    """Keep all memory admission semantics except the measured latency."""
    return _normalize(run["memory_admission"])


def _install_clients(
    monkeypatch: pytest.MonkeyPatch, data: dict[str, Any]
) -> dict[str, Any]:
    """Install fresh clients so one adapter cannot leak calls into the other."""
    from agent.research import loop

    searxng = MagicMock()
    searxng.search = AsyncMock(return_value=([data["search_result"]], True))
    searxng.close = AsyncMock()
    scraper = MagicMock()
    scraper.scrape_with_fallback = AsyncMock(
        return_value={
            "success": True,
            "data": {"markdown": "Deterministic source content.", "source": "scenario"},
        }
    )
    scraper.close = AsyncMock()
    llm = MagicMock()

    async def generate(**kwargs: Any) -> str:
        system_prompt = kwargs["system_prompt"]
        if system_prompt == "You are a research gap analyzer.":
            return "[]"
        if "focused_queries" in system_prompt:
            return json.dumps(
                {
                    "focused_queries": ["deterministic evidence"],
                    "research_strategy": "focused",
                    "reasoning": "The scenario requires one discovery pass.",
                }
            )
        return data["result"]

    llm.generate = AsyncMock(side_effect=generate)
    llm.close = AsyncMock()

    async def generate_stream(**_kwargs: Any) -> Any:
        yield {"type": "token", "content": "The source "}
        yield {"type": "token", "content": "established the expected fact [1]."}
        yield {"type": "done", "full_content": data["result"]}

    llm.generate_stream = generate_stream
    search_budget: dict[str, int] = {}

    def make_searxng(*_args: Any, **kwargs: Any) -> MagicMock:
        search_budget["value"] = kwargs["max_searches"]
        return searxng

    monkeypatch.setattr(loop, "SearXNGClient", make_searxng)
    monkeypatch.setattr(loop, "ScraperClient", lambda *_args, **_kwargs: scraper)
    monkeypatch.setattr(loop, "LLMClient", lambda *_args, **_kwargs: llm)
    return {"searxng": searxng, "scraper": scraper, "budget": search_budget}


def _record_canonical_events(
    monkeypatch: pytest.MonkeyPatch, original: Any
) -> list[dict[str, Any]]:
    from agent.research import loop

    recorded: list[dict[str, Any]] = []

    async def record_events(*args: Any, **kwargs: Any) -> Any:
        async for event in original(*args, **kwargs):
            recorded.append(copy.deepcopy(event))
            yield event

    monkeypatch.setattr(loop, "_run_research_events", record_events)
    return recorded


async def _run_polling(
    monkeypatch: pytest.MonkeyPatch, data: dict[str, Any], original_events: Any
) -> dict[str, Any]:
    from agent.models import CitationStyle
    from agent.worker import _process_agent_async

    clients = _install_clients(monkeypatch, data)
    events = _record_canonical_events(monkeypatch, original_events)
    store = MagicMock()
    memory = MagicMock()
    memory.store = AsyncMock(return_value="memory-polling")
    metrics = MagicMock()
    metrics.counter.return_value.inc = MagicMock()
    metrics.histogram.return_value.observe = MagicMock()

    monkeypatch.setattr("agent.worker.JobStore", lambda *_args: store)
    monkeypatch.setattr("agent.worker.deliver_webhook", AsyncMock())
    monkeypatch.setattr("agent.worker.METRICS", metrics)
    monkeypatch.setattr(
        "agent.worker.load_settings",
        lambda: MagicMock(valkey_host="valkey", valkey_port=6379, valkey_db=0),
    )

    await _process_agent_async(
        job_id="polling-job",
        prompt=data["prompt"],
        urls=None,
        schema_=None,
        llm_base_url="http://llm",
        llm_api_key="test-key",
        llm_model="scenario-model",
        searxng_url="http://searxng",
        scraper_url="http://scraper",
        citation_style=CitationStyle.inline,
        force_fresh=True,
        research_memory=memory,
        search_type="focused",
        max_searches_per_request=data["max_searches_per_request"],
    )

    return {
        "canonical_events": events,
        "final": store.complete_job.call_args.args[1],
        "search_budget": clients["budget"]["value"],
        "memory_admission": memory.store.call_args.kwargs,
        "search_calls": clients["searxng"].search.await_count,
        "scrape_calls": clients["scraper"].scrape_with_fallback.await_count,
    }


async def _run_streaming(
    monkeypatch: pytest.MonkeyPatch, data: dict[str, Any], original_events: Any
) -> dict[str, Any]:
    from agent.models import CitationStyle
    from agent.research.streaming import stream_research_live

    clients = _install_clients(monkeypatch, data)
    events = _record_canonical_events(monkeypatch, original_events)
    memory = MagicMock()
    memory.store = AsyncMock(return_value="memory-streaming")
    chunks = [
        chunk
        async for chunk in stream_research_live(
            prompt=data["prompt"],
            urls=None,
            schema=None,
            searxng_url="http://searxng",
            scraper_url="http://scraper",
            llm_base_url="http://llm",
            llm_api_key="test-key",
            llm_model="scenario-model",
            requested_model=None,
            max_searches_per_request=data["max_searches_per_request"],
            include_images=False,
            citation_style=CitationStyle.inline,
            research_memory=memory,
            search_type="focused",
        )
    ]
    emitted = [
        json.loads(chunk.removeprefix("data: "))
        for chunk in chunks
        if chunk != "data: [DONE]\n\n"
    ]

    return {
        "canonical_events": events,
        "final": next(event for event in reversed(emitted) if event["type"] == "done"),
        "search_budget": clients["budget"]["value"],
        "memory_admission": memory.store.call_args.kwargs,
        "search_calls": clients["searxng"].search.await_count,
        "scrape_calls": clients["scraper"].scrape_with_fallback.await_count,
    }


@pytest.mark.asyncio
async def test_successful_research_has_polling_streaming_parity(
    monkeypatch: pytest.MonkeyPatch, research_parity_data: dict[str, Any]
) -> None:
    """Intent: one plan/search/scrape/synthesis/completion has the same observable meaning."""
    from agent.research import loop

    original_events = loop._run_research_events
    polling = await _run_polling(monkeypatch, research_parity_data, original_events)
    streaming = await _run_streaming(monkeypatch, research_parity_data, original_events)

    # Intent: prove the scenario did not accidentally skip its required pipeline stages.
    assert polling["search_calls"] == streaming["search_calls"] == 1
    assert polling["scrape_calls"] == streaming["scrape_calls"] == 1

    polling_events = _normalize(polling["canonical_events"])
    streaming_events = _normalize(streaming["canonical_events"])
    assert [event["type"] for event in polling_events] == [
        "status",
        "research_plan",
        "research_pass",
        "status",
        "sources_pending",
        "source_scraped",
        "status",
        "sources",
        "done",
    ]
    assert [
        event["state"] for event in polling_events if event["type"] == "status"
    ] == ["planning", "searching", "synthesizing"]

    polling_final = {key: polling["final"][key] for key in ("result", "sources")}
    streaming_final = {key: streaming["final"][key] for key in ("result", "sources")}
    differences = {
        "meaningful_events": (polling_events, streaming_events),
        "final_result": (polling_final, streaming_final),
        "source_set": (
            set(polling["final"]["sources"]),
            set(streaming["final"]["sources"]),
        ),
        "search_budget": (polling["search_budget"], streaming["search_budget"]),
        "memory_admission": (
            _semantic_memory_input(polling),
            _semantic_memory_input(streaming),
        ),
    }
    mismatches = {
        name: values for name, values in differences.items() if values[0] != values[1]
    }

    assert not mismatches, f"Polling/streaming research parity mismatch: {mismatches}"
