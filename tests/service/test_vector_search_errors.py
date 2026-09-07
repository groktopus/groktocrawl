"""Public vector-search failure contracts, including an already-open SSE stream."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from agent.app import create_app
from agent.auth import verify_api_key
from agent.models import SearchRequest
from agent.research.search import run_search_stream
from agent.routes.search import search
from agent.searxng_client import SearchHealth
from fastapi.testclient import TestClient

VECTOR_RESULT = {"url": "https://example.test/vector", "title": "Vector", "score": 0.9}
WEB_RESULT = {
    "url": "https://example.test/web",
    "title": "Web",
    "description": "Web description",
}


@pytest.fixture
def harness(monkeypatch):
    app = create_app()
    app.dependency_overrides[verify_api_key] = lambda: None
    app.state.llm_model = "fixture"
    semantic = SimpleNamespace(
        search_vector=AsyncMock(return_value=[VECTOR_RESULT]), close=AsyncMock()
    )
    searxng = SimpleNamespace(
        search=AsyncMock(return_value=([WEB_RESULT], SearchHealth())), close=AsyncMock()
    )
    scraper = SimpleNamespace(close=AsyncMock())
    llm = SimpleNamespace(close=AsyncMock())
    monkeypatch.setattr(
        "agent.semantic_client.SemanticClient", lambda *_a, **_k: semantic
    )
    monkeypatch.setattr("agent.searxng_client.SearXNGClient", lambda *_a, **_k: searxng)
    monkeypatch.setattr(
        "agent.research.search.SearXNGClient", lambda *_a, **_k: searxng
    )
    monkeypatch.setattr(
        "agent.research.search.ScraperClient", lambda *_a, **_k: scraper
    )
    monkeypatch.setattr("agent.research.search.LLMClient", lambda *_a, **_k: llm)
    # No lifespan context: background maintenance must not start in route tests.
    return SimpleNamespace(
        client=TestClient(app),
        app=app,
        semantic=semantic,
        searxng=searxng,
        scraper=scraper,
        llm=llm,
    )


def _upstream_failure(kind):
    request = httpx.Request(
        "POST", "http://private-user:private-key@qdrant.internal/search"
    )
    if isinstance(kind, int):
        response = httpx.Response(
            kind,
            request=request,
            json={"detail": "Qdrant at private-host:6333 timed out"},
        )
        return httpx.HTTPStatusError(
            "private backend diagnostic", request=request, response=response
        )
    return kind("private backend diagnostic", request=request)


def _events(response):
    data = [
        line.removeprefix("data: ")
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    assert data[-1] == "[DONE]"
    return [json.loads(item) for item in data[:-1]]


@pytest.mark.parametrize(
    "failure", [503, 500, 401, httpx.ConnectError, httpx.ReadTimeout]
)
@pytest.mark.parametrize("stream", [False, True])
def test_vector_unavailable_is_sanitized_and_closes_clients(harness, failure, stream):
    harness.semantic.search_vector.side_effect = _upstream_failure(failure)
    response = harness.client.post(
        "/v2/search",
        json={"query": "fixture", "retrieval_mode": "vector", "stream": stream},
    )
    if stream:
        assert response.status_code == 200  # SSE headers precede retrieval.
        assert _events(response) == [
            {"type": "error", "content": "Semantic service is unavailable"}
        ]
        harness.scraper.close.assert_awaited_once()
        harness.llm.close.assert_awaited_once()
    else:
        assert response.status_code == 503
        assert response.json()["success"] is False
        assert response.json()["error"] == "Semantic service is unavailable"
        assert "data" not in response.json()
    for secret in ("private", "qdrant", "6333"):
        assert secret not in response.text.lower()
    harness.semantic.close.assert_awaited_once()
    harness.searxng.close.assert_awaited_once()
    harness.searxng.search.assert_not_awaited()


@pytest.mark.parametrize("mode", ["vector", "hybrid_vector"])
@pytest.mark.parametrize("stream", [False, True])
def test_successful_vector_and_hybrid_contract(harness, mode, stream):
    response = harness.client.post(
        "/v2/search",
        json={"query": "fixture", "retrieval_mode": mode, "limit": 5, "stream": stream},
    )
    assert response.status_code == 200
    if stream:
        events = _events(response)
        assert events[-1]["type"] == "done"
        rows = [event["result"] for event in events if event["type"] == "search_result"]
        assert events[-1]["total_results"] == len(rows)
        assert not any(event["type"] == "error" for event in events)
    else:
        body = response.json()
        assert body["success"] is True
        assert body["data"]["images"] == body["data"]["news"] == []
        rows = body["data"]["web"]
    expected_urls = {VECTOR_RESULT["url"]}
    if mode == "hybrid_vector":
        expected_urls.add(WEB_RESULT["url"])
    assert {row["url"] for row in rows} == expected_urls
    vector = next(row for row in rows if row["url"] == VECTOR_RESULT["url"])
    assert vector["title"] == "Vector"
    assert vector["description"] == ""
    harness.semantic.close.assert_awaited_once()


@pytest.mark.parametrize("stream", [False, True])
def test_empty_vector_index_remains_successful(harness, stream):
    harness.semantic.search_vector.return_value = []
    response = harness.client.post(
        "/v2/search",
        json={"query": "fixture", "retrieval_mode": "vector", "stream": stream},
    )
    assert response.status_code == 200
    if stream:
        events = _events(response)
        assert len(events) == 1 and events[0]["type"] == "done"
        assert events[0]["total_results"] == 0
    else:
        assert response.json()["success"] is True
        assert response.json()["data"] == {"web": [], "images": [], "news": []}


@pytest.mark.parametrize("stream", [False, True])
def test_hybrid_retains_web_fallback(harness, stream):
    harness.semantic.search_vector.side_effect = _upstream_failure(503)
    response = harness.client.post(
        "/v2/search",
        json={"query": "fixture", "retrieval_mode": "hybrid_vector", "stream": stream},
    )
    assert response.status_code == 200
    if stream:
        events = _events(response)
        assert events[-1]["type"] == "done"
        rows = [event["result"] for event in events if event["type"] == "search_result"]
    else:
        assert response.json()["success"] is True
        rows = response.json()["data"]["web"]
    assert [row["url"] for row in rows] == [WEB_RESULT["url"]]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [asyncio.CancelledError, ValueError])
@pytest.mark.parametrize("stream", [False, True])
async def test_unrelated_errors_and_cancellation_propagate(harness, failure, stream):
    harness.semantic.search_vector.side_effect = failure("fixture")
    with pytest.raises(failure):
        if stream:
            async for _ in run_search_stream(
                "fixture", retrieval_mode="vector", llm_model="fixture"
            ):
                pytest.fail(
                    "An unexpected failure must not emit a success or availability event"
                )
        else:
            await search(
                SimpleNamespace(app=harness.app),
                SearchRequest(query="fixture", retrieval_mode="vector"),
            )
    harness.semantic.close.assert_awaited_once()
    harness.searxng.close.assert_awaited_once()
