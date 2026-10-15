"""Offline delivery-contract regressions for search and deep-search modes."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_deep_search_forwards_filters_and_bounds_unique_followups(monkeypatch):
    import agent.research.search as module

    class Searcher:
        def __init__(self, *_args, **_kwargs):
            self.calls = []

        async def search(self, query, *, limit, categories=None, sources=None):
            self.calls.append((query, limit, categories, sources))
            return ([{"url": f"https://example.test/{len(self.calls)}"}], None)

        async def close(self):
            pass

    searcher = Searcher()
    llm = SimpleNamespace(
        generate=AsyncMock(
            return_value=json.dumps(
                [
                    " MAIN QUERY ",
                    "  facets  ",
                    "FACETS",
                    "fresh angle",
                    "third",
                    "fourth",
                    "fifth",
                    7,
                    "",
                ]
            )
        ),
        close=AsyncMock(),
    )
    monkeypatch.setattr(module, "SearXNGClient", lambda *_a, **_kw: searcher)
    monkeypatch.setattr(module, "LLMClient", lambda *_a, **_kw: llm)

    result = await module.run_deep_search(
        "main query",
        5,
        llm_model="fixture",
        categories=["science"],
        sources=["web"],
    )

    expected_queries = ["main query", "facets", "fresh angle", "third", "fourth"]
    assert result["query_variations"] == expected_queries
    assert [call[0] for call in searcher.calls] == expected_queries
    assert all(call[2:] == (["science"], ["web"]) for call in searcher.calls)
    assert len(result["results"]) == 5


@pytest.mark.asyncio
async def test_nonstream_deep_route_forwards_requested_categories_and_sources(
    monkeypatch,
):
    import agent.routes.search as route
    from agent.models import SearchRequest

    searcher = SimpleNamespace(search=AsyncMock(), close=AsyncMock())
    captured = {}

    async def deep_search(**kwargs):
        captured.update(kwargs)
        return {"results": [], "query_variations": [kwargs["query"]]}

    monkeypatch.setattr(
        "agent.searxng_client.SearXNGClient", lambda *_a, **_kw: searcher
    )
    monkeypatch.setattr("agent.research.run_deep_search", deep_search)
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                searxng_url="fixture",
                scraper_url="fixture",
                semantic_url="fixture",
                llm_base_url="fixture",
                llm_api_key="",
                llm_model="fixture",
            )
        )
    )

    response = await route.search(
        request,
        SearchRequest(
            query="main query",
            search_type="deep",
            categories=["science"],
            sources=["web"],
        ),
    )

    assert response.query_variations == ["main query"]
    assert captured["categories"] == ["science"]
    assert captured["sources"] == ["web"]
    searcher.search.assert_not_awaited()
    searcher.close.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("retrieval_mode", ["semantic", "hybrid"])
async def test_reranked_stream_emits_final_order(monkeypatch, retrieval_mode):
    import agent.research.search as module

    rows = [
        {"url": "https://example.test/a", "title": "A", "description": "a"},
        {"url": "https://example.test/b", "title": "B", "description": "b"},
    ]
    searcher = SimpleNamespace(
        search=AsyncMock(return_value=(rows, None)), close=AsyncMock()
    )
    semantic = SimpleNamespace(
        embed=AsyncMock(return_value=[[1.0], [0.1], [0.9]]),
        rerank=AsyncMock(return_value=[{"index": 1}, {"index": 0}]),
        close=AsyncMock(),
    )
    scraper = SimpleNamespace(close=AsyncMock())
    llm = SimpleNamespace(close=AsyncMock())

    class Artifact:
        def __init__(self, url):
            self.url = url
            self.markdown = "body"

    async def acquired(*_args, **_kwargs):
        return SimpleNamespace(
            artifacts=[Artifact(row["url"]) for row in rows],
            refusals={},
            failures={},
            by_url=lambda: {row["url"]: Artifact(row["url"]) for row in rows},
        )

    monkeypatch.setattr(module, "SearXNGClient", lambda *_a, **_kw: searcher)
    monkeypatch.setattr(module, "ScraperClient", lambda *_a, **_kw: scraper)
    monkeypatch.setattr(module, "LLMClient", lambda *_a, **_kw: llm)
    monkeypatch.setattr(module, "acquire_source_artifacts", acquired)
    monkeypatch.setattr(
        "agent.semantic_client.SemanticClient", lambda *_a, **_kw: semantic
    )

    events = [
        event
        async for event in module.run_search_stream(
            "query", retrieval_mode=retrieval_mode, llm_model="fixture"
        )
    ]

    assert [e["result"]["url"] for e in events if e["type"] == "search_result"] == [
        rows[1]["url"],
        rows[0]["url"],
    ]
    assert events[-1]["total_results"] == 2
    if retrieval_mode == "hybrid":
        semantic.rerank.assert_awaited_once()
    else:
        semantic.embed.assert_awaited_once()
    assert semantic.close.await_count == 1


@pytest.mark.asyncio
async def test_deep_stream_runs_gap_analysis_and_exposes_final_result_events(
    monkeypatch,
):
    import agent.research.search as module

    queries = []
    searcher = SimpleNamespace(close=AsyncMock())

    async def search(query, *, limit, categories=None, sources=None):
        queries.append((query, categories, sources))
        return ([{"url": f"https://example.test/{len(queries)}", "title": query}], None)

    searcher.search = AsyncMock(side_effect=search)
    llm = SimpleNamespace(
        generate=AsyncMock(return_value='["follow-up"]'), close=AsyncMock()
    )
    scraper = SimpleNamespace(close=AsyncMock())
    monkeypatch.setattr(module, "SearXNGClient", lambda *_a, **_kw: searcher)
    monkeypatch.setattr(module, "ScraperClient", lambda *_a, **_kw: scraper)
    monkeypatch.setattr(module, "LLMClient", lambda *_a, **_kw: llm)

    events = [
        event
        async for event in module.run_search_stream(
            "main query",
            search_type="deep",
            categories=["science"],
            sources=["web"],
            llm_model="fixture",
        )
    ]

    assert [query for query, _, _ in queries] == ["main query", "follow-up"]
    assert all(cats == ["science"] and srcs == ["web"] for _, cats, srcs in queries)
    assert [e["type"] for e in events] == ["search_result", "search_result", "done"]
    done = events[-1]
    assert done["query_variations"] == ["main query", "follow-up"]
    assert done["total_results"] == len(
        [event for event in events if event["type"] == "search_result"]
    )
