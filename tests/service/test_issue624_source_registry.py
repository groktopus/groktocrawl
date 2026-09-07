"""Regression coverage for request-scoped cross-pass source reuse (#624)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest


def _scraper_for(markdown_by_url: dict[str, str | None]) -> MagicMock:
    scraper = MagicMock()
    scraper.base_url = "http://scraper"
    scraper.calls: list[tuple[str, dict | None]] = []

    async def scrape_with_fallback(
        url: str, scrape_options: dict | None = None
    ) -> dict:
        scraper.calls.append((url, scrape_options))
        markdown = markdown_by_url.get(url, f"markdown for {url}")
        if markdown is None:
            return {"success": False, "error": "fixture failure"}
        return {
            "success": True,
            "data": {"markdown": markdown, "source": "fixture"},
        }

    scraper.scrape_with_fallback = AsyncMock(side_effect=scrape_with_fallback)
    return scraper


def test_source_identity_is_conservative():
    from agent.research.sources import normalize_source_url

    assert (
        normalize_source_url("HTTPS://EXAMPLE.COM:443/docs/#section")
        == "https://example.com/docs/"
    )
    assert normalize_source_url("https://example.com/docs") != normalize_source_url(
        "https://example.com/docs/"
    )
    assert normalize_source_url(
        "https://example.com/docs?a=1&b=2"
    ) != normalize_source_url("https://example.com/docs?b=2&a=1")
    assert normalize_source_url("https://example.com/Docs") != normalize_source_url(
        "https://example.com/docs"
    )


@pytest.mark.asyncio
async def test_registry_reuses_alias_without_duplicate_context():
    from agent.research.discovery import _scrape_urls
    from agent.research.sources import SourceRegistry

    scraper = _scraper_for({})
    registry = SourceRegistry()
    first = await _scrape_urls(
        ["https://example.com/page/"],
        scraper,
        min_sources=1,
        source_registry=registry,
    )
    second = await _scrape_urls(
        ["HTTPS://EXAMPLE.COM:443/page/#part"],
        scraper,
        min_sources=1,
        source_registry=registry,
    )

    assert len(first) == len(second) == 1
    assert len(scraper.calls) == 1
    assert len(registry.artifacts()) == 1
    assert registry.context().count("markdown for") == 1


@pytest.mark.asyncio
async def test_failed_acquisition_is_retryable_and_not_registered():
    from agent.research.discovery import _scrape_urls
    from agent.research.sources import SourceRegistry

    scraper = _scraper_for({"https://retry.example/page": None})
    registry = SourceRegistry()
    failed = await _scrape_urls(
        ["https://retry.example/page"],
        scraper,
        min_sources=1,
        source_registry=registry,
    )

    # Replace the failing fixture with a coroutine so this is a real retry,
    # rather than a cache hit.
    async def recovered(url: str, scrape_options: dict | None = None) -> dict:
        scraper.calls.append((url, scrape_options))
        return {"success": True, "data": {"markdown": "recovered", "source": "fixture"}}

    scraper.scrape_with_fallback.side_effect = recovered
    retried = await _scrape_urls(
        ["https://retry.example/page"],
        scraper,
        min_sources=1,
        source_registry=registry,
    )

    assert failed == []
    assert len(retried) == 1
    assert len(scraper.calls) == 2
    assert registry.artifacts()[0].markdown == "recovered"


@pytest.mark.asyncio
async def test_changed_scrape_options_force_new_acquisition():
    from agent.research.discovery import _scrape_urls
    from agent.research.sources import SourceRegistry

    scraper = _scraper_for({})
    registry = SourceRegistry()
    url = "https://example.com/page"
    await _scrape_urls(
        [url],
        scraper,
        min_sources=1,
        source_registry=registry,
        scrape_options={"formats": ["markdown"]},
    )
    await _scrape_urls(
        [url],
        scraper,
        min_sources=1,
        source_registry=registry,
        scrape_options={"formats": ["markdown", "images"]},
    )

    assert len(scraper.calls) == 2
    assert len(registry.artifacts()) == 1


@pytest.mark.asyncio
async def test_discovery_accounts_novel_and_reused_sources_with_credit_budget():
    from agent.research.discovery import (
        _run_multi_query_discover_and_scrape,
        _scrape_urls,
    )
    from agent.research.sources import SourceRegistry

    searxng = MagicMock()
    searxng.search = AsyncMock(
        return_value=(
            [
                {"url": "HTTPS://EXAMPLE.COM:443/old#x", "title": "old"},
                {"url": "https://new.example/page", "title": "new"},
            ],
            MagicMock(),
        )
    )
    scraper = _scraper_for({})
    registry = SourceRegistry()
    await _scrape_urls(
        ["https://example.com/old"],
        scraper,
        min_sources=1,
        source_registry=registry,
    )

    result = await _run_multi_query_discover_and_scrape(
        queries=["gap"],
        urls=None,
        searxng=searxng,
        scraper=scraper,
        max_credits=1,
        source_registry=registry,
        pass_number=2,
    )

    assert len(scraper.calls) == 2
    assert result["credits_used"] == 1
    assert result["novel_sources"] == ["https://new.example/page"]
    assert len(result["reused_sources"]) == 1
    assert len(result["source_details"]) == 2
    assert result["context"].count("Source:") == 2


@pytest.mark.asyncio
async def test_scrape_urls_keeps_all_successes_from_completed_batch():
    from agent.research.discovery import _scrape_urls
    from agent.research.sources import SourceRegistry

    started = asyncio.Event()
    release = asyncio.Event()
    active = 0
    scraper = MagicMock()

    async def scrape(url: str, **_kwargs):
        nonlocal active
        active += 1
        if active == 2:
            started.set()
        await release.wait()
        return {
            "success": True,
            "data": {"markdown": f"content for {url}", "source": "fixture"},
        }

    scraper.scrape_with_fallback = AsyncMock(side_effect=scrape)
    registry = SourceRegistry()
    urls = [f"https://batch.example/{i}" for i in range(2)]
    request = asyncio.create_task(
        _scrape_urls(
            urls,
            scraper,
            min_sources=1,
            max_concurrent=2,
            source_registry=registry,
        )
    )
    await started.wait()
    release.set()
    artifacts = await request

    assert {artifact.url for artifact in artifacts} == set(urls)
    assert {artifact.url for artifact in registry.artifacts()} == set(urls)


@pytest.mark.asyncio
async def test_scrape_urls_zero_attempt_budget_does_not_fetch():
    from agent.research.discovery import _scrape_urls

    scraper = MagicMock()
    scraper.scrape_with_fallback = AsyncMock()
    artifacts = await _scrape_urls(
        ["https://zero.example/page"], scraper, min_sources=1, max_attempts=0
    )

    assert artifacts == []
    scraper.scrape_with_fallback.assert_not_awaited()


def _research_clients(search_by_query: dict[str, list[dict]], markdown_by_url=None):
    """Build deterministic clients for mocked two-pass loop tests."""
    searxng = MagicMock()

    async def search(query: str, **kwargs):
        return search_by_query[query], MagicMock()

    searxng.search = AsyncMock(side_effect=search)
    searxng.close = AsyncMock()
    scraper = MagicMock()
    scraper.base_url = "http://scraper"
    scraper.calls: list[str] = []
    markdown_by_url = markdown_by_url or {}

    async def scrape_with_fallback(url: str, **kwargs):
        scraper.calls.append(url)
        return {
            "success": True,
            "data": {
                "markdown": markdown_by_url.get(url, f"markdown for {url}"),
                "source": "fixture",
            },
        }

    scraper.scrape_with_fallback = AsyncMock(side_effect=scrape_with_fallback)
    scraper.close = AsyncMock()
    llm = MagicMock()
    llm.generate = AsyncMock(return_value="answer [1]")
    llm.close = AsyncMock()
    return searxng, scraper, llm


def _patch_research_clients(searxng, scraper, llm, *, gaps=True):
    from unittest.mock import patch

    plan = {
        "focused_queries": ["first-a", "first-b"],
        "research_strategy": "deep",
        "reasoning": "fixture",
    }
    return patch.multiple(
        "agent.research.loop",
        SearXNGClient=MagicMock(return_value=searxng),
        ScraperClient=MagicMock(return_value=scraper),
        LLMClient=MagicMock(return_value=llm),
        _generate_research_plan=AsyncMock(return_value=plan),
        _detect_gaps=AsyncMock(return_value=["gap"] if gaps else []),
    )


@pytest.mark.asyncio
async def test_two_pass_duplicate_only_reuses_and_synthesizes_once():
    from agent.research.loop import run_research

    first = [
        {"url": f"https://source{i}.example/page", "title": f"S{i}"} for i in range(3)
    ]
    duplicate_aliases = [
        {"url": "HTTPS://SOURCE0.EXAMPLE:443/page#section"},
        {"url": "https://SOURCE1.EXAMPLE/page#part"},
        {"url": "https://source2.example/page#part"},
    ]
    clients = _research_clients(
        {"first-a": first, "first-b": [], "gap": duplicate_aliases}
    )
    _searxng, scraper, llm = clients
    with _patch_research_clients(*clients):
        result = await run_research(
            prompt="question", llm_model="fixture", search_type="deep"
        )

    assert len(scraper.calls) == 3
    assert llm.generate.await_count == 1
    assert result["sources"] == [item["url"] for item in first]
    assert len(result["source_details"]) == 3


@pytest.mark.asyncio
async def test_two_pass_partial_overlap_fetches_only_novel_source():
    from agent.research.loop import run_research

    first = [
        {"url": f"https://source{i}.example/page", "title": f"S{i}"} for i in range(3)
    ]
    second = [
        {"url": "HTTPS://SOURCE0.EXAMPLE:443/page#section"},
        {"url": "https://SOURCE1.EXAMPLE/page#part"},
        {"url": "https://new.example/page"},
    ]
    clients = _research_clients({"first-a": first, "first-b": [], "gap": second})
    _searxng, scraper, llm = clients
    with _patch_research_clients(*clients):
        result = await run_research(
            prompt="question", llm_model="fixture", search_type="deep"
        )

    assert len(scraper.calls) == 4
    assert llm.generate.await_count == 1
    assert len(result["sources"]) == 4
    assert result["sources"].count("https://new.example/page") == 1


@pytest.mark.asyncio
async def test_two_pass_failed_first_acquisition_retries_without_duplicate_source():
    from agent.research.loop import run_research

    first = [{"url": f"https://source{i}.example/page"} for i in range(3)]
    second = [
        {"url": "HTTPS://SOURCE0.EXAMPLE:443/page#retry"},
        {"url": "https://source1.example/page"},
        {"url": "https://source2.example/page"},
    ]
    clients = _research_clients({"first-a": first, "first-b": [], "gap": second})
    _searxng, scraper, llm = clients
    attempts: dict[str, int] = {}

    async def scrape_with_retry(url: str, **kwargs):
        key = "source0" if "source0" in url.lower() else url
        attempts[key] = attempts.get(key, 0) + 1
        scraper.calls.append(url)
        if key == "source0" and attempts[key] == 1:
            return {"success": False, "error": "transient"}
        return {
            "success": True,
            "data": {"markdown": f"markdown for {url}", "source": "fixture"},
        }

    scraper.scrape_with_fallback = AsyncMock(side_effect=scrape_with_retry)
    with _patch_research_clients(*clients):
        result = await run_research(
            prompt="question", llm_model="fixture", search_type="deep"
        )

    assert attempts["source0"] == 2
    assert len(scraper.calls) == 4
    assert llm.generate.await_count == 1
    assert len(result["sources"]) == 3


@pytest.mark.asyncio
async def test_two_pass_schema_and_streaming_preserve_single_synthesis_on_duplicate_pass():
    from agent.research.loop import run_research_stream

    first = [{"url": f"https://source{i}.example/page"} for i in range(3)]
    second = [{"url": f"HTTPS://SOURCE{i}.EXAMPLE:443/page#part"} for i in range(3)]
    clients = _research_clients({"first-a": first, "first-b": [], "gap": second})
    _searxng, scraper, llm = clients

    async def generate_stream(**kwargs):
        yield {"type": "token", "content": "streamed"}
        yield {"type": "done", "full_content": "streamed"}

    llm.generate_stream = generate_stream
    with _patch_research_clients(*clients):
        events = [
            event
            async for event in run_research_stream(
                prompt="question", llm_model="fixture", search_type="deep"
            )
        ]

    assert len(scraper.calls) == 3
    assert [event["type"] for event in events].count("token") == 1
    assert events[-1]["type"] == "done"

    # Structured output uses the non-streaming synthesis branch even through
    # the stream adapter, and duplicate pass reuse still avoids a second call.
    clients = _research_clients({"first-a": first, "first-b": [], "gap": second})
    _searxng, scraper, llm = clients
    llm.generate.return_value = "{}"
    with _patch_research_clients(*clients):
        events = [
            event
            async for event in run_research_stream(
                prompt="question",
                llm_model="fixture",
                search_type="deep",
                schema={"type": "object"},
            )
        ]

    assert len(scraper.calls) == 3
    assert llm.generate.await_count == 1
    assert events[-1]["type"] == "done"


@pytest.mark.asyncio
async def test_reused_evidence_does_not_crowd_out_novel_gap_sources():
    from agent.research.discovery import _scrape_urls
    from agent.research.sources import SourceArtifact, SourceRegistry

    registry = SourceRegistry()
    old = [f"https://example.com/old{i}" for i in range(3)]
    for url in old:
        registry.register(SourceArtifact(url=url, markdown="old evidence"))
    scraper = _scraper_for({})
    fresh = "https://example.com/gap"
    artifacts = await _scrape_urls([*old, fresh], scraper, source_registry=registry)
    assert [url for url, _ in scraper.calls] == [fresh]
    assert len(artifacts) == 4
    assert len(registry.artifacts()) == 4


def test_source_identity_preserves_path_parameters():
    from agent.research.sources import normalize_source_url

    assert normalize_source_url("https://example.com/page;a=1") != normalize_source_url(
        "https://example.com/page;a=2"
    )


@pytest.mark.asyncio
async def test_scrape_urls_cancellation_drains_inflight_workers():
    from agent.research.discovery import _scrape_urls
    from agent.research.sources import SourceRegistry

    started = 0
    both_started = asyncio.Event()
    cancelled: list[str] = []

    async def scrape_with_fallback(url: str, **kwargs):
        nonlocal started
        started += 1
        if started == 2:
            both_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(url)

    scraper = MagicMock()
    scraper.scrape_with_fallback = scrape_with_fallback
    task = asyncio.create_task(
        _scrape_urls(
            ["https://example.com/a", "https://example.com/b"],
            scraper,
            max_concurrent=2,
            source_registry=SourceRegistry(),
        )
    )

    await asyncio.wait_for(both_started.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert sorted(cancelled) == [
        "https://example.com/a",
        "https://example.com/b",
    ]
