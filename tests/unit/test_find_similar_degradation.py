"""Tests for graceful degradation of the find-similar qdrant path.

Covers ``agent-svc/agent/research/similar.py:_run_find_similar_qdrant``:
when semantic-svc fails for the vector search (503 index unavailable,
timeout on a slow backend, or connection error), find-similar should
degrade to an empty result instead of letting the error escape as a 500.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest
from agent.research.similar import _run_find_similar_qdrant


class _FakeScraper:
    def __init__(self):
        self.closed = False

    async def scrape(self, url):
        return {
            "success": True,
            "data": {
                "markdown": "# Herbs\nHydroponic herb gardening tips.",
                "metadata": {"title": "Herb Garden"},
            },
        }

    async def close(self):
        self.closed = True


class _FakeSemantic:
    def __init__(self, search_vector):
        self._search_vector = search_vector
        self.closed = False

    async def search_vector(self, query, limit=5):
        return await self._search_vector(query, limit)

    async def close(self):
        self.closed = True


def _mock_http_status_error(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "http://semantic-svc:8003/search/vector")
    response = httpx.Response(status, request=request)
    return httpx.HTTPStatusError("error", request=request, response=response)


@pytest.mark.asyncio
async def test_qdrant_503_returns_empty_results():
    """A 503 from semantic-svc degrades to no similar results, not a 500."""
    semantic = _FakeSemantic(AsyncMock(side_effect=_mock_http_status_error(503)))

    with (
        patch("agent.research.similar.ScraperClient", return_value=_FakeScraper()),
        patch("agent.semantic_client.SemanticClient", return_value=semantic),
    ):
        results = await _run_find_similar_qdrant(
            url="https://example.com/herbs",
            limit=5,
            scraper_url="http://scraper-svc:8001",
            semantic_url="http://semantic-svc:8003",
        )

    assert results == []


@pytest.mark.asyncio
async def test_qdrant_http_error_returns_empty():
    """A 500 from semantic-svc degrades to no similar results, not a 500."""
    semantic = _FakeSemantic(AsyncMock(side_effect=_mock_http_status_error(500)))

    with (
        patch("agent.research.similar.ScraperClient", return_value=_FakeScraper()),
        patch("agent.semantic_client.SemanticClient", return_value=semantic),
    ):
        results = await _run_find_similar_qdrant(
            url="https://example.com/herbs",
            limit=5,
            scraper_url="http://scraper-svc:8001",
            semantic_url="http://semantic-svc:8003",
        )

    assert results == []


@pytest.mark.asyncio
async def test_qdrant_timeout_returns_empty():
    """A timeout (slow backend) degrades to no similar results, not a 500."""
    request = httpx.Request("POST", "http://semantic-svc:8003/search/vector")
    semantic = _FakeSemantic(
        AsyncMock(side_effect=httpx.ReadTimeout("timed out", request=request))
    )

    with (
        patch("agent.research.similar.ScraperClient", return_value=_FakeScraper()),
        patch("agent.semantic_client.SemanticClient", return_value=semantic),
    ):
        results = await _run_find_similar_qdrant(
            url="https://example.com/herbs",
            limit=5,
            scraper_url="http://scraper-svc:8001",
            semantic_url="http://semantic-svc:8003",
        )

    assert results == []


@pytest.mark.asyncio
async def test_qdrant_success_returns_results():
    """A healthy vector search returns mapped results unchanged."""

    async def _ok(query, limit):
        return [{"url": "https://a.com", "title": "A", "content": "content A"}]

    semantic = _FakeSemantic(_ok)

    with (
        patch("agent.research.similar.ScraperClient", return_value=_FakeScraper()),
        patch("agent.semantic_client.SemanticClient", return_value=semantic),
    ):
        results = await _run_find_similar_qdrant(
            url="https://example.com/herbs",
            limit=5,
            scraper_url="http://scraper-svc:8001",
            semantic_url="http://semantic-svc:8003",
        )

    assert results == [
        {"url": "https://a.com", "title": "A", "description": "content A"}
    ]
