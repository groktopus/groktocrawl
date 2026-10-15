"""Bounded scholarly searches must fail closed across the HTTP boundary."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from agent.exceptions import UpstreamError
from agent.models import SearchRequest
from agent.searxng_client import SearXNGClient
from pydantic import ValidationError

DATES = {"date_from": "2026-01-01", "date_to": "2026-01-31", "engines": ["openalex"]}


def proof(bounds=DATES):
    applied = {key: bounds.get(key) for key in ("date_from", "date_to")}
    return {
        "requested": {**applied, "time_range": None},
        "applied": applied,
        "status": "enforced",
        "engines": ["openalex"],
        "enforced_by": ["upstream:openalex"],
        "reason": "Audited publication dates",
        "warnings": [],
    }


@pytest.mark.parametrize(
    "extra",
    [
        {"date_from": "2026-1-01"},
        {"date_from": "2026-02-30"},
        {"date_from": "2026-02-01"},
        {"dateFrom": "2026-01-01"},
        {"date_form": "2026-01-01"},
        {"published_after": "2026-01-01"},
        {"time_range": "week"},
        {"engines": ["openalex", "arxiv"]},
        {"engines": None},
        *[{"search_type": v} for v in ("rich", "deep")],
        *[
            {"retrieval_mode": v}
            for v in ("semantic", "hybrid", "vector", "hybrid_vector")
        ],
        {"stream": True},
        {"sources": ["images"]},
        {"sources": ["news"]},
        {"categories": ["news"]},
        {"contents": {}},
        {"output_schema": {}},
    ],
)
def test_invalid_or_unsupported_pre_dispatch(extra):
    with pytest.raises(ValidationError):
        SearchRequest(query="research", **(DATES | extra))


@pytest.mark.parametrize(
    "bounds",
    [
        DATES,
        DATES | {"date_from": None},
        DATES | {"date_to": None},
        DATES | {"date_to": "2026-01-01"},
    ],
)
def test_inclusive_one_sided_bounds(bounds):
    assert SearchRequest(query="research", **bounds).engines == ["openalex"]


async def query(payload, bounds=DATES):
    client = SearXNGClient("http://backend.test")
    seen = []

    def respond(request):
        seen.append(request)
        return httpx.Response(200, json=payload)

    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    try:
        results, health = await client.search("research", **bounds)
        return results, health, seen
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("cached", [False, True])
async def test_exact_forwarding_provenance_and_cached_proof(cached):
    payload = {
        "results": [
            {
                "url": "https://example.org",
                "engine": "openalex",
                "published_date": "2026-01-01",
            }
        ],
        "meta": {
            "date_filter": proof(),
            "cache_hit": cached,
            "engine_status": {"openalex": {"status": "ok"}},
            "partial": False,
            "deadline_exceeded": False,
        },
    }
    results, health, requests = await query(payload)
    assert results[0]["publishedDate"] == "2026-01-01"
    assert health.date_filter == proof()
    assert dict(requests[0].url.params)["engines"] == "openalex"
    assert dict(requests[0].url.params)["date_from"] == DATES["date_from"]
    assert dict(requests[0].url.params)["date_to"] == DATES["date_to"]
    assert dict(requests[0].url.params)["categories"] == "science"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_proof",
    [
        None,
        {},
        proof() | {"status": "partially_enforced"},
        proof() | {"applied": {}},
        proof() | {"enforced_by": []},
        proof() | {"engines": ["arxiv"]},
    ],
)
async def test_old_or_mismatched_backend_fails_closed(bad_proof):
    with pytest.raises(UpstreamError):
        await query(
            {
                "results": [],
                "meta": {
                    "date_filter": bad_proof,
                    "engine_status": {"openalex": {"status": "ok"}},
                    "partial": False,
                    "deadline_exceeded": False,
                },
            }
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "published,engine",
    [
        (None, "openalex"),
        ("bad", "openalex"),
        ("2025-12-31", "openalex"),
        ("2026-02-01", "openalex"),
        ("2026-01-15", "arxiv"),
    ],
)
async def test_untrusted_result_dates_fail_closed(published, engine):
    with pytest.raises(UpstreamError):
        await query(
            {
                "results": [{"published_date": published, "engine": engine}],
                "meta": {
                    "date_filter": proof(),
                    "engine_status": {"openalex": {"status": "ok"}},
                    "partial": False,
                    "deadline_exceeded": False,
                },
            }
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("version", [1, 2])
async def test_route_preserves_proof_and_publication_date(monkeypatch, version):
    from agent.routes.search import search, search_v1

    mock = SimpleNamespace(
        search=AsyncMock(
            return_value=(
                [
                    {
                        "url": "https://example.org",
                        "title": "paper",
                        "publishedDate": "2026-01-01",
                    }
                ],
                SimpleNamespace(
                    date_filter=proof(), result_coverage=None, engines_responding=1
                ),
            )
        ),
        close=AsyncMock(),
    )
    monkeypatch.setattr("agent.searxng_client.SearXNGClient", lambda _: mock)
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(searxng_url="http://backend"))
    )
    result = await (search_v1 if version == 1 else search)(
        request, SearchRequest(query="paper", **DATES)
    )
    data = result if version == 1 else result.model_dump()
    assert data["date_filter"] == proof()
    assert (data["data"] if version == 1 else data["data"]["web"])[0][
        "publishedDate"
    ] == "2026-01-01"
    assert mock.search.call_args.kwargs["date_from"] == DATES["date_from"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "meta",
    [
        {},
        {
            "engine_status": {"openalex": {"status": "timeout"}},
            "partial": False,
            "deadline_exceeded": False,
        },
        {
            "engine_status": {"openalex": {"status": "ok"}},
            "partial": True,
            "deadline_exceeded": False,
        },
    ],
)
async def test_failed_or_missing_acquisition_cannot_look_like_successful_empty(meta):
    with pytest.raises(UpstreamError):
        await query({"results": [], "meta": {"date_filter": proof(), **meta}})


@pytest.mark.asyncio
async def test_successful_empty_requires_acquisition_and_proof():
    results, health, _ = await query(
        {
            "results": [],
            "meta": {
                "date_filter": proof(),
                "engine_status": {"openalex": {"status": "ok"}},
                "partial": False,
                "deadline_exceeded": False,
            },
        }
    )
    assert results == []
    assert health.date_filter == proof()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["timeout", "http"])
async def test_upstream_failure_never_degrades_bounded_search(failure):
    client = SearXNGClient("http://backend.test")

    def respond(request):
        if failure == "timeout":
            raise httpx.ReadTimeout("timeout", request=request)
        return httpx.Response(400, json={"error": "unsupported"})

    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    try:
        with pytest.raises(UpstreamError):
            await client.search("paper", **DATES)
    finally:
        await client.close()


def test_unrelated_legacy_extras_remain_compatible():
    assert (
        SearchRequest(query="paper", candidate="value", update=True, timeout=5).query
        == "paper"
    )


@pytest.mark.asyncio
async def test_audited_empty_result_has_no_unrelated_brave_warning(monkeypatch):
    from agent.routes.search import search

    mock = SimpleNamespace(
        search=AsyncMock(
            return_value=(
                [],
                SimpleNamespace(
                    date_filter=proof(), result_coverage=None, engines_responding=0
                ),
            )
        ),
        close=AsyncMock(),
    )
    monkeypatch.setattr("agent.searxng_client.SearXNGClient", lambda _: mock)
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(searxng_url="http://backend"))
    )
    result = await search(request, SearchRequest(query="paper", **DATES))
    assert result.warning is None
    assert result.date_filter == proof()
