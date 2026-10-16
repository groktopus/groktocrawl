"""Route-level tests for find-similar error propagation (issue #588).

Exercises the real route wiring — ``agent.routes.router`` with the real
``groktocrawl_error_handler`` registered for ``GroktoCrawlError``, the same
minimal harness used by ``tests/service/test_rate_limit_contract.py`` — to
prove that a raised :class:`SemanticError` renders as HTTP 502 with the
standard ``ErrorResponse`` body and never as a success-shaped payload.

Also pins the healthy-path contracts: a successful vector search still
returns mapped results, a genuinely empty index still yields
``success:true, data:[]``, and the OpenAPI success schema for
``POST /v2/find-similar`` is unchanged.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from agent.app import groktocrawl_error_handler
from agent.exceptions import GroktoCrawlError, SemanticError
from agent.routes import router
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _build_app() -> FastAPI:
    """Minimal real harness mirroring create_app's error-handler wiring."""
    app = FastAPI()
    app.state.rate_limiter = MagicMock()
    app.state.job_store = MagicMock()
    app.state.max_searches_per_request = 5
    app.state.task_tracker = MagicMock()
    app.state.llm_base_url = "http://llm.test/v1"
    app.state.llm_api_key = ""
    app.state.llm_model = "test-model"
    app.state.searxng_url = "http://searxng.test"
    app.state.scraper_url = "http://scraper.test"
    app.state.semantic_url = "http://semantic.test"
    app.state.research_memory = MagicMock()
    app.add_exception_handler(GroktoCrawlError, groktocrawl_error_handler)
    app.include_router(router)
    return app


@pytest.fixture
def client():
    return TestClient(_build_app())


def _patched_research(search_vector):
    """Patch the research-layer SemanticClient used by run_find_similar."""

    class _FakeSemantic:
        def __init__(self, base_url=""):
            pass

        async def search_vector(self, query, limit=5):
            return await search_vector(query, limit)

        async def embed(self, texts):
            return [[0.0] * 3 for _ in texts]

        async def close(self):
            pass

    class _FakeScraper:
        def __init__(self, base_url=""):
            pass

        async def scrape(self, url):
            return {
                "success": True,
                "data": {"markdown": "# Herbs", "metadata": {"title": "Herbs"}},
            }

        async def close(self):
            pass

    class _FakeSearx:
        def __init__(self, base_url=""):
            pass

        async def search(self, query, limit=5):
            return [], {}

        async def close(self):
            pass

    return (
        patch("agent.research.similar.ScraperClient", _FakeScraper),
        patch("agent.semantic_client.SemanticClient", _FakeSemantic),
        patch("agent.research.similar.SearXNGClient", _FakeSearx),
    )


class TestFindSimilarSemanticErrorContract:
    def test_semantic_error_renders_502_with_standard_error_body(self, client):
        """A raised SemanticError surfaces as 502 + ErrorResponse shape."""
        patches = _patched_research(
            AsyncMock(side_effect=SemanticError("semantic-svc vector search failed"))
        )
        with patches[0], patches[1], patches[2]:
            resp = client.post(
                "/v2/find-similar",
                json={"url": "https://example.com/herbs"},
            )

        assert resp.status_code == 502
        body = resp.json()
        assert body["success"] is False
        assert body["error"] == "semantic-svc vector search failed"
        assert body["error_code"] == "SEMANTIC_SERVICE_ERROR"
        # No success-model keys may leak into the error body.
        for key in ("query_url", "search_mode", "latency_ms", "data"):
            assert key not in body

    def test_semantic_error_propagates_through_route_without_local_handling(
        self, client
    ):
        """The route has no local try/except; the handler does the rendering.

        A GroktoCrawlError raised inside run_find_similar must reach the
        registered handler unchanged (status/error_code preserved).
        """
        err = SemanticError("upstream down")
        patches = _patched_research(AsyncMock(side_effect=err))
        with patches[0], patches[1], patches[2]:
            resp = client.post(
                "/v2/find-similar",
                json={"url": "https://example.com/herbs", "limit": 3},
            )

        assert resp.status_code == err.status_code == 502
        assert resp.json()["error_code"] == "SEMANTIC_SERVICE_ERROR"


class TestFindSimilarHealthyContracts:
    def test_success_still_returns_mapped_results(self, client):
        """A healthy vector search keeps returning mapped results."""

        async def _ok(query, limit):
            assert limit == 10  # FindSimilarRequest default
            return [
                {"url": "https://a.com", "title": "A", "content": "c" * 250},
                {"url": "https://b.com", "title": "B"},
            ]

        patches = _patched_research(_ok)
        with patches[0], patches[1], patches[2]:
            resp = client.post(
                "/v2/find-similar",
                json={"url": "https://example.com/herbs"},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["query_url"] == "https://example.com/herbs"
        assert body["search_mode"] == "qdrant"
        assert isinstance(body["latency_ms"], float)
        assert len(body["data"]) == 2
        first, second = body["data"]
        assert first["url"] == "https://a.com"
        assert first["description"] == "c" * 200  # truncated at 200 chars
        assert second["description"] == "# Herbs"  # hydrated from the live page
        assert second["metadata_complete"] is True

    def test_genuinely_empty_index_returns_success_with_empty_data(self, client):
        """A clean empty result is success, never an error (issue #588)."""

        async def _empty(query, limit):
            return []

        patches = _patched_research(_empty)
        with patches[0], patches[1], patches[2]:
            resp = client.post(
                "/v2/find-similar",
                json={"url": "https://example.com/unindexed"},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"] == []
        assert "error_code" not in body


class TestFindSimilarOpenApiSchema:
    def test_openapi_success_schema_is_find_similar_response(self):
        """No schema drift: success response stays FindSimilarResponse."""
        spec = _build_app().openapi()
        operation = spec["paths"]["/v2/find-similar"]["post"]
        declared = {
            int(code): models for code, models in operation["responses"].items()
        }
        assert 200 in declared
        success_ref = declared[200]["content"]["application/json"]["schema"][
            "$ref"
        ].split("/")[-1]
        assert success_ref == "FindSimilarResponse"


# ─────────────────────────────────────────────────────────────────────────────
# Web-mode happy path (promoted from validator scratch coverage, #588
# follow-up). The #588 fix was scoped to qdrant mode; this pins the web mode's
# unchanged rerank contract (search → embed → cosine rerank) so it is guarded
# by the permanent suite rather than a throwaway validation test.
# ─────────────────────────────────────────────────────────────────────────────


class TestFindSimilarWebModeRerank:
    def test_web_mode_healthy_path_returns_reranked_results(self):
        """VAL-FIND-016: web mode reranks searxng results by cosine
        similarity against the scraped page and maps them to
        url/title/description dicts — no error raised on the healthy path."""
        from agent.research.similar import _run_find_similar_web

        # Query embedding for the scraped page's content.
        query_vec = [1.0, 0.0]

        # Candidate embeddings chosen so cosine similarity against [1, 0] is
        # deterministic: Alpha -> 1.0, Gamma -> ~0.707, Beta -> 0.0.
        cand_vecs = {
            "https://b.example.com/beta": [0.0, 1.0],
            "https://c.example.com/gamma": [
                0.7071067811865476,
                0.7071067811865476,
            ],
            "https://a.example.com/alpha": [1.0, 0.0],
        }

        class _FakeScraper:
            async def scrape(self, url):
                return {
                    "success": True,
                    "data": {
                        "markdown": "# Herb Gardens\n\nHydroponic herb gardening tips.",
                        "metadata": {"title": "Herb Gardens"},
                    },
                }

            async def close(self):
                return None

        class _FakeSearx:
            """Healthy SearXNG returning three results in a deliberately
            UNRANKED order (Beta, Gamma, Alpha) so passing requires actual
            cosine reranking."""

            async def search(self, query, limit=5):
                results = [
                    {
                        "url": "https://b.example.com/beta",
                        "title": "Beta",
                        "description": "beta description",
                    },
                    {
                        "url": "https://c.example.com/gamma",
                        "title": "Gamma",
                        "description": "gamma description",
                    },
                    {
                        "url": "https://a.example.com/alpha",
                        "title": "Alpha",
                        "description": "alpha description",
                    },
                ]
                return results[:limit], {}

            async def close(self):
                return None

        class _FakeSemantic:
            def __init__(self):
                self.embed_calls: list[list[str]] = []

            async def embed(self, texts):
                self.embed_calls.append(list(texts))
                if len(texts) == 1:
                    # Single-text call: the query URL's markdown embedding.
                    return [query_vec]
                # Batch call: one embedding per candidate description.
                vecs = []
                for text in texts:
                    for url_hint, vec in cand_vecs.items():
                        marker = url_hint.split("/")[-1]
                        if marker in text.lower():
                            vecs.append(vec)
                            break
                    else:
                        raise AssertionError(f"unexpected embed text: {text!r}")
                return vecs

            async def close(self):
                return None

        scraper = _FakeScraper()
        semantic = _FakeSemantic()
        searx = _FakeSearx()

        with (
            patch("agent.research.similar.ScraperClient", return_value=scraper),
            patch("agent.semantic_client.SemanticClient", return_value=semantic),
            patch("agent.research.similar.SearXNGClient", return_value=searx),
        ):
            # Must not raise; the #588 fix touched only the qdrant path.
            results = asyncio.run(
                _run_find_similar_web(
                    url="https://example.com/herbs",
                    limit=2,
                    scraper_url="http://scraper.test",
                    semantic_url="http://semantic.test",
                    searxng_url="http://searxng.test",
                )
            )

        # Non-empty reranked results, truncated to limit.
        assert isinstance(results, list)
        assert len(results) == 2

        # Rerank actually reordered: Alpha (cos 1.0) beats Gamma (~0.707);
        # raw searxng order was Beta, Gamma, Alpha — Beta must be dropped.
        assert [r["title"] for r in results] == ["Alpha", "Gamma"]

        # Result semantics expose quality and provenance, not just transport success.
        for item in results:
            assert item["url"].startswith("https://")
            assert item["description"].endswith("description")
            assert 0.0 <= item["score"] <= 1.0
            assert item["confidence"] in {"low", "medium", "high"}
            assert item["metadata_complete"] is True
            assert item["provenance"]["source"] == "web_search"
        assert results[0]["raw_rank"] == 3
        assert results[0]["rank"] == 1

        # Full pipeline ran: scrape -> search -> embed(query) -> embed(batch).
        assert len(semantic.embed_calls) == 2
        assert len(semantic.embed_calls[0]) == 1  # query markdown
        assert len(semantic.embed_calls[1]) == 3  # candidate descriptions

    def test_web_query_skips_leading_browser_boilerplate(self):
        from agent.research.similar import _web_query

        query = _web_query(
            "Python Success Stories",
            "JavaScript is disabled. Enable JavaScript to continue.\n\n"
            "Organizations describe how Python improved scientific computing.",
        )

        assert query.startswith("Python Success Stories")
        assert "scientific computing" in query
        assert "JavaScript" not in query

    def test_sparse_vector_title_falls_back_to_heading_then_url(self):
        from agent.research.similar import _fallback_title

        assert (
            _fallback_title("https://docs.example/guide", "# Useful Guide\n\nBody")
            == "Useful Guide"
        )
        assert (
            _fallback_title("https://docs.example/guides/setup", "plain body")
            == "docs.example — guides / setup"
        )
