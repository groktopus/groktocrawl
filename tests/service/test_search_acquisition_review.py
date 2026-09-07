"""Regression checks for source identity, streaming, and full route reuse."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from agent.models import ContentsOptions, SearchRequest
from agent.research.acquisition import acquire_source_artifacts, stream_source_artifacts
from agent.research.sources import SourceArtifact


@pytest.mark.asyncio
async def test_distinct_case_query_order_and_trailing_slash_are_preserved():
    urls = [
        "https://example.test/A",
        "https://example.test/a",
        "https://example.test/a/",
        "https://example.test/?q=A",
        "https://example.test/?q=a",
        "https://example.test/?q=1&q=2",
        "https://example.test/?q=2&q=1",
    ]
    scraper = SimpleNamespace(
        scrape=AsyncMock(return_value={"success": True, "data": {"markdown": "page"}})
    )
    acquired = await acquire_source_artifacts([{"url": u} for u in urls], scraper)
    assert scraper.scrape.await_count == len(urls)
    assert len(acquired.artifacts) == len(urls)


@pytest.mark.asyncio
async def test_stream_emits_fast_artifact_before_slow_fetch_and_closes_children():
    slow_started = asyncio.Event()
    slow_cancelled = asyncio.Event()

    async def scrape(url):
        if url.endswith("slow"):
            slow_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                slow_cancelled.set()
        await slow_started.wait()
        return {"success": True, "data": {"markdown": "fast evidence"}}

    stream = stream_source_artifacts(
        [{"url": "https://example.test/slow"}, {"url": "https://example.test/fast"}],
        SimpleNamespace(scrape=scrape),
    )
    first = await asyncio.wait_for(anext(stream), timeout=1)
    assert isinstance(first, SourceArtifact) and first.url.endswith("fast")
    await stream.aclose()
    assert slow_cancelled.is_set()


@pytest.mark.asyncio
async def test_hybrid_rich_contents_route_fetches_each_source_once(monkeypatch):
    from agent.routes.search import search

    results = [
        {"url": f"https://example.test/{i}", "title": str(i), "description": "snippet"}
        for i in range(3)
    ]
    searcher = SimpleNamespace(
        search=AsyncMock(return_value=(results, None)), close=AsyncMock()
    )
    scraper = SimpleNamespace(
        scrape=AsyncMock(
            return_value={"success": True, "data": {"markdown": "actual full evidence"}}
        ),
        close=AsyncMock(),
    )
    llm = SimpleNamespace(
        generate=AsyncMock(return_value="generated"), close=AsyncMock()
    )
    semantic = SimpleNamespace(
        rerank=AsyncMock(return_value=[{"index": i} for i in range(3)]),
        embed=AsyncMock(),
        close=AsyncMock(),
    )
    monkeypatch.setattr(
        "agent.searxng_client.SearXNGClient", lambda *_a, **_kw: searcher
    )
    monkeypatch.setattr(
        "agent.scraper_client.ScraperClient", lambda *_a, **_kw: scraper
    )
    monkeypatch.setattr(
        "agent.research.search.ScraperClient", lambda *_a, **_kw: scraper
    )
    monkeypatch.setattr("agent.llm.LLMClient", lambda *_a, **_kw: llm)
    monkeypatch.setattr("agent.research.search.LLMClient", lambda *_a, **_kw: llm)
    monkeypatch.setattr(
        "agent.semantic_client.SemanticClient", lambda *_a, **_kw: semantic
    )
    state = SimpleNamespace(
        searxng_url="fixture",
        scraper_url="fixture",
        semantic_url="fixture",
        llm_base_url="fixture",
        llm_api_key="",
        llm_model="fixture",
    )
    response = await search(
        SimpleNamespace(app=SimpleNamespace(state=state)),
        SearchRequest(
            query="q",
            limit=3,
            retrieval_mode="hybrid",
            search_type="rich",
            contents=ContentsOptions(summary=True),
        ),
    )
    assert scraper.scrape.await_count == 3
    semantic.embed.assert_not_awaited()
    assert semantic.rerank.call_args.args[1] == ["actual full evidence"] * 3
    assert all(row["summary"] == "generated" for row in response.data["web"])
