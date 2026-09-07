"""Deterministic producer/consumer discovery pipeline coverage (#622)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest


def _result(url: str) -> dict:
    return {"url": url, "title": url.rsplit("/", 1)[-1], "description": "fixture"}


def _scraper(started: asyncio.Event, urls: list[str]) -> MagicMock:
    scraper = MagicMock()

    async def scrape(url: str, **_kwargs):
        started.set()
        urls.append(url)
        return {
            "success": True,
            "data": {"markdown": f"content for {url}", "source": "fixture"},
        }

    scraper.scrape_with_fallback = AsyncMock(side_effect=scrape)
    return scraper


@pytest.mark.asyncio
async def test_fast_query_starts_scrape_before_slow_query_finishes(monkeypatch):
    from agent.research import discovery

    slow_finished = asyncio.Event()
    scrape_started = asyncio.Event()
    scraped: list[str] = []
    callback_urls: list[str] = []

    async def search(query: str, **_kwargs):
        if query == "slow":
            await slow_finished.wait()
        return ([_result(f"https://{query}.example/page")], "healthy")

    searxng = MagicMock(search=AsyncMock(side_effect=search))
    scraper = _scraper(scrape_started, scraped)
    original_rank = discovery._filter_and_rank_urls
    monkeypatch.setattr(discovery, "_filter_and_rank_urls", lambda urls, **_: urls)

    async def on_artifact(artifact):
        callback_urls.append(artifact.url)

    task = asyncio.create_task(
        discovery._run_multi_query_discover_and_scrape(
            queries=["fast", "slow"],
            urls=None,
            searxng=searxng,
            scraper=scraper,
            source_registry=discovery.SourceRegistry(),
            on_artifact=on_artifact,
        )
    )
    await asyncio.wait_for(scrape_started.wait(), timeout=1)
    assert not slow_finished.is_set()
    assert scraped == ["https://fast.example/page"]

    slow_finished.set()
    result = await asyncio.wait_for(task, timeout=1)
    assert result["target_urls"] == [
        "https://fast.example/page",
        "https://slow.example/page",
    ]
    assert callback_urls == scraped
    monkeypatch.setattr(discovery, "_filter_and_rank_urls", original_rank)


@pytest.mark.asyncio
async def test_failed_query_does_not_cancel_other_query_acquisition():
    from agent.research.discovery import _run_multi_query_discover_and_scrape

    async def search(query: str, **_kwargs):
        if query == "failed":
            raise RuntimeError("fixture search failure")
        return ([_result("https://healthy.example/page")], "healthy")

    scraped: list[str] = []
    searxng = MagicMock(search=AsyncMock(side_effect=search))
    scraper = _scraper(asyncio.Event(), scraped)
    result = await _run_multi_query_discover_and_scrape(
        queries=["failed", "healthy"],
        urls=None,
        searxng=searxng,
        scraper=scraper,
    )

    assert scraped == ["https://healthy.example/page"]
    assert result["target_urls"] == ["https://healthy.example/page"]


@pytest.mark.asyncio
async def test_credit_admission_waits_for_query_order_before_spending_budget():
    """A quick later result cannot consume the only credit ahead of query zero."""
    from agent.research import discovery

    slow_finished = asyncio.Event()
    later_finished = asyncio.Event()
    scraped: list[str] = []

    async def search(query: str, **_kwargs):
        if query == "first":
            await slow_finished.wait()
            return ([_result("https://source-a.example/important/article")], "healthy")
        later_finished.set()
        return ([_result("https://source-b.example/other")], "healthy")

    searxng = MagicMock(search=AsyncMock(side_effect=search))
    scraper = _scraper(asyncio.Event(), scraped)
    task = asyncio.create_task(
        discovery._run_multi_query_discover_and_scrape(
            queries=["first", "later"],
            urls=None,
            searxng=searxng,
            scraper=scraper,
            max_credits=1,
        )
    )
    await asyncio.wait_for(later_finished.wait(), timeout=1)
    assert scraped == []

    slow_finished.set()
    result = await asyncio.wait_for(task, timeout=1)
    assert scraped == ["https://source-a.example/important/article"]
    assert [artifact.url for artifact in result["artifacts"]] == scraped
    assert result["credits_used"] == 1


@pytest.mark.asyncio
async def test_credit_admission_filters_blacklisted_urls_before_fetch():
    from agent.research import discovery

    async def search(_query: str, **_kwargs):
        return (
            [
                _result("https://bad.example/login"),
                _result("https://good.example/article/detail"),
            ],
            "healthy",
        )

    scraped: list[str] = []
    searxng = MagicMock(search=AsyncMock(side_effect=search))
    scraper = _scraper(asyncio.Event(), scraped)
    result = await discovery._run_multi_query_discover_and_scrape(
        queries=["one"],
        urls=None,
        searxng=searxng,
        scraper=scraper,
        max_credits=1,
    )

    assert scraped == ["https://good.example/article/detail"]
    assert result["credits_used"] == 1
    assert all("/login" not in url for url in result["target_urls"])


@pytest.mark.asyncio
async def test_discovery_cancellation_awaits_search_and_scrape_tasks():
    from agent.research.discovery import _run_multi_query_discover_and_scrape

    cancelled = asyncio.Event()

    async def search(_query: str, **_kwargs):
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    searxng = MagicMock(search=AsyncMock(side_effect=search))
    scraper = _scraper(asyncio.Event(), [])
    task = asyncio.create_task(
        _run_multi_query_discover_and_scrape(
            queries=["one", "two"], urls=None, searxng=searxng, scraper=scraper
        )
    )
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_loop_forwards_source_event_before_discovery_completes():
    from agent.research.loop import _discover_with_progress
    from agent.research.sources import SourceArtifact

    discovery_finished = asyncio.Event()
    artifact = SourceArtifact(
        url="https://fast.example/page", markdown="fixture", source="fixture"
    )

    async def factory(on_artifact, on_search_results):
        await on_search_results([_result(artifact.url)])
        await on_artifact(artifact)
        await asyncio.sleep(0.02)
        discovery_finished.set()
        return {"context": "fixture"}

    seen = []
    async for event in _discover_with_progress(factory):
        seen.append(event)
        if event["type"] == "source_scraped":
            assert not discovery_finished.is_set()

    assert [event["type"] for event in seen] == [
        "sources_pending",
        "source_scraped",
        "_discovery_complete",
    ]
    assert seen[0]["sources"][0]["url"] == artifact.url
    assert seen[1]["type"] == "source_scraped"
    assert seen[-1]["type"] == "_discovery_complete"


@pytest.mark.asyncio
async def test_loop_progress_cancellation_cleans_waiter_and_discovery():
    from agent.research.loop import _discover_with_progress

    cancelled = asyncio.Event()

    async def factory(_on_artifact, _on_search_results):
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    async def consume():
        async for _event in _discover_with_progress(factory):
            pass

    task = asyncio.create_task(consume())
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_scrape_urls_keeps_all_successes_from_completed_batch():
    from agent.research.discovery import _scrape_urls

    scraped: list[str] = []
    callbacks: list[str] = []
    scraper = MagicMock()

    async def scrape(url: str, **_kwargs):
        scraped.append(url)
        await asyncio.sleep(0)
        return {
            "success": True,
            "data": {"markdown": f"content for {url}", "source": "fixture"},
        }

    scraper.scrape_with_fallback = AsyncMock(side_effect=scrape)

    async def on_artifact(artifact):
        callbacks.append(artifact.url)

    urls = [f"https://batch.example/{i}" for i in range(5)]
    artifacts = await _scrape_urls(
        urls, scraper, min_sources=3, max_concurrent=5, on_artifact=on_artifact
    )

    assert scraped == urls
    assert {artifact.url for artifact in artifacts} == set(urls)
    assert set(callbacks) == set(urls)


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


@pytest.mark.asyncio
async def test_multi_query_keeps_all_successes_from_completed_scrape_batch():
    from agent.research.discovery import _run_multi_query_discover_and_scrape

    urls = [f"https://multi.example/{i}" for i in range(5)]
    searxng = MagicMock()
    searxng.search = AsyncMock(return_value=([_result(url) for url in urls], "healthy"))
    scraper = MagicMock()

    async def scrape(url: str, **_kwargs):
        await asyncio.sleep(0)
        return {
            "success": True,
            "data": {"markdown": f"content for {url}", "source": "fixture"},
        }

    scraper.scrape_with_fallback = AsyncMock(side_effect=scrape)
    result = await _run_multi_query_discover_and_scrape(
        queries=["one"], urls=None, searxng=searxng, scraper=scraper
    )

    assert {artifact.url for artifact in result["artifacts"]} == set(urls)


@pytest.mark.asyncio
async def test_closing_public_stream_drains_discovery_and_clients():
    from unittest.mock import patch

    from agent.research.loop import run_research_stream

    from tests.service.test_issue624_source_registry import (
        _patch_research_clients,
        _research_clients,
    )

    clients = _research_clients({})
    cancelled = asyncio.Event()

    async def discovery(**kwargs):
        await kwargs["on_search_results"]([{"url": "https://example.com/a"}])
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    with (
        _patch_research_clients(*clients),
        patch(
            "agent.research.loop._run_multi_query_discover_and_scrape",
            side_effect=discovery,
        ),
    ):
        stream = run_research_stream(prompt="q", llm_model="fixture")
        async for event in stream:
            if event["type"] == "sources_pending":
                break
        await stream.aclose()
    assert cancelled.is_set()
    for client in clients:
        client.close.assert_awaited_once()
