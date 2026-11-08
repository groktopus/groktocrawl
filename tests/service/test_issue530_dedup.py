"""Regression tests for issue #530 — eliminate duplicate retrieval, cache,
and browser work in the agent answer/research pipelines."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from agent.models import AgentRequest

from common.metrics import MetricsCollector

# ── Research-memory lookup ownership ──────────────────────────────


def _make_agent_request_mock() -> MagicMock:
    request = MagicMock()
    rate_limiter = MagicMock()
    rate_limiter.limit = 100
    rate_limiter.window = 60
    rate_limiter.check = AsyncMock(return_value=(True, 100))

    state = MagicMock()
    state.rate_limiter = rate_limiter
    state.max_searches_per_request = 5
    state.research_memory = MagicMock()
    state.research_memory.query = AsyncMock(return_value={"hit": False})
    state.job_store = MagicMock()
    state.job_store.create_job.return_value = "job-1"
    state.task_tracker = MagicMock()

    request.app.state = state
    request.headers = MagicMock()
    request.headers.get.return_value = None
    request.client = MagicMock()
    request.client.host = "127.0.0.1"
    return request


@pytest.mark.asyncio
async def test_non_streaming_agent_route_skips_memory_lookup():
    """The non-streaming route defers the single lookup to the worker."""
    from agent.routes.agent import create_agent

    request = _make_agent_request_mock()
    body = AgentRequest(prompt="hello", stream=False)
    response = MagicMock()
    response.headers = {}

    with (
        patch(
            "agent.routes.agent._lookup_agent_cache",
            new=AsyncMock(return_value=None),
        ) as lookup,
        patch(
            "agent.routes.agent._handle_agent_streaming",
            new=AsyncMock(return_value=None),
        ),
        patch("agent.worker._process_agent_async", new=MagicMock()),
    ):
        await create_agent(request, body, response)

    lookup.assert_not_awaited()


@pytest.mark.asyncio
async def test_streaming_agent_route_lookup_once():
    """The streaming route owns the single research-memory lookup."""
    from agent.routes.agent import create_agent

    request = _make_agent_request_mock()
    body = AgentRequest(prompt="hello", stream=True)
    response = MagicMock()
    response.headers = {}

    with (
        patch(
            "agent.routes.agent._lookup_agent_cache",
            new=AsyncMock(return_value=None),
        ) as lookup,
        patch(
            "agent.routes.agent._handle_agent_streaming",
            new=AsyncMock(return_value=None),
        ),
        patch("agent.worker._process_agent_async", new=MagicMock()),
    ):
        await create_agent(request, body, response)

    lookup.assert_awaited_once()


# ── Rerank concurrency + content reuse ────────────────────────────


class TrackingScraper:
    def __init__(self) -> None:
        self.base_url = "http://scraper"
        self.scrape_counts: dict[str, int] = {}
        self.concurrent = 0
        self.max_concurrent = 0
        self._scrape_calls: list[tuple[str, str]] = []

    async def scrape(self, url: str, **kwargs) -> dict:
        self.scrape_counts[url] = self.scrape_counts.get(url, 0) + 1
        self._scrape_calls.append((url, "scrape"))
        self.concurrent += 1
        self.max_concurrent = max(self.max_concurrent, self.concurrent)
        try:
            await asyncio.sleep(0.02)
        finally:
            self.concurrent -= 1
        return {
            "success": True,
            "data": {"markdown": f"content {url}", "source": "test"},
        }

    async def scrape_with_fallback(self, url: str, **kwargs) -> dict:
        self.scrape_counts[url] = self.scrape_counts.get(url, 0) + 1
        self._scrape_calls.append((url, "fallback"))
        return {
            "success": True,
            "data": {"markdown": f"content {url}", "source": "test"},
        }

    async def close(self) -> None:
        pass


def _make_semantic(embed_sims: list[float]) -> MagicMock:
    semantic = MagicMock()
    query_embedding = [1.0, 0.0]
    semantic.embed = AsyncMock(
        return_value=[query_embedding, *[[sim, 0.0] for sim in embed_sims]]
    )
    semantic.close = AsyncMock()
    return semantic


@pytest.mark.asyncio
async def test_rerank_scrapes_concurrently_with_bound():
    """Rerank candidate scraping is concurrent and bounded by max_concurrent."""
    from agent.research.rerank import _rerank_answer_sources

    scraper = TrackingScraper()
    semantic = _make_semantic([0.9, 0.1, 0.5, 0.2])
    search_results = [
        {"url": f"https://a{i}.com", "title": f"A{i}", "description": f"d{i}"}
        for i in range(4)
    ]

    with (
        patch("agent.research.rerank.SemanticClient", return_value=semantic),
        patch("agent.research.rerank.ScraperClient", return_value=scraper),
    ):
        ranked, artifacts = await _rerank_answer_sources(
            search_results=search_results,
            query="q",
            retrieval_mode="semantic",
            semantic_url="http://semantic",
            scraper_url="http://scraper",
            limit=4,
            max_concurrent=2,
        )

    assert scraper.max_concurrent == 2
    assert len(ranked) == 4
    assert len(artifacts) == 4
    assert sorted(a.url for a in artifacts) == sorted(r["url"] for r in search_results)


@pytest.mark.asyncio
async def test_hybrid_rerank_passes_fetched_content_not_descriptions():
    """Hybrid mode ranks fetched Markdown, not search-result descriptions."""
    from agent.research.rerank import _rerank_answer_sources

    scraper = TrackingScraper()
    semantic = MagicMock()
    semantic.rerank = AsyncMock(
        return_value=[
            {"index": 0, "relevance_score": 0.9},
            {"index": 1, "relevance_score": 0.5},
        ]
    )
    semantic.close = AsyncMock()

    search_results = [
        {"url": "https://a.com", "title": "A", "description": "DESC A"},
        {"url": "https://b.com", "title": "B", "description": "DESC B"},
    ]

    with (
        patch("agent.research.rerank.SemanticClient", return_value=semantic),
        patch("agent.research.rerank.ScraperClient", return_value=scraper),
    ):
        ranked, artifacts = await _rerank_answer_sources(
            search_results=search_results,
            query="q",
            retrieval_mode="hybrid",
            semantic_url="http://semantic",
            scraper_url="http://scraper",
            limit=2,
        )

    documents = semantic.rerank.call_args[0][1]
    assert documents == ["content https://a.com", "content https://b.com"]
    assert len(ranked) == 2
    assert len(artifacts) == 2


@pytest.mark.asyncio
async def test_rerank_keyword_mode_passthrough():
    """Keyword mode returns the original results with no artifacts."""
    from agent.research.rerank import _rerank_answer_sources

    search_results = [
        {"url": "https://a.com", "title": "A", "description": "d"},
    ]
    ranked, artifacts = await _rerank_answer_sources(
        search_results=search_results,
        query="q",
        retrieval_mode="keyword",
        semantic_url="http://semantic",
        scraper_url="http://scraper",
        limit=1,
    )
    assert ranked == search_results
    assert artifacts == []


@pytest.mark.asyncio
async def test_rerank_no_results_returns_empty():
    """Empty search results short-circuit without creating clients."""
    from agent.research.rerank import _rerank_answer_sources

    ranked, artifacts = await _rerank_answer_sources(
        search_results=[],
        query="q",
        retrieval_mode="semantic",
        semantic_url="http://semantic",
        scraper_url="http://scraper",
        limit=1,
    )
    assert ranked == []
    assert artifacts == []


@pytest.mark.asyncio
async def test_rerank_vector_mode_returns_no_artifacts():
    """Vector mode uses search_vector and returns no scraped artifacts."""
    from agent.research.rerank import _rerank_answer_sources

    semantic = MagicMock()
    semantic.search_vector = AsyncMock(
        return_value=[{"url": "https://v.com", "title": "V"}]
    )
    semantic.close = AsyncMock()
    scraper = MagicMock()
    scraper.close = AsyncMock()

    with (
        patch("agent.research.rerank.SemanticClient", return_value=semantic),
        patch("agent.research.rerank.ScraperClient", return_value=scraper),
    ):
        ranked, artifacts = await _rerank_answer_sources(
            search_results=[{"url": "https://a.com", "title": "A"}],
            query="q",
            retrieval_mode="vector",
            semantic_url="http://semantic",
            scraper_url="http://scraper",
            limit=1,
        )

    assert ranked[0]["url"] == "https://v.com"
    assert artifacts == []


@pytest.mark.asyncio
async def test_rerank_hybrid_vector_mode_returns_provenance_artifacts():
    """Hybrid-vector mode delegates to the shared planner and acquires content."""
    from agent.research.rerank import _rerank_answer_sources

    semantic = MagicMock()
    semantic.search_vector = AsyncMock(
        return_value=[
            {"url": "https://a.com", "title": "A"},
            {"url": "https://c.com", "title": "C"},
        ]
    )
    semantic.close = AsyncMock()
    scraper = TrackingScraper()

    with (
        patch("agent.research.rerank.SemanticClient", return_value=semantic),
        patch("agent.research.rerank.ScraperClient", return_value=scraper),
    ):
        ranked, artifacts = await _rerank_answer_sources(
            search_results=[
                {"url": "https://a.com", "title": "A", "description": "d"},
                {"url": "https://b.com", "title": "B", "description": "d"},
            ],
            query="q",
            retrieval_mode="hybrid_vector",
            semantic_url="http://semantic",
            scraper_url="http://scraper",
            limit=5,
        )

    urls = [r["url"] for r in ranked]
    assert urls == ["https://a.com", "https://b.com", "https://c.com"]
    # Artifacts now carry Markdown + provenance for the answer pipeline.
    assert {a.url for a in artifacts} == set(urls)
    assert all(a.markdown for a in artifacts)
    assert all(a.retrieval in ("web", "vector", "both") for a in artifacts)
    assert all(a.cache_state in ("live", "from_cache") for a in artifacts)


@pytest.mark.asyncio
async def test_answer_stream_reuses_rerank_content():
    """run_answer_stream reuses rerank content without re-scraping."""
    from agent.research import run_answer_stream

    searxng = MagicMock()
    searxng.search = AsyncMock(
        return_value=(
            [
                {"url": "https://a.com", "title": "A", "description": "d"},
                {"url": "https://b.com", "title": "B", "description": "d"},
            ],
            MagicMock(),
        )
    )
    searxng.close = AsyncMock()

    scraper = TrackingScraper()

    async def _stream(*args, **kwargs):
        yield {"type": "token", "content": "Based on [1] the answer."}
        yield {"type": "done", "full_content": "Based on [1] the answer."}

    llm = MagicMock()
    llm.generate_stream = _stream
    llm.close = AsyncMock()

    semantic = _make_semantic([0.9, 0.1])

    with (
        patch("agent.research.loop.SearXNGClient", return_value=searxng),
        patch("agent.research.loop.ScraperClient", return_value=scraper),
        patch("agent.research.loop.LLMClient", return_value=llm),
        patch("agent.research.rerank.ScraperClient", return_value=scraper),
        patch("agent.research.rerank.SemanticClient", return_value=semantic),
    ):
        events = [
            event
            async for event in run_answer_stream(
                query="q", num_sources=2, retrieval_mode="semantic", llm_model="m"
            )
        ]

    done = next(event for event in events if event["type"] == "done")
    assert done["answer"] == "Based on [1] the answer."
    assert sum(scraper.scrape_counts.values()) == 2
    assert scraper.scrape_counts["https://a.com"] == 1
    assert scraper.scrape_counts["https://b.com"] == 1


@pytest.mark.asyncio
async def test_answer_synthesis_reuses_rerank_content():
    """A candidate URL is scraped exactly once across rerank and synthesis."""
    from agent.research import run_answer

    searxng = MagicMock()
    searxng.search = AsyncMock(
        return_value=(
            [
                {"url": "https://a.com", "title": "A", "description": "d"},
                {"url": "https://b.com", "title": "B", "description": "d"},
            ],
            MagicMock(),
        )
    )
    searxng.close = AsyncMock()

    scraper = TrackingScraper()

    llm = MagicMock()
    llm.generate = AsyncMock(return_value="Based on [1] the answer.")
    llm.close = AsyncMock()

    semantic = _make_semantic([0.9, 0.1])

    with (
        patch("agent.research.loop.SearXNGClient", return_value=searxng),
        patch("agent.research.loop.ScraperClient", return_value=scraper),
        patch("agent.research.loop.LLMClient", return_value=llm),
        patch("agent.research.rerank.ScraperClient", return_value=scraper),
        patch("agent.research.rerank.SemanticClient", return_value=semantic),
    ):
        result = await run_answer(
            query="q", num_sources=2, retrieval_mode="semantic", llm_model="m"
        )

    assert result["answer"] == "Based on [1] the answer."
    # Two candidates scraped once each during rerank, never re-scraped.
    assert sum(scraper.scrape_counts.values()) == 2
    assert scraper.scrape_counts["https://a.com"] == 1
    assert scraper.scrape_counts["https://b.com"] == 1


# ── scrape_with_fallback lightweight contract + retry metric ──────


@pytest.mark.asyncio
async def test_generic_stage_uses_lightweight_only():
    """The generic fallback stage never forces the browser tier."""
    from agent.scraper_client import ScraperClient

    client = ScraperClient("http://scraper")
    calls: list[tuple[bool, bool]] = []

    async def fake_scrape(
        url: str,
        force_browser: bool = False,
        lightweight_only: bool = False,
        scrape_options: dict | None = None,
        **kwargs,
    ) -> dict:
        calls.append((force_browser, lightweight_only))
        return {"success": True, "data": {"markdown": "ok", "source": "test"}}

    with patch.object(client, "scrape", new=fake_scrape):
        result = await client.scrape_with_fallback("https://x.com")

    assert result["success"] is True
    assert calls == [(False, True)]


@pytest.mark.asyncio
async def test_generic_timeout_awaits_cancelled_task_before_browser():
    """A generic timeout yields one retry after the cancelled task is awaited."""
    from agent.scraper_client import ScraperClient

    client = ScraperClient("http://scraper")
    events: list[str] = []
    call_kinds: list[str] = []

    async def fake_scrape(
        url: str,
        force_browser: bool = False,
        lightweight_only: bool = False,
        scrape_options: dict | None = None,
        **kwargs,
    ) -> dict:
        call_kinds.append("browser" if force_browser else "generic")
        if force_browser:
            events.append("browser_start")
            return {
                "success": True,
                "data": {"markdown": "browser ok", "source": "browser"},
            }
        events.append("generic_start")
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            events.append("generic_cancelled")
            raise
        return {"success": True, "data": {"markdown": "generic ok"}}

    with patch.object(client, "scrape", new=fake_scrape):
        result = await client.scrape_with_fallback(
            "https://x.com", generic_timeout=0.05, browser_timeout=5
        )

    assert result["data"]["markdown"] == "browser ok"
    assert call_kinds == ["generic", "browser"]
    assert events == ["generic_start", "generic_cancelled", "browser_start"]


@pytest.mark.asyncio
async def test_scrape_retry_metric_increments_on_browser_fallback():
    """The explicit generic→browser retry is observable via scrape_retries_total."""
    from agent.scraper_client import ScraperClient

    client = ScraperClient("http://scraper")
    fresh_metrics = MetricsCollector()

    async def generic_timeout(
        url: str,
        force_browser: bool = False,
        lightweight_only: bool = False,
        scrape_options: dict | None = None,
        **kwargs,
    ) -> dict:
        if force_browser:
            return {"success": True, "data": {"markdown": "ok", "source": "browser"}}
        await asyncio.sleep(10)
        return {"success": False}

    with (
        patch.object(client, "scrape", new=generic_timeout),
        patch("agent.scraper_client.METRICS", new=fresh_metrics),
    ):
        await client.scrape_with_fallback("https://x.com", generic_timeout=0.05)

    text = fresh_metrics.generate_openmetrics()
    assert 'scrape_retries_total{stage="generic_to_browser"} 1.0' in text


@pytest.mark.asyncio
async def test_degraded_lightweight_result_falls_through_to_browser():
    """A warning-carrying (degraded) lightweight result triggers the browser retry.

    The generic stage runs with ``lightweight_only=True``; when Tier 1/2 return
    non-empty but below-quality content, smart_scrape attaches a ``warning`` key.
    Such degraded content must not satisfy the success check — it falls through
    to the forced-browser stage and the retry is observable via the metric.
    """
    from agent.scraper_client import ScraperClient

    client = ScraperClient("http://scraper")
    fresh_metrics = MetricsCollector()
    call_kinds: list[str] = []

    async def fake_scrape(
        url: str,
        force_browser: bool = False,
        lightweight_only: bool = False,
        scrape_options: dict | None = None,
        **kwargs,
    ) -> dict:
        call_kinds.append("browser" if force_browser else "generic")
        if force_browser:
            return {
                "success": True,
                "data": {"markdown": "rendered ok", "source": "browser"},
            }
        return {
            "success": True,
            "warning": "Lightweight-only content — quality (0.10) below threshold (0.30)",
            "data": {
                "markdown": "thin nav-only shell",
                "source": "tier2-content-negotiation",
            },
        }

    with (
        patch.object(client, "scrape", new=fake_scrape),
        patch("agent.scraper_client.METRICS", new=fresh_metrics),
    ):
        result = await client.scrape_with_fallback("https://x.com")

    assert result["data"]["markdown"] == "rendered ok"
    assert call_kinds == ["generic", "browser"]
    text = fresh_metrics.generate_openmetrics()
    assert 'scrape_retries_total{stage="generic_to_browser"} 1.0' in text


@pytest.mark.asyncio
async def test_non_degraded_lightweight_result_returns_immediately():
    """A clean (non-degraded) lightweight result skips the browser retry."""
    from agent.scraper_client import ScraperClient

    client = ScraperClient("http://scraper")
    fresh_metrics = MetricsCollector()
    call_kinds: list[str] = []

    async def fake_scrape(
        url: str,
        force_browser: bool = False,
        lightweight_only: bool = False,
        scrape_options: dict | None = None,
        **kwargs,
    ) -> dict:
        call_kinds.append("browser" if force_browser else "generic")
        return {
            "success": True,
            "data": {"markdown": "full llms.txt", "source": "tier1-llms-txt"},
        }

    with (
        patch.object(client, "scrape", new=fake_scrape),
        patch("agent.scraper_client.METRICS", new=fresh_metrics),
    ):
        result = await client.scrape_with_fallback("https://x.com")

    assert result["data"]["markdown"] == "full llms.txt"
    assert call_kinds == ["generic"]
    text = fresh_metrics.generate_openmetrics()
    assert "scrape_retries_total" not in text


# ── Dedup metric ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dedup_metric_increments_for_reused_content():
    """Reusing rerank content increments fetches_deduped_total."""
    from agent.research.discovery import _scrape_answer_sources
    from agent.research.sources import SourceArtifact

    scraper = MagicMock()
    fresh_metrics = MetricsCollector()
    artifacts = [
        SourceArtifact(url="https://a.com", markdown="content a"),
        SourceArtifact(url="https://b.com", markdown="content b"),
    ]

    with patch("agent.research.discovery.METRICS", new=fresh_metrics):
        result = await _scrape_answer_sources(
            ["https://a.com", "https://b.com"], artifacts, scraper, num_sources=2
        )

    assert len(result) == 2
    text = fresh_metrics.generate_openmetrics()
    assert 'fetches_deduped_total{reason="rerank_reuse"} 2.0' in text


@pytest.mark.asyncio
async def test_keyword_answer_dedupes_duplicate_urls_respecting_num_sources():
    """Duplicate URLs across preferred+deprioritized never exceed num_sources.

    Keyword mode returns up to 2x num_sources search entries, many repeating
    the same URL. The answer pipeline must deduplicate so the returned
    artifacts/source_map stay within quota and contain no duplicate URLs,
    while preserving first-occurrence ordering and the video fallback.
    """
    from agent.research.discovery import _run_answer_discover_and_scrape

    searxng = MagicMock()
    searxng.search = AsyncMock(
        return_value=(
            [
                {"url": "https://a.com", "title": "A1", "description": "d1"},
                {"url": "https://b.com", "title": "B", "description": "d2"},
                {
                    "url": "https://youtube.com/watch?v=1",
                    "title": "V1",
                    "description": "d3",
                },
                {"url": "https://a.com", "title": "A2", "description": "d4"},
                {
                    "url": "https://youtube.com/watch?v=1",
                    "title": "V1b",
                    "description": "d5",
                },
                {
                    "url": "https://youtube.com/watch?v=2",
                    "title": "V2",
                    "description": "d6",
                },
            ],
            MagicMock(),
        )
    )
    searxng.close = AsyncMock()

    class _Scraper:
        base_url = "http://scraper"

        async def scrape_with_fallback(self, url: str, **kwargs) -> dict:
            if url == "https://youtube.com/watch?v=2":
                return {"success": False, "error": "video scrape failed"}
            return {
                "success": True,
                "data": {"markdown": f"content {url}", "source": "test"},
            }

    result = await _run_answer_discover_and_scrape(
        query="q",
        num_sources=3,
        retrieval_mode="keyword",
        searxng=searxng,
        scraper=_Scraper(),  # type: ignore[arg-type]
        semantic_url="http://semantic",
        llm_base_url="http://llm",
        llm_api_key="k",
        llm_model="m",
        requested_model=None,
    )

    source_map = result["source_map"]
    urls = [entry["url"] for entry in source_map]
    assert len(urls) <= 3
    assert len(set(urls)) == len(urls)
    # Stable ordering: preferred sources in first-occurrence rank order,
    # then the video-platform fallback (duplicate URLs collapse to one entry).
    assert urls == ["https://a.com", "https://b.com", "https://youtube.com/watch?v=1"]


# ── Cancelled speculative tasks are awaited ───────────────────────


@pytest.mark.asyncio
async def test_scrape_urls_awaits_cancelled_tasks():
    """Early termination awaits cancelled speculative scrape tasks."""
    from agent.research import _scrape_urls

    events: list[str] = []

    class Scraper:
        async def scrape_with_fallback(self, url: str, **kwargs) -> dict:
            events.append(f"start:{url}")
            if url.endswith("/fast"):
                return {"success": True, "data": {"markdown": "x", "source": "t"}}
            try:
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                events.append(f"cancelled:{url}")
                raise
            return {"success": True, "data": {"markdown": "x", "source": "t"}}

    scraper = Scraper()
    artifacts = await _scrape_urls(
        ["https://x.com/fast", "https://x.com/slow1", "https://x.com/slow2"],
        scraper,  # type: ignore[arg-type]
        min_sources=1,
        max_concurrent=3,
    )

    assert len(artifacts) == 1
    assert "cancelled:https://x.com/slow1" in events
    assert "cancelled:https://x.com/slow2" in events


@pytest.mark.asyncio
async def test_scrape_urls_batch_awaits_cancelled_tasks():
    """Batch scrape awaits cancelled speculative tasks before returning."""
    from agent.scraper_client import ScraperClient

    client = ScraperClient("http://scraper")
    events: list[str] = []

    async def fake_scrape(url: str) -> dict:
        events.append(f"start:{url}")
        if url.endswith("/fast"):
            return {"success": True, "data": {"markdown": "x"}}
        try:
            await asyncio.sleep(1)
        except asyncio.CancelledError:
            events.append(f"cancelled:{url}")
            raise
        return {"success": True, "data": {"markdown": "x"}}

    with patch.object(client, "scrape", new=fake_scrape):
        results = await client.scrape_urls_batch(
            ["https://x.com/fast", "https://x.com/slow1", "https://x.com/slow2"],
            max_concurrent=3,
            url_timeout=5,
            min_sources=1,
        )

    assert len(results) == 1
    assert "cancelled:https://x.com/slow1" in events
    assert "cancelled:https://x.com/slow2" in events
