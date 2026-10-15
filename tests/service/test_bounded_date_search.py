"""Date controls must survive every search boundary or fail closed."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from agent.exceptions import UpstreamError
from agent.models import SearchRequest
from agent.routes.search import search, search_v1
from agent.searxng_client import SearXNGClient
from pydantic import ValidationError

BOUNDS = {"date_from": "2026-01-01", "date_to": "2026-01-31"}
PROOF = {
    "requested": {**BOUNDS, "time_range": None},
    "applied": BOUNDS,
    "status": "enforced",
    "reason": "OpenAlex upstream and publication-date postfilter",
    "enforced_by": ["upstream:openalex"],
    "engines": ["openalex"],
    "warnings": [],
}
ROW = {
    "url": "https://example.test/paper",
    "title": "Paper",
    "engine": "openalex",
    "publishedDate": "2026-01-01",
}


@pytest.mark.parametrize(
    "extra",
    [
        {"date_from": "2026-1-01"},
        {"date_from": "2026-02-30"},
        {"date_from": 20260101},
        {"date_to": "2025-01-01"},
        {"time_range": "week"},
        {"engines": None},
        {"engines": ["openalex", "brave"]},
        {"dateFrom": "2026-01-01"},
        {"date_form": "2026-01-01"},
        {"from_date": "2026-01-01"},
        {"endDate": "2026-01-01"},
        {"stream": True},
        {"search_type": "rich"},
        {"search_type": "deep"},
        {"retrieval_mode": "semantic"},
        {"retrieval_mode": "hybrid"},
        {"retrieval_mode": "vector"},
        {"retrieval_mode": "hybrid_vector"},
        {"sources": ["news"]},
        {"categories": ["news"]},
        {"output_schema": {}},
    ],
)
def test_invalid_scope_rejected(extra):
    with pytest.raises(ValidationError):
        SearchRequest(query="paper", **({**BOUNDS, "engines": ["openalex"], **extra}))


@pytest.mark.parametrize(
    "bounds",
    [
        {"date_from": "2026-01-01"},
        {"date_to": "2026-01-01"},
        {"date_from": "2026-01-01", "date_to": "2026-01-01"},
    ],
)
def test_inclusive_and_one_sided(bounds):
    assert SearchRequest(query="paper", engines=["openalex"], **bounds)


@pytest.mark.asyncio
@pytest.mark.parametrize("cached", [False, True])
async def test_client_forwards_bounds_and_preserves_proof(cached):
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "results": [ROW],
                "meta": {
                    "date_filter": PROOF,
                    "cache_hit": cached,
                    "engine_status": {"openalex": {"status": "ok"}},
                    "partial": False,
                    "deadline_exceeded": False,
                },
            },
        )

    client = SearXNGClient("https://search.example.test")
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    try:
        rows, health = await client.search("paper", engines=["openalex"], **BOUNDS)
        assert health.date_filter == PROOF
        assert rows[0]["publishedDate"] == BOUNDS["date_from"]
        assert dict(calls[0].url.params)["engines"] == "openalex"
        for key, value in BOUNDS.items():
            assert calls[0].url.params[key] == value
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation", ["missing", "mismatch", "partial", "engine", "timeout", "row_date"]
)
async def test_client_rejects_unproven_success(mutation):
    proof = deepcopy(PROOF)
    row = dict(ROW)
    if mutation == "mismatch":
        proof["applied"]["date_to"] = "2026-02-01"
    if mutation == "partial":
        proof["status"] = "partially_enforced"
    if mutation == "engine":
        proof["engines"] = ["brave"]
    if mutation == "row_date":
        row["publishedDate"] = "2025-12-31"

    def respond(request):
        if mutation == "timeout":
            raise httpx.ReadTimeout("timeout", request=request)
        return httpx.Response(
            200,
            json={
                "results": [row],
                "meta": {}
                if mutation == "missing"
                else {
                    "date_filter": proof,
                    "engine_status": {"openalex": {"status": "ok"}},
                    "partial": False,
                    "deadline_exceeded": False,
                },
            },
        )

    client = SearXNGClient("https://search.example.test")
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    try:
        with pytest.raises(UpstreamError):
            await client.search("paper", engines=["openalex"], **BOUNDS)
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("route", [search, search_v1])
async def test_both_routes_forward_and_return_proof(monkeypatch, route):
    client = SimpleNamespace(
        search=AsyncMock(
            return_value=(
                [ROW],
                SimpleNamespace(date_filter=PROOF, engines_responding=1),
            )
        ),
        close=AsyncMock(),
    )
    monkeypatch.setattr("agent.searxng_client.SearXNGClient", lambda _: client)
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(searxng_url="https://search.example.test")
        )
    )
    result = await route(
        request, SearchRequest(query="paper", engines=["openalex"], **BOUNDS)
    )
    data = result if isinstance(result, dict) else result.model_dump()
    assert data["date_filter"] == PROOF
    rows = data["data"] if route is search_v1 else data["data"]["web"]
    assert rows[0]["publishedDate"] == BOUNDS["date_from"]
    assert client.search.call_args.kwargs["date_from"] == BOUNDS["date_from"]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["timeout", "error", None, "ok"])
async def test_empty_results_require_completed_acquisition(status):
    meta = {"date_filter": PROOF, "partial": False, "deadline_exceeded": False}
    if status is not None:
        meta["engine_status"] = {"openalex": {"status": status}}
    client = SearXNGClient("https://search.example.test")
    await client._client.aclose()
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"results": [], "meta": meta})
        )
    )
    try:
        if status == "ok":
            rows, health = await client.search("paper", engines=["openalex"], **BOUNDS)
            assert rows == [] and health.date_filter == PROOF
        else:
            with pytest.raises(UpstreamError):
                await client.search("paper", engines=["openalex"], **BOUNDS)
    finally:
        await client.close()


def test_unrelated_legacy_extras_remain_ignored():
    assert SearchRequest(query="paper", candidate="legacy", timeout=5).query == "paper"


@pytest.mark.asyncio
async def test_successful_empty_route_does_not_claim_backend_failure(monkeypatch):
    client = SimpleNamespace(
        search=AsyncMock(
            return_value=([], SimpleNamespace(date_filter=PROOF, engines_responding=0))
        ),
        close=AsyncMock(),
    )
    monkeypatch.setattr("agent.searxng_client.SearXNGClient", lambda _: client)
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(searxng_url="https://search.example.test")
        )
    )
    result = await search(
        request, SearchRequest(query="paper", engines=["openalex"], **BOUNDS)
    )
    assert result.warning is None and result.date_filter == PROOF
