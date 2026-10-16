"""Search result acquisition behavior for the autonomous agent."""

import asyncio
from types import SimpleNamespace

import pytest
from agent.research.discovery import (
    _run_multi_query_discover_and_scrape,
    _run_research_discover_and_scrape,
)
from agent.research_memory import compute_fingerprint
from agent.searxng_client import SearXNGClient


class _Search:
    def __init__(self, results: dict[str, list[dict]]) -> None:
        self.results = results
        self.calls: list[tuple[str, int | None]] = []

    async def search(
        self, query: str, *, limit: int | None, raise_on_rate_limit: bool
    ):
        self.calls.append((query, limit))
        return self.results[query], None


class _Scraper:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def scrape_with_fallback(self, url: str, **_kwargs):
        self.calls.append(url)
        await asyncio.sleep(0)
        return {"success": True, "data": {"markdown": f"content {url}"}}


def _results(prefix: str, count: int) -> list[dict]:
    return [
        {"url": f"https://{prefix}.test/{i}", "title": str(i)} for i in range(count)
    ]


def test_credit_limit_participates_in_research_memory_fingerprint() -> None:
    assert compute_fingerprint(prompt="q", max_credits=1) != compute_fingerprint(
        prompt="q", max_credits=10
    )


@pytest.mark.asyncio
async def test_single_query_requests_all_results_and_scrapes_all_returned_results() -> None:
    search = _Search({"topic": _results("single", 7)})
    scraper = _Scraper()

    result = await _run_research_discover_and_scrape(
        "topic", None, search, scraper
    )

    assert search.calls == [("topic", None)]
    assert set(scraper.calls) == {item["url"] for item in search.results["topic"]}
    assert len(result["target_urls"]) == 7


@pytest.mark.asyncio
async def test_multi_query_requests_all_results_for_each_query() -> None:
    search = _Search({"one": _results("one", 6), "two": _results("two", 6)})
    scraper = _Scraper()

    result = await _run_multi_query_discover_and_scrape(
        ["one", "two"], None, search, scraper
    )

    assert sorted(search.calls) == [("one", None), ("two", None)]
    assert len(result["target_urls"]) == 12


@pytest.mark.asyncio
async def test_searxng_client_supports_unbounded_results_without_sending_limit_upstream() -> None:
    client = SearXNGClient("http://searxng.test")
    captured: dict = {}

    async def fake_get(_url, params=None):
        captured.update(params or {})
        return SimpleNamespace(
            status_code=200,
            json=lambda: {"results": _results("raw", 12), "engines": []},
        )

    client._client.get = fake_get
    try:
        results, _ = await client.search("topic", limit=None)
    finally:
        await client.close()

    assert len(results) == 12
    assert "limit" not in captured
    assert "max_results" not in captured


@pytest.mark.asyncio
async def test_searxng_client_retains_default_limit_for_non_agent_callers() -> None:
    client = SearXNGClient("http://searxng.test")

    async def fake_get(_url, params=None):
        return SimpleNamespace(
            status_code=200,
            json=lambda: {"results": _results("raw", 12), "engines": []},
        )

    client._client.get = fake_get
    try:
        results, _ = await client.search("topic")
    finally:
        await client.close()

    assert len(results) == 10
