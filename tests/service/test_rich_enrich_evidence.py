"""Rich/search extraction scans retained bodies while bounding selected evidence."""

from __future__ import annotations

import asyncio
import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from agent.research.enrich import run_enrich_pipeline
from agent.research.search import run_rich_search, run_search_stream
from agent.research.sources import SourceArtifact
from agent.searxng_client import SearchHealth

MARKER = "Quasar omega revenue is 42 million. Ω late evidence."
BODY = "Unrelated introduction with background details.\n" * 1800 + MARKER
ROWS = [
    {"url": "https://example.test/first", "title": "First", "description": "snippet"},
    {"url": "https://example.test/second", "title": "Second", "description": "snippet"},
]


class FakeLLM:
    def __init__(self):
        self.prompts = []

    async def generate(self, **kwargs):
        self.prompts.append(kwargs["user_prompt"])
        return '{"revenue":{"value":"42 million"}}'

    async def generate_stream(self, **kwargs):
        self.prompts.append(kwargs["user_prompt"])
        yield {"type": "token", "content": "answer [1] [2]"}
        yield {"type": "done", "full_content": "answer [1] [2]"}

    async def close(self):
        pass


def assert_coverage(coverage, source_count=2):
    assert coverage["budget_chars"] == 32000
    assert coverage["selected_chars"] <= 32000
    assert coverage["source_chars"] == len(BODY) * source_count
    assert (
        coverage["omitted_chars"]
        == coverage["source_chars"] - coverage["selected_chars"]
    )
    assert coverage["coverage_complete"] is False
    assert coverage["complete"] is False
    assert all(
        row["content_sha256"] == hashlib.sha256(BODY.encode()).hexdigest()
        for row in coverage["sources"]
    )


@pytest.mark.asyncio
async def test_rich_sync_selects_late_evidence_with_aggregate_budget_and_citation_order(
    monkeypatch,
):
    llm = FakeLLM()
    scraper = SimpleNamespace(scrape=AsyncMock(), close=AsyncMock())
    monkeypatch.setattr("agent.research.search.ScraperClient", lambda *_a: scraper)
    monkeypatch.setattr("agent.research.search.LLMClient", lambda *_a: llm)
    artifacts = [SourceArtifact(url=row["url"], markdown=BODY) for row in ROWS]
    result = await run_rich_search(
        ROWS, "quasar omega revenue", llm_model="fixture", artifacts=artifacts
    )
    assert result is not None
    assert_coverage(result["evidence_coverage"])
    assert MARKER in llm.prompts[0]
    assert llm.prompts[0].index("[1] URL: " + ROWS[0]["url"]) < llm.prompts[0].index(
        "[2] URL: " + ROWS[1]["url"]
    )
    assert [row["url"] for row in result["grounding"]] == [row["url"] for row in ROWS]
    assert [row["url"] for row in result["evidence_coverage"]["sources"]] == [
        row["url"] for row in ROWS
    ]
    assert all(artifact.markdown == BODY for artifact in artifacts)
    scraper.scrape.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("schema", [None, {"type": "object"}])
async def test_rich_stream_reports_preview_omissions_and_full_body_evidence(
    monkeypatch, schema
):
    llm = FakeLLM()
    searcher = SimpleNamespace(
        search=AsyncMock(return_value=(ROWS, SearchHealth())), close=AsyncMock()
    )

    class Scraper:
        async def scrape(self, url, **kwargs):
            # Reverse fetch completion cannot reorder numbered citations.
            if url == ROWS[0]["url"]:
                await asyncio.sleep(0.01)
            return {"success": True, "data": {"markdown": BODY}}

        async def close(self):
            pass

    monkeypatch.setattr(
        "agent.research.search.SearXNGClient", lambda *_a, **_kw: searcher
    )
    monkeypatch.setattr("agent.research.search.ScraperClient", lambda *_a: Scraper())
    monkeypatch.setattr("agent.research.search.LLMClient", lambda *_a: llm)
    events = [
        event
        async for event in run_search_stream(
            "quasar omega revenue",
            search_type="rich",
            output_schema=schema,
            llm_model="fixture",
        )
    ]
    previews = [event for event in events if event["type"] == "scrape_result"]
    assert len(previews) == 2
    for preview in previews:
        assert preview["contents"]["markdown"] == BODY[:3000]
        assert preview["source_chars"] == len(BODY)
        assert preview["preview_chars"] == 3000
        assert preview["omitted_chars"] == len(BODY) - 3000
    done = next(event for event in events if event["type"] == "done")
    assert_coverage(done["evidence_coverage"])
    assert done["evidence_coverage"] == done["output"]["evidence_coverage"]
    assert MARKER in llm.prompts[0]
    assert [row["url"] for row in done["output"]["grounding"]] == [
        row["url"] for row in ROWS
    ]
    assert llm.prompts[0].index("[1] URL: " + ROWS[0]["url"]) < llm.prompts[0].index(
        "[2] URL: " + ROWS[1]["url"]
    )


@pytest.mark.asyncio
async def test_enrichment_selects_late_field_evidence_and_returns_coverage(monkeypatch):
    llm = FakeLLM()
    searcher = SimpleNamespace(
        search=AsyncMock(return_value=([ROWS[0]], SearchHealth())), close=AsyncMock()
    )
    scraper = SimpleNamespace(
        scrape=AsyncMock(return_value={"success": True, "data": {"markdown": BODY}}),
        close=AsyncMock(),
    )
    monkeypatch.setattr("agent.research.enrich.SearXNGClient", lambda *_a: searcher)
    monkeypatch.setattr("agent.research.enrich.ScraperClient", lambda *_a: scraper)
    monkeypatch.setattr("agent.research.enrich.LLMClient", lambda *_a: llm)
    result = await run_enrich_pipeline(
        [{"company": "Acme"}],
        {"revenue": SimpleNamespace(description="quasar omega revenue")},
        llm_model="fixture",
    )
    assert MARKER in llm.prompts[0]
    assert result[0]["enrichments"]["revenue"]["value"] == "42 million"
    assert_coverage(result[0]["evidence_coverage"], source_count=1)
    assert scraper.scrape.await_count == 1
