"""Source metadata survives acquisition without new network/model work."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from agent.models import ContentsOptions, SearchRequest, SearchResult
from agent.research.hybrid import _collect_candidates
from agent.search_metadata import search_metadata
from agent.searxng_client import SearchHealth, SearXNGClient

SOURCE = {
    "url": "https://example.test/paper",
    "title": "Public fixture",
    "description": "Public scholarly snippet",
    "engine": "brave",
    "engines": ["semanticscholar", "brave"],
    "media": {
        "media_type": "image",
        "url": "https://images.example.test/full.jpg",
        "thumbnail": "https://images.example.test/thumb.jpg",
        "source": "https://example.test/paper",
        "width": 640,
        "height": 480,
    },
    "doi": "10.1234/fixture",
    "authors": ["A. Author"],
    "journal": "Fixture Journal",
    "comments": "Retracted Publication",
}


def assert_metadata(row):
    for key, value in search_metadata(SOURCE).items():
        assert (set(row[key]) == set(value)) if key == "engines" else row[key] == value
    assert isinstance(row["url"], str)


def test_public_model_retains_metadata_and_legacy_missing_shape():
    assert_metadata(SearchResult(**SOURCE).model_dump())
    legacy = SearchResult(url=SOURCE["url"], title=SOURCE["title"]).model_dump()
    assert "engine" not in legacy and "doi" not in legacy


def test_malformed_metadata_is_omitted_and_single_engine_fallback():
    assert search_metadata({"engines": "bad", "doi": ["bad"], "url": "bad"}) == {}
    assert search_metadata({"engine": "brave"}) == {
        "engine": "brave",
        "engines": ["brave"],
    }


@pytest.mark.asyncio
async def test_client_retains_optional_fields_without_extra_requests():
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(
            200, json={"results": [{**SOURCE, "content": SOURCE["description"]}]}
        )

    client = SearXNGClient("https://search.example.test")
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    try:
        rows, _ = await client.search("public query")
    finally:
        await client.close()
    assert_metadata(rows[0])
    assert len(calls) == 1


def test_hybrid_blend_keeps_web_metadata_when_vector_matches():
    _, _, candidates = _collect_candidates(
        [SOURCE], [{"url": SOURCE["url"], "title": "Vector", "score": 0.9}]
    )
    assert_metadata(next(iter(candidates.values())))


@pytest.mark.asyncio
@pytest.mark.parametrize("contents", [None, ContentsOptions(summary=True)])
async def test_fast_and_enriched_routes_keep_metadata(monkeypatch, contents):
    from agent.routes.search import search

    searcher = SimpleNamespace(
        search=AsyncMock(return_value=([SOURCE], SearchHealth())), close=AsyncMock()
    )
    scraper = SimpleNamespace(
        scrape=AsyncMock(
            return_value={"success": True, "data": {"markdown": "Public evidence"}}
        ),
        close=AsyncMock(),
    )
    llm = SimpleNamespace(
        generate=AsyncMock(return_value="Fixture summary"), close=AsyncMock()
    )
    for target, value in [
        ("agent.searxng_client.SearXNGClient", searcher),
        ("agent.scraper_client.ScraperClient", scraper),
        ("agent.llm.LLMClient", llm),
    ]:
        monkeypatch.setattr(target, lambda *_a, _value=value, **_kw: _value)
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
        SearchRequest(query="Public query", contents=contents),
    )
    assert_metadata(response.data["web"][0])
    assert searcher.search.await_count == 1


@pytest.mark.asyncio
async def test_deep_search_preserves_metadata(monkeypatch):
    from agent.research.search import run_deep_search

    searcher = SimpleNamespace(
        search=AsyncMock(return_value=([SOURCE], SearchHealth())), close=AsyncMock()
    )
    llm = SimpleNamespace(generate=AsyncMock(return_value="[]"), close=AsyncMock())
    monkeypatch.setattr(
        "agent.research.search.SearXNGClient", lambda *_a, **_kw: searcher
    )
    monkeypatch.setattr("agent.research.search.LLMClient", lambda *_a, **_kw: llm)
    response = await run_deep_search("Public query", 10, llm_model="fixture")
    assert_metadata(response["results"][0].model_dump())


@pytest.mark.parametrize(
    "unsafe",
    [
        "javascript:alert(1)",
        "data:image/png;base64,a",
        "http://127.0.0.1/a",
        "http://169.254.169.254/a",
        "http://169.254.169%2e254/a",
        "http://127.0%2e0.1/a",
        "http://%31%32%37.0.0.1/a",
        "http://0x7f000001/a",
        "http://0177.0.0.1/a",
        "http://[::1]/a",
        "http://localhost/a",
        "https://internal.local/a",
        "https://user:secret@example.org/a",
        "https://example.org/a?token=secret",
        "https://example.org/a?X-Amz-Signature=secret",
        "https://example.org/" + "a" * 2048,
    ],
)
def test_unsafe_media_urls_are_omitted(unsafe):
    row = search_metadata(
        {
            "media": {
                "media_type": "video",
                "url": unsafe,
                "thumbnail": unsafe,
                "source": unsafe,
            }
        }
    )
    assert row == {"media": {"media_type": "video"}}


@pytest.mark.parametrize(
    "record", [None, [], "bad", {"media_type": []}, {"media_type": "iframe"}]
)
def test_malformed_media_is_omitted(record):
    assert search_metadata({"media": record}) == {}


def test_reported_aliases_and_bounds_without_inventing_media():
    row = search_metadata(
        {
            "category": "videos",
            "video_url": "https://example.org/v.mp4",
            "thumbnail_src": "https://example.org/t.jpg",
            "url": "https://example.org/watch",
            "width": True,
            "height": 100001,
            "duration": float("nan"),
            "iframe_src": "https://example.org/embed",
            "private": "secret",
        }
    )
    assert row == {
        "media": {
            "media_type": "video",
            "url": "https://example.org/v.mp4",
            "thumbnail": "https://example.org/t.jpg",
            "source": "https://example.org/watch",
        }
    }
    assert search_metadata({"img_src": "https://example.org/i.jpg"}) == {}
    assert search_metadata({"category": "news", "publishedDate": "2026-10-07"}) == {
        "publishedDate": "2026-10-07"
    }
    assert (
        search_metadata({"media": {"media_type": "video", "duration": 125.5}})["media"][
            "duration"
        ]
        == 125.5
    )


def test_model_revalidates_cached_media_and_research_projection():
    from agent.models import Source
    from agent.research.state import _compact_source

    assert SearchResult(
        url="https://example.org",
        title="Old",
        media={"media_type": "image", "url": "http://localhost/private"},
    ).model_dump()["media"] == {"media_type": "image"}
    assert (
        Source(url=SOURCE["url"], **search_metadata(SOURCE)).model_dump()["media"]
        == SOURCE["media"]
    )
    assert _compact_source(SOURCE)["media"] == SOURCE["media"]


@pytest.mark.asyncio
async def test_stream_search_keeps_media_without_fetching_preview(monkeypatch):
    from agent.research.search import run_search_stream

    searcher = SimpleNamespace(
        search=AsyncMock(return_value=([SOURCE], SearchHealth())), close=AsyncMock()
    )
    scraper = SimpleNamespace(scrape=AsyncMock(), close=AsyncMock())
    llm = SimpleNamespace(close=AsyncMock())
    for name, fake in [
        ("SearXNGClient", searcher),
        ("ScraperClient", scraper),
        ("LLMClient", llm),
    ]:
        monkeypatch.setattr(
            "agent.research.search." + name, lambda *_a, _fake=fake, **_kw: _fake
        )
    events = [
        event async for event in run_search_stream("fixture", llm_model="fixture")
    ]
    assert_metadata(next(e["result"] for e in events if e["type"] == "search_result"))
    scraper.scrape.assert_not_awaited()


def test_answer_citation_metadata_never_becomes_evidence():
    from agent.research.discovery import _build_answer_context
    from agent.research.sources import SourceArtifact

    artifact = SourceArtifact(
        url=SOURCE["url"], markdown="Only crawled page evidence", source="web"
    )
    built = _build_answer_context([SOURCE], [artifact])
    assert_metadata(built["source_map"][0])
    assert SOURCE["media"]["url"] not in built["context"]
    assert "Only crawled page evidence" in built["context"]


def test_media_survives_artifact_reuse_and_cache_serialization():
    import dataclasses
    import json

    from agent.research.discovery import _discovery_result
    from agent.research.sources import SourceArtifact, SourceRegistry

    artifact = SourceArtifact(
        url=SOURCE["url"], markdown="Crawled content", source="web"
    )
    registry = SourceRegistry()
    registry.register(artifact)
    first = _discovery_result(
        search_results=[SOURCE],
        target_urls=[SOURCE["url"]],
        artifacts=[artifact],
        source_registry=registry,
        reusable_keys=set(),
    )
    assert first["source_details"][0]["media"] == SOURCE["media"]
    restored = SourceArtifact(**json.loads(json.dumps(dataclasses.asdict(artifact))))
    registry.register(restored)
    second = _discovery_result(
        search_results=[],
        target_urls=[SOURCE["url"]],
        artifacts=[restored],
        source_registry=registry,
        reusable_keys={SOURCE["url"]},
    )
    assert second["source_details"][0]["media"] == SOURCE["media"]
    assert second["source_details"][0]["engines"] == ["brave", "semanticscholar"]


@pytest.mark.parametrize("duration", [10**1000, float("inf"), -1, True, "90"])
def test_invalid_duration_cannot_crash_projection(duration):
    assert search_metadata(
        {"media": {"media_type": "video", "duration": duration}}
    ) == {"media": {"media_type": "video"}}


@pytest.mark.asyncio
async def test_image_contract_uses_reported_original_and_provenance(monkeypatch):
    from agent.routes.search import search

    searcher = SimpleNamespace(
        search=AsyncMock(return_value=([SOURCE], SearchHealth())), close=AsyncMock()
    )
    monkeypatch.setattr(
        "agent.searxng_client.SearXNGClient", lambda *_a, **_kw: searcher
    )
    state = SimpleNamespace(
        searxng_url="fixture",
        scraper_url="fixture",
        llm_base_url="fixture",
        llm_api_key="",
        llm_model="fixture",
    )
    response = await search(
        SimpleNamespace(app=SimpleNamespace(state=state)),
        SearchRequest(query="fixture", sources=["images"]),
    )
    row = response.data["images"][0]
    assert row["image_url"] == SOURCE["media"]["url"]
    assert row["image_width"] == 640 and row["image_height"] == 480
    assert row["url"] == SOURCE["url"]
    assert row["media"] == SOURCE["media"]
    assert row["engines"] == ["brave", "semanticscholar"]


def test_partial_alias_metadata_keeps_existing_scholarly_provenance():
    from agent.research.sources import SourceArtifact, SourceRegistry

    registry = SourceRegistry()
    registry.register(
        SourceArtifact(
            url=SOURCE["url"],
            markdown="Page",
            discovery_metadata=search_metadata(SOURCE),
        )
    )
    replacement = SourceArtifact(
        url=SOURCE["url"],
        markdown="Updated page",
        discovery_metadata={"media": {"media_type": "image", "width": 800}},
    )
    registry.register(replacement)
    row = replacement.to_source_detail()
    assert row["doi"] == SOURCE["doi"]
    assert row["engines"] == ["brave", "semanticscholar"]
    assert row["media"]["width"] == 800
