"""Deterministic concurrency and reuse checks for search acquisition."""

from __future__ import annotations

import asyncio

import pytest
from agent.models import ContentsOptions
from agent.research.acquisition import acquire_source_artifacts
from agent.research.contents import process_contents_for_results
from agent.research.search import run_rich_search
from agent.research.sources import SourceArtifact


class CountingScraper:
    def __init__(self, delay: float = 0.01) -> None:
        self.delay = delay
        self.calls: list[tuple[str, dict]] = []
        self.active = 0
        self.max_active = 0

    async def scrape(self, url: str, **kwargs) -> dict:
        self.calls.append((url, kwargs))
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(self.delay)
            return {
                "success": True,
                "data": {"markdown": f"content for {url}", "source": "test"},
            }
        finally:
            self.active -= 1


@pytest.mark.asyncio
async def test_acquisition_deduplicates_and_bounds_fetch_overlap() -> None:
    scraper = CountingScraper(delay=0.02)
    results = [
        {"url": "https://EXAMPLE.test:443/a#top", "title": "A"},
        {"url": "https://example.test/a", "title": "duplicate"},
        *({"url": f"https://example.test/{i}", "title": str(i)} for i in range(4)),
    ]

    acquired = await acquire_source_artifacts(results, scraper, max_concurrent=2)

    assert len(scraper.calls) == 5
    assert scraper.max_active == 2
    assert [artifact.url for artifact in acquired.artifacts] == [
        "https://EXAMPLE.test:443/a#top",
        "https://example.test/0",
        "https://example.test/1",
        "https://example.test/2",
        "https://example.test/3",
    ]


@pytest.mark.asyncio
async def test_barrier_refusal_is_reusable_metadata_without_retry() -> None:
    class BarrierScraper(CountingScraper):
        async def scrape(self, url: str, **kwargs) -> dict:
            self.calls.append((url, kwargs))
            return {
                "success": True,
                "warning": "challenge",
                "data": {"markdown": "checking your browser"},
            }

    scraper = BarrierScraper()
    first = await acquire_source_artifacts(
        [{"url": "https://barrier.test", "description": "safe fallback"}], scraper
    )
    second = await acquire_source_artifacts(
        [{"url": "https://barrier.test", "description": "safe fallback"}],
        scraper,
        refused_urls=set(first.refusals),
    )

    assert first.artifacts == []
    assert set(first.refusals) == {"https://barrier.test/"}
    assert second.artifacts == []
    assert len(scraper.calls) == 1


@pytest.mark.asyncio
async def test_contents_reuses_artifact_and_overlaps_llm_transforms() -> None:
    scraper = CountingScraper()
    artifact = SourceArtifact(
        url="https://example.test/a",
        markdown="A useful page",
        char_count=14,
    )

    class CountingLLM:
        def __init__(self) -> None:
            self.active = 0
            self.max_active = 0

        async def generate(self, **kwargs) -> str:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            try:
                await asyncio.sleep(0.02)
                return "generated"
            finally:
                self.active -= 1

    llm = CountingLLM()
    enriched = await process_contents_for_results(
        [{"url": "https://example.test/a", "title": "A"}],
        "page",
        ContentsOptions(highlights=True, summary=True),
        llm,
        scraper,
        artifacts=[artifact],
    )

    assert scraper.calls == []
    assert enriched[0]["markdown"] == "A useful page"
    assert enriched[0]["highlights"] == "generated"
    assert enriched[0]["summary"] == "generated"
    assert llm.max_active == 2


@pytest.mark.asyncio
async def test_option_mismatch_fetches_once_for_new_contract() -> None:
    scraper = CountingScraper()
    artifact = SourceArtifact(
        url="https://example.test/a",
        markdown="cached markdown",
        char_count=15,
    )

    acquired = await acquire_source_artifacts(
        [{"url": "https://example.test/a"}],
        scraper,
        existing=[artifact],
        contents_options={"extras": {"links": 2}},
    )

    assert len(scraper.calls) == 1
    assert scraper.calls[0][1] == {"contents": {"extras": {"links": 2}}}
    assert acquired.artifacts[0].markdown == "content for https://example.test/a"


@pytest.mark.asyncio
async def test_rich_search_reuses_rank_artifact_without_scraping_again(
    monkeypatch,
) -> None:
    class FakeLLM:
        async def generate(self, **kwargs):
            return "synthesized"

        async def close(self):
            pass

    class FakeScraper(CountingScraper):
        async def close(self):
            pass

    supplied = FakeScraper()
    monkeypatch.setattr("agent.research.search.ScraperClient", lambda *_a: supplied)
    monkeypatch.setattr("agent.research.search.LLMClient", lambda *_a: FakeLLM())
    await run_rich_search(
        [{"url": "https://example.test/a", "title": "A", "description": "D"}],
        "q",
        limit=1,
        llm_model="model",
        artifacts=[
            SourceArtifact(url="https://example.test/a", markdown="ranked markdown")
        ],
    )
    assert supplied.calls == []
