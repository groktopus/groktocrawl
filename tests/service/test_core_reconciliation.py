"""Combined mainline contracts must survive the richer evidence pipeline."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from agent.models import CitationStyle
from agent.research.loop import run_research
from agent.research.streaming import stream_research_live

from tests.service.test_issue624_source_registry import (
    _patch_research_clients,
    _research_clients,
)
from tests.service.test_research_scrape_all import FakeScraper, FakeSearch, _results


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_full_source_export_coexists_with_bounded_evidence_and_coverage(
    streaming,
):
    url = "https://source.example/spec"
    text = "A general introduction.\n" * 1000 + "Aurora quantum value 791."
    clients = _research_clients(
        {"first-a": [{"url": url, "title": "Spec"}], "first-b": []},
        {url: text},
    )

    async def generate_stream(**_kwargs):
        yield {"type": "done", "full_content": "answer [1]"}

    clients[2].generate_stream = MagicMock(side_effect=generate_stream)
    options = {
        "prompt": "Aurora quantum value",
        "llm_model": "fixture",
        "search_type": "deep",
        "include_source_content": True,
        "evidence_budget_chars": 256,
    }
    with (
        _patch_research_clients(*clients, gaps=False),
        patch(
            "agent.research.loop.load_settings",
            return_value=SimpleNamespace(typesafe_api_key=""),
        ),
    ):
        if streaming:
            events = [
                json.loads(event.removeprefix("data: "))
                async for event in stream_research_live(
                    **options,
                    urls=None,
                    schema=None,
                    searxng_url="http://search.fixture",
                    scraper_url="http://scraper.fixture",
                    llm_base_url="http://llm.fixture",
                    llm_api_key="",
                    requested_model=None,
                    max_searches_per_request=5,
                    include_images=False,
                    citation_style=CitationStyle.inline,
                )
                if event != "data: [DONE]\n\n"
            ]
            result = next(event for event in events if event["type"] == "done")
        else:
            result = await run_research(**options)
    assert result["source_contents"] == {url: text}
    assert result["coverage"]["planned_queries"] == 2
    assert result["coverage"]["executed_queries"] == 2
    assert result["coverage"]["attempted_urls"] == 1
    assert result["coverage"]["successful_sources"] == 1
    assert not result["coverage"]["coverage_complete"]
    assert result["coverage"]["evidence"] == result["evidence_coverage"]
    evidence = result["evidence_coverage"]
    assert evidence["selected_chars"] <= 256
    assert evidence["omitted_chars"] > 0
    assert evidence["sources"][0]["spans"][0]["quote_digest"]
    generation = clients[2].generate_stream if streaming else clients[2].generate
    assert "791" in generation.call_args.kwargs["context"]


@pytest.mark.asyncio
@pytest.mark.parametrize("seeded", [False, True])
async def test_exhaustive_acquisition_reports_attempts_and_failures(seeded):
    from agent.research.discovery import _run_multi_query_discover_and_scrape

    urls = [f"https://source.example/{index}" for index in range(8)]
    search = FakeSearch({"topic": _results(urls)})
    scraper = FakeScraper({urls[0]})
    result = await _run_multi_query_discover_and_scrape(
        ["topic"], urls if seeded else None, search, scraper, max_credits=6
    )
    assert len(scraper.calls) == 6
    assert result["coverage"]["attempted_urls"] == 6
    assert result["coverage"]["failures"] == 1
    assert result["coverage"]["successful_sources"] == 5
