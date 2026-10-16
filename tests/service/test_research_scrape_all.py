"""Focused tests for exhaustive research discovery acquisition."""

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "agent-svc"))

from agent.research.discovery import (
    _run_multi_query_discover_and_scrape,
    _run_research_discover_and_scrape,
)


class FakeSearch:
    def __init__(self, results_by_query: dict[str, list[dict]]) -> None:
        self.results_by_query = results_by_query
        self.calls: list[str] = []

    async def search(self, query: str, *, limit: int | None, raise_on_rate_limit: bool):
        self.calls.append(query)
        return self.results_by_query[query], None


class FakeScraper:
    def __init__(self, failures: set[str] | None = None) -> None:
        self.failures = failures or set()
        self.calls: list[str] = []
        self.in_flight = 0
        self.max_in_flight = 0

    async def scrape_with_fallback(self, url: str, **_kwargs):
        self.calls.append(url)
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        await asyncio.sleep(0)
        self.in_flight -= 1
        if url in self.failures:
            return {"success": False, "data": {}, "error": "failed"}
        return {
            "success": True,
            "data": {"markdown": f"content for {url}", "source": "test"},
        }


def _results(urls: list[str]) -> list[dict]:
    return [{"url": url, "title": url} for url in urls]


def test_single_query_scrapes_every_result_over_ten_despite_a_failure() -> None:
    async def run() -> None:
        urls = [f"https://example.test/{i}" for i in range(24)]
        urls[-1] = "https://www.youtube.com/watch?v=research"
        search = FakeSearch({"topic": _results(urls)})
        failed_url = urls[3]
        scraper = FakeScraper({failed_url})

        result = await _run_research_discover_and_scrape("topic", None, search, scraper)

        assert search.calls == ["topic"]
        assert set(scraper.calls) == set(urls)
        assert set(result["novel_sources"]) == set(urls) - {failed_url}
        assert scraper.max_in_flight <= 5

    asyncio.run(run())


def test_multi_query_scrapes_union_without_three_source_or_twenty_url_caps() -> None:
    async def run() -> None:
        first = [f"https://one.test/{i}" for i in range(10)]
        second = ["https://one.test/0"] + [f"https://two.test/{i}" for i in range(10)]
        third = [f"https://three.test/{i}" for i in range(10)]
        search = FakeSearch(
            {"one": _results(first), "two": _results(second), "three": _results(third)}
        )
        scraper = FakeScraper()

        result = await _run_multi_query_discover_and_scrape(
            ["one", "two", "three"], None, search, scraper
        )

        expected = set(first + second + third)
        assert set(scraper.calls) == expected
        assert set(result["novel_sources"]) == expected
        assert len(result["target_urls"]) == len(expected)
        assert scraper.max_in_flight <= 5

    asyncio.run(run())


def test_discovery_continues_after_failures_and_honors_explicit_credit_budget() -> None:
    async def run() -> None:
        urls = [f"https://example.test/{i}" for i in range(5)]
        search = FakeSearch({"topic": _results(urls)})
        scraper = FakeScraper({urls[0]})

        result = await _run_research_discover_and_scrape(
            "topic", None, search, scraper, max_credits=3
        )

        assert len(scraper.calls) == 3
        assert urls[0] in scraper.calls
        assert set(scraper.calls[1:]) == set(urls[1:3])
        assert len(result["novel_sources"]) == 2

    asyncio.run(run())
