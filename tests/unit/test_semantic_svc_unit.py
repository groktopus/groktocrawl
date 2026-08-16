"""Unit tests for semantic-svc — models, retention, domain classification, migration.

Tests pure logic functions without requiring Qdrant, sentence-transformers,
or any running services. Uses mocking to handle circular imports.
"""

import datetime
import json
import sys
import time
import types

import pytest

# ── Import setup: mock router modules to avoid circular imports ──


def _import_app_helpers():
    """Import app.py helper functions with router circular-import workaround.

    app.py imports router modules at the bottom which create circular
    imports with retention.py. We mock those modules before importing app.
    """
    import fastapi
    from fastapi import APIRouter

    for mod_name in ["router_index", "router_migration", "router_search"]:
        mod = types.ModuleType(mod_name)
        setattr(mod, mod_name, APIRouter())
        sys.modules[mod_name] = mod

    # Prevent FastAPI.include_router assertion errors with mock routers
    original_include = fastapi.applications.FastAPI.include_router
    fastapi.applications.FastAPI.include_router = lambda self, router, **kwargs: None

    from app import (
        _get_active_model,
        _named_vector_name,
        _now_iso,
        _set_active_override,
        _url_hash,
    )

    fastapi.applications.FastAPI.include_router = original_include

    return (
        _url_hash,
        _named_vector_name,
        _now_iso,
        _get_active_model,
        _set_active_override,
    )


_url_hash, _named_vector_name, _now_iso, _get_active_model, _set_active_override = (
    _import_app_helpers()
)


def _import_retention():
    """Import retention functions after app is loaded."""
    from retention import _compute_domain_category, _compute_retention_score

    return _compute_domain_category, _compute_retention_score


_compute_domain_category, _compute_retention_score = _import_retention()


def _import_build_payload():
    """Import _build_index_payload after breaking circular imports.

    After app.py is loaded (via mocked router modules), we remove the
    mocks and load the real router_index module. Since retention and app
    are already cached in sys.modules, the real router_index can import
    from retention without circular import issues.
    """
    # Remove the mock modules so the real ones get imported
    for mod_name in ["router_index", "router_migration", "router_search"]:
        if mod_name in sys.modules:
            del sys.modules[mod_name]

    from router_index import _build_index_payload

    return _build_index_payload


_build_index_payload = _import_build_payload()


# ── Tests: health readiness ─────────────────────────────────────────


class TestHealthReadiness:
    """Semantic readiness requires both models and Qdrant."""

    @pytest.mark.asyncio
    async def test_health_is_ok_when_models_and_qdrant_are_ready(self, monkeypatch):
        from unittest.mock import MagicMock

        import app

        qdrant = MagicMock()
        monkeypatch.setattr(app, "_models_ready", True)
        monkeypatch.setattr(app, "_qdrant", qdrant)

        response = await app.health()

        assert response == {"status": "ok", "models": "loaded", "qdrant": "ready"}
        qdrant.get_collections.assert_called_once_with()

    @pytest.mark.asyncio
    async def test_health_is_not_ok_when_qdrant_is_unavailable(self, monkeypatch):
        from unittest.mock import MagicMock

        import app

        qdrant = MagicMock()
        qdrant.get_collections.side_effect = ConnectionError("Connection refused")
        monkeypatch.setattr(app, "_models_ready", True)
        monkeypatch.setattr(app, "_qdrant", qdrant)

        response = await app.health()

        assert response == {
            "status": "starting",
            "models": "loaded",
            "qdrant": "unavailable",
        }

    def test_temporary_qdrant_readiness_client_is_closed(self, monkeypatch):
        from unittest.mock import MagicMock

        import app

        temporary_client = MagicMock()
        monkeypatch.setattr(app, "_qdrant", None)
        monkeypatch.setattr(
            app, "QdrantClient", MagicMock(return_value=temporary_client)
        )

        assert app._is_qdrant_ready() is True
        temporary_client.get_collections.assert_called_once_with()
        temporary_client.close.assert_called_once_with()


# ── Tests: _url_hash ──────────────────────────────────────────────


class TestUrlHash:
    """Deterministic URL hashing."""

    def test_same_url_same_hash(self):
        h1 = _url_hash("https://example.com/page")
        h2 = _url_hash("https://example.com/page")
        assert h1 == h2

    def test_different_urls_different_hash(self):
        h1 = _url_hash("https://example.com/page-a")
        h2 = _url_hash("https://example.com/page-b")
        assert h1 != h2

    def test_url_hash_is_positive_int(self):
        h = _url_hash("https://example.com")
        assert isinstance(h, int)
        assert h > 0

    def test_url_hash_trailing_slash_matters(self):
        h1 = _url_hash("https://example.com/page")
        h2 = _url_hash("https://example.com/page/")
        assert h1 != h2

    def test_url_hash_case_sensitive(self):
        h1 = _url_hash("https://Example.com/Page")
        h2 = _url_hash("https://example.com/page")
        assert h1 != h2


# ── Tests: _named_vector_name ──────────────────────────────────────


class TestNamedVectorName:
    """Named vector name formatting from model names."""

    def test_simple_model_name(self):
        assert _named_vector_name("BAAI/bge-m3") == "v_bge-m3"

    def test_reranker_model(self):
        assert _named_vector_name("BAAI/bge-reranker-v2-m3") == "v_bge-reranker-v2-m3"

    def test_model_with_dots(self):
        assert (
            _named_vector_name("sentence-transformers/all-MiniLM-L6-v2")
            == "v_all-minilm-l6-v2"
        )

    def test_model_with_special_chars(self):
        result = _named_vector_name("some-org/some_model@v1")
        assert result == "v_some-model-v1"


# ── Tests: _now_iso ────────────────────────────────────────────────


class TestNowIso:
    """ISO 8601 timestamp generation."""

    def test_returns_iso_format(self):
        iso = _now_iso()
        # ISO 8601 contains 'T' separator
        assert "T" in iso

    def test_parseable_datetime(self):
        iso = _now_iso()
        parsed = datetime.datetime.fromisoformat(iso)
        assert isinstance(parsed, datetime.datetime)

    def test_utc_timezone(self):
        iso = _now_iso()
        # Should have timezone info (UTC)
        parsed = datetime.datetime.fromisoformat(iso)
        assert parsed.tzinfo is not None

    def test_consecutive_calls_increase(self):
        t1 = _now_iso()
        time.sleep(0.001)
        t2 = _now_iso()
        assert t2 >= t1


# ── Tests: _get_active_model / _set_active_override ────────────────


class TestActiveModel:
    """Active model getter and override setter."""

    def test_default_active_model_is_env_var(self):
        # Default is from ACTIVE_EMBED_MODEL env var or 'bge-m3'
        model = _get_active_model()
        assert isinstance(model, str)
        assert len(model) > 0

    def test_set_override_changes_active_model(self):
        original = _get_active_model()
        _set_active_override("v_new-test-model")
        assert _get_active_model() == "v_new-test-model"
        # Reset
        _set_active_override(None)
        assert _get_active_model() == original

    def test_set_override_to_none_reverts(self):
        original = _get_active_model()
        _set_active_override("v_override")
        _set_active_override(None)
        assert _get_active_model() == original

    def test_override_persists_across_calls(self):
        _set_active_override("v_persist-test")
        assert _get_active_model() == "v_persist-test"
        assert _get_active_model() == "v_persist-test"
        _set_active_override(None)


# ── Tests: _compute_domain_category ────────────────────────────────


class TestDomainCategory:
    """Domain classification into 7 categories."""

    # ── docs category ──

    def test_docs_domain_readthedocs(self):
        assert (
            _compute_domain_category("https://docs.readthedocs.io/en/stable/") == "docs"
        )

    def test_docs_prefix(self):
        assert _compute_domain_category("https://docs.docker.com/get-started") == "docs"

    def test_docs_learn_prefix(self):
        assert (
            _compute_domain_category("https://learn.microsoft.com/en-us/python")
            == "docs"
        )

    def test_docs_help_prefix(self):
        assert _compute_domain_category("https://help.github.com/en") == "docs"

    # ── reference category ──

    def test_reference_wikipedia(self):
        assert (
            _compute_domain_category("https://en.wikipedia.org/wiki/Python")
            == "reference"
        )

    def test_reference_stackoverflow(self):
        assert (
            _compute_domain_category("https://stackoverflow.com/questions/123")
            == "reference"
        )

    def test_reference_github(self):
        assert _compute_domain_category("https://github.com/user/repo") == "reference"

    # ── news category ──

    def test_news_reuters(self):
        assert (
            _compute_domain_category("https://reuters.com/article/world-news") == "news"
        )

    def test_news_nytimes(self):
        assert (
            _compute_domain_category("https://nytimes.com/2024/01/01/article") == "news"
        )

    def test_news_bbc(self):
        assert _compute_domain_category("https://bbc.com/news/technology") == "news"

    def test_news_apnews(self):
        assert (
            _compute_domain_category("https://apnews.com/article/some-story") == "news"
        )

    def test_news_subdomain(self):
        assert (
            _compute_domain_category("https://www.reuters.com/article/test") == "news"
        )

    # ── social category ──

    def test_social_reddit(self):
        assert _compute_domain_category("https://reddit.com/r/python") == "social"

    def test_social_twitter(self):
        assert _compute_domain_category("https://twitter.com/user") == "social"

    def test_social_x(self):
        assert _compute_domain_category("https://x.com/user") == "social"

    def test_social_youtube(self):
        assert _compute_domain_category("https://youtube.com/watch?v=abc") == "social"

    # ── blog category ──

    def test_blog_medium(self):
        assert _compute_domain_category("https://medium.com/some-article") == "blog"

    def test_blog_substack(self):
        assert (
            _compute_domain_category("https://newsletter.substack.com/p/test") == "blog"
        )

    def test_blog_wordpress(self):
        assert (
            _compute_domain_category("https://techblog.wordpress.com/2024/01") == "blog"
        )

    def test_blog_prefix(self):
        assert _compute_domain_category("https://blog.example.com/article") == "blog"

    # ── api category ──

    def test_api_prefix(self):
        assert _compute_domain_category("https://api.github.com/repos/test") == "api"

    def test_api_developer_prefix(self):
        assert (
            _compute_domain_category("https://developer.apple.com/documentation")
            == "api"
        )

    # ── unknown category ──

    def test_unknown_domain(self):
        assert (
            _compute_domain_category("https://someobscuresite.example.com/page")
            == "unknown"
        )

    def test_unknown_no_netloc(self):
        assert _compute_domain_category("not-a-url") == "unknown"

    def test_unknown_empty(self):
        assert _compute_domain_category("") == "unknown"


# ── Tests: _compute_retention_score ────────────────────────────────


class TestRetentionScore:
    """Retention scoring: recency decay, domain multiplier, access/crawl boost."""

    def test_score_in_range(self):
        now = datetime.datetime.now(datetime.UTC)
        payload = {
            "domain_category": "unknown",
            "last_indexed_at": now.isoformat(),
            "access_count": 0,
            "crawl_count": 0,
        }
        score = _compute_retention_score(payload)
        assert 0.0 <= score <= 2.0

    def test_docs_scores_higher_than_news(self):
        now = datetime.datetime.now(datetime.UTC)
        docs_payload = {
            "domain_category": "docs",
            "last_indexed_at": now.isoformat(),
            "access_count": 0,
            "crawl_count": 0,
        }
        news_payload = {
            "domain_category": "news",
            "last_indexed_at": now.isoformat(),
            "access_count": 0,
            "crawl_count": 0,
        }
        assert _compute_retention_score(docs_payload) > _compute_retention_score(
            news_payload
        )

    def test_recency_decay_reduces_score(self):
        now = datetime.datetime.now(datetime.UTC)
        old = now - datetime.timedelta(days=365)
        recent_payload = {
            "domain_category": "reference",
            "last_indexed_at": now.isoformat(),
            "access_count": 0,
            "crawl_count": 0,
        }
        old_payload = {
            "domain_category": "reference",
            "last_indexed_at": old.isoformat(),
            "access_count": 0,
            "crawl_count": 0,
        }
        assert _compute_retention_score(old_payload) < _compute_retention_score(
            recent_payload
        )

    def test_access_boost_increases_score(self):
        now = datetime.datetime.now(datetime.UTC)
        low_access = {
            "domain_category": "docs",
            "last_indexed_at": now.isoformat(),
            "access_count": 0,
            "crawl_count": 0,
        }
        high_access = {
            "domain_category": "docs",
            "last_indexed_at": now.isoformat(),
            "access_count": 50,
            "crawl_count": 0,
        }
        assert _compute_retention_score(high_access) > _compute_retention_score(
            low_access
        )

    def test_crawl_boost_increases_score(self):
        now = datetime.datetime.now(datetime.UTC)
        low_crawl = {
            "domain_category": "docs",
            "last_indexed_at": now.isoformat(),
            "access_count": 0,
            "crawl_count": 0,
        }
        high_crawl = {
            "domain_category": "docs",
            "last_indexed_at": now.isoformat(),
            "access_count": 0,
            "crawl_count": 10,
        }
        assert _compute_retention_score(high_crawl) > _compute_retention_score(
            low_crawl
        )

    def test_no_date_returns_mid_recency(self):
        payload = {
            "domain_category": "unknown",
            "last_indexed_at": "",
            "access_count": 0,
            "crawl_count": 0,
        }
        score = _compute_retention_score(payload)
        assert 0.0 <= score <= 2.0

    def test_invalid_date_returns_mid_recency(self):
        payload = {
            "domain_category": "unknown",
            "last_indexed_at": "not-a-date",
            "access_count": 0,
            "crawl_count": 0,
        }
        score = _compute_retention_score(payload)
        assert 0.0 <= score <= 2.0

    def test_missing_category_defaults_to_unknown(self):
        now = datetime.datetime.now(datetime.UTC)
        payload = {
            "last_indexed_at": now.isoformat(),
            "access_count": 0,
            "crawl_count": 0,
        }
        score = _compute_retention_score(payload)
        assert 0.0 <= score <= 2.0

    def test_score_rounding(self):
        now = datetime.datetime.now(datetime.UTC)
        payload = {
            "domain_category": "docs",
            "last_indexed_at": now.isoformat(),
            "access_count": 0,
            "crawl_count": 0,
        }
        score = _compute_retention_score(payload)
        # Should be rounded to 4 decimal places
        score_str = str(score)
        if "." in score_str:
            decimal_places = len(score_str.split(".")[1])
            assert decimal_places <= 4, (
                f"Expected ≤4 decimal places, got {decimal_places}"
            )


# ── Tests: _build_index_payload ────────────────────────────────────


class TestBuildIndexPayload:
    """Index payload building for Qdrant."""

    def test_new_payload_has_all_required_keys(self):
        payload = _build_index_payload("https://example.com/page", "Test Page", None)
        required_keys = {
            "url",
            "title",
            "domain_category",
            "first_indexed_at",
            "last_indexed_at",
            "crawl_count",
            "access_count",
            "last_accessed_at",
            "embedding_model",
            "embedding_dim",
            "embedding_models",
            "retention_score",
        }
        assert required_keys.issubset(payload.keys()), (
            f"Missing keys: {required_keys - payload.keys()}"
        )

    def test_new_payload_crawl_count_is_one(self):
        payload = _build_index_payload("https://example.com/page", "Test Page", None)
        assert payload["crawl_count"] == 1

    def test_new_payload_access_count_is_zero(self):
        payload = _build_index_payload("https://example.com/page", "Test Page", None)
        assert payload["access_count"] == 0

    def test_new_payload_domain_category_detected(self):
        payload = _build_index_payload("https://reuters.com/article/test", "News", None)
        assert payload["domain_category"] == "news"

    def test_new_payload_embedding_models_list(self):
        payload = _build_index_payload("https://example.com/page", "Test", None)
        models_list = json.loads(payload["embedding_models"])
        assert isinstance(models_list, list)
        assert len(models_list) == 1

    def test_reindex_increments_crawl_count(self):
        existing = {
            "url": "https://example.com/page",
            "title": "Old Title",
            "domain_category": "unknown",
            "first_indexed_at": "2024-01-01T00:00:00+00:00",
            "last_indexed_at": "2024-01-01T00:00:00+00:00",
            "crawl_count": 3,
            "access_count": 5,
            "last_accessed_at": "2024-06-01T00:00:00+00:00",
            "embedding_model": "BAAI/bge-m3",
            "embedding_dim": 1024,
            "embedding_models": json.dumps(["v_bge-m3"]),
        }
        payload = _build_index_payload(
            "https://example.com/page", "New Title", existing
        )
        assert payload["crawl_count"] == 4

    def test_reindex_preserves_first_indexed_at(self):
        existing = {
            "first_indexed_at": "2024-01-01T00:00:00+00:00",
            "crawl_count": 1,
            "access_count": 0,
            "last_accessed_at": "",
            "embedding_model": "BAAI/bge-m3",
            "embedding_dim": 1024,
            "embedding_models": json.dumps(["v_bge-m3"]),
        }
        payload = _build_index_payload("https://example.com/page", "Test", existing)
        assert payload["first_indexed_at"] == "2024-01-01T00:00:00+00:00"

    def test_reindex_preserves_access_count(self):
        existing = {
            "access_count": 42,
            "crawl_count": 1,
            "first_indexed_at": "2024-01-01T00:00:00+00:00",
            "last_accessed_at": "2024-06-01T00:00:00+00:00",
            "embedding_model": "BAAI/bge-m3",
            "embedding_dim": 1024,
            "embedding_models": json.dumps(["v_bge-m3"]),
        }
        payload = _build_index_payload("https://example.com/page", "Test", existing)
        assert payload["access_count"] == 42

    def test_retention_score_computed_in_payload(self):
        payload = _build_index_payload("https://docs.example.com/guide", "Guide", None)
        assert isinstance(payload["retention_score"], float)
        assert payload["retention_score"] > 0

    def test_payload_title_preserved(self):
        payload = _build_index_payload(
            "https://example.com/page", "My Custom Title", None
        )
        assert payload["title"] == "My Custom Title"


# ── Tests: Model schemas ───────────────────────────────────────────


class TestModelSchemas:
    """Pydantic request/response schema validation."""

    def test_embed_request(self):
        from models import EmbedRequest

        req = EmbedRequest(input=["hello", "world"])
        assert req.model == "BGE-M3"  # default
        assert req.input == ["hello", "world"]

    def test_embed_response(self):
        from models import EmbedResponse

        resp = EmbedResponse(embeddings=[[0.1, 0.2], [0.3, 0.4]])
        assert len(resp.embeddings) == 2

    def test_rerank_request_defaults(self):
        from models import RerankRequest

        req = RerankRequest(query="test", documents=["a", "b"])
        assert req.top_k == 5

    def test_rerank_result(self):
        from models import RerankResult

        r = RerankResult(index=0, score=0.95)
        assert r.index == 0
        assert r.score == 0.95

    def test_index_request(self):
        from models import IndexRequest

        req = IndexRequest(url="https://example.com", content="test content")
        assert req.title == ""  # default

    def test_index_batch_request_empty(self):
        from models import IndexBatchRequest

        req = IndexBatchRequest(pages=[])
        assert req.pages == []

    def test_vector_search_request_defaults(self):
        from models import VectorSearchRequest

        req = VectorSearchRequest(query="test")
        assert req.limit == 5

    def test_vector_search_result(self):
        from models import VectorSearchResult

        r = VectorSearchResult(url="https://example.com", title="Test", score=0.9)
        assert r.score == 0.9

    def test_model_info_response_has_migration(self):
        from models import ModelInfoResponse

        resp = ModelInfoResponse(
            current_model="BAAI/bge-m3",
            current_dim=1024,
            active_named_vector="v_bge-m3",
            collection="groktocrawl_pages",
            total_docs=0,
            max_docs=250000,
            migration={
                "status": "idle",
                "source_model": "",
                "target_model": "",
                "docs_processed": 0,
                "docs_total": 0,
            },
        )
        assert "status" in resp.migration

    def test_migration_status_response(self):
        from models import MigrationStatusResponse

        resp = MigrationStatusResponse(
            status="backfilling",
            source_model="BAAI/bge-m3",
            source_dim=1024,
            target_model="BAAI/bge-m4",
            target_dim=2048,
            docs_processed=100,
            docs_total=1000,
            started_at="2024-01-01T00:00:00+00:00",
            completed_at="",
        )
        assert resp.status == "backfilling"
        assert resp.docs_processed == 100


# ── Tests: Migration state transitions ──────────────────────────────


class TestMigrationStates:
    """Migration lifecycle state transitions."""

    def test_migration_initial_state_is_idle(self):
        from app import _migration

        assert _migration["status"] == "idle"

    def test_migration_state_is_mutable(self):
        from app import _migration

        _migration["status"] = "backfilling"
        assert _migration["status"] == "backfilling"
        _migration["status"] = "idle"  # reset

    def test_migration_has_required_state_keys(self):
        from app import _migration

        required = {
            "status",
            "source_model",
            "target_model",
            "docs_processed",
            "docs_total",
            "started_at",
            "completed_at",
        }
        assert required.issubset(_migration.keys())

    def test_migration_has_docs_tracking(self):
        from app import _migration

        _migration["docs_processed"] = 500
        _migration["docs_total"] = 1000
        assert _migration["docs_processed"] == 500
        _migration["docs_processed"] = 0
        _migration["docs_total"] = 0

    def test_cutover_updates_active_model(self):
        from app import _migration

        original = _get_active_model()
        _migration["target_model"] = "BAAI/bge-m4"
        target_nv = _named_vector_name("BAAI/bge-m4")
        _set_active_override(target_nv)
        assert _get_active_model() == target_nv
        # Reset
        _set_active_override(None)
        assert _get_active_model() == original


# ── Tests: verify_api_key ──────────────────────────────────────────


class TestVerifyApiKey:
    """API key verification for migration endpoints (VAL-FIX-003)."""

    def _make_request(self, headers: dict | None = None, query: dict | None = None):
        """Create a mock FastAPI Request for testing verify_api_key."""
        from unittest.mock import MagicMock

        mock_request = MagicMock(spec=["headers", "query_params"])
        mock_request.headers = headers or {}
        mock_request.query_params = query or {}
        return mock_request

    @pytest.mark.asyncio
    async def test_no_api_key_configured_allows_all(self, monkeypatch):
        """When API_KEY is empty/unset, all requests are allowed."""
        monkeypatch.setattr("auth.API_KEY", "")
        from auth import verify_api_key

        req = self._make_request()
        # Should not raise
        await verify_api_key(req)

    @pytest.mark.asyncio
    async def test_returns_401_without_api_key(self, monkeypatch):
        """Migration endpoints return 401 without valid API key."""
        monkeypatch.setattr("auth.API_KEY", "test-secret-key")
        from auth import verify_api_key
        from fastapi import HTTPException

        req = self._make_request(headers={})
        with pytest.raises(HTTPException) as exc_info:
            await verify_api_key(req)
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_returns_401_with_wrong_x_api_key(self, monkeypatch):
        """Migration endpoints return 401 with wrong X-API-Key header."""
        monkeypatch.setattr("auth.API_KEY", "test-secret-key")
        from auth import verify_api_key
        from fastapi import HTTPException

        req = self._make_request(headers={"X-API-Key": "wrong-key"})
        with pytest.raises(HTTPException) as exc_info:
            await verify_api_key(req)
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_succeeds_with_valid_x_api_key(self, monkeypatch):
        """Migration endpoints succeed with valid X-API-Key header."""
        monkeypatch.setattr("auth.API_KEY", "test-secret-key")
        from auth import verify_api_key

        req = self._make_request(headers={"X-API-Key": "test-secret-key"})
        # Should not raise
        await verify_api_key(req)

    @pytest.mark.asyncio
    async def test_succeeds_with_valid_api_key_query_param(self, monkeypatch):
        """Migration endpoints succeed with valid api_key query parameter."""
        monkeypatch.setattr("auth.API_KEY", "test-secret-key")
        from auth import verify_api_key

        req = self._make_request(query={"api_key": "test-secret-key"})
        # Should not raise
        await verify_api_key(req)

    @pytest.mark.asyncio
    async def test_returns_401_with_wrong_api_key_query_param(self, monkeypatch):
        """Migration endpoints return 401 with wrong api_key query parameter."""
        monkeypatch.setattr("auth.API_KEY", "test-secret-key")
        from auth import verify_api_key
        from fastapi import HTTPException

        req = self._make_request(query={"api_key": "wrong-key"})
        with pytest.raises(HTTPException) as exc_info:
            await verify_api_key(req)
        assert exc_info.value.status_code == 401


# ── Tests: _get_target_embed_model ─────────────────────────────────


class TestTargetEmbedModelCache:
    """Target model caching for migration dual-write (fix-semantic-hardening).

    These tests verify the caching logic without loading actual
    SentenceTransformer models (too heavy for unit tests).
    """

    def test_cache_variables_exist(self):
        """_target_embed_model and _target_embed_model_name globals exist."""
        from app import _target_embed_model, _target_embed_model_name

        # These should exist as module-level variables
        assert _target_embed_model is None  # Initially None
        assert _target_embed_model_name == ""

    def test_target_embed_model_name_tracked(self):
        """The _target_embed_model_name tracks which model is cached."""
        from app import _target_embed_model_name

        # Initially empty string
        assert isinstance(_target_embed_model_name, str)


# ── Search vector: Qdrant failure boundary ───────────────────────


def _async_return(value):
    async def _wrap(*_args, **_kwargs):
        return value

    return _wrap


class TestSearchVectorQdrantBoundary:
    """POST /search/vector must convert a slow or failing Qdrant query
    into a structured 503 instead of an unhandled 500.

    Regression for the live 500 observed when the local Qdrant index is
    unhealthy: ``router_search.search_vector`` previously called the
    blocking ``qdrant.query_points`` with no timeout and no error boundary,
    so a slow/stuck index escaped as a generic internal error.
    """

    @staticmethod
    def _ready_router(monkeypatch, qdrant):
        import app as app_module
        import router_search

        class _FakeModel:
            def encode(self, text, normalize_embeddings=True):
                import numpy as np

                return np.array([[0.1, 0.2, 0.3]])

        monkeypatch.setattr(app_module, "_models_ready", True)
        monkeypatch.setattr(router_search, "_ensure_qdrant", _async_return(qdrant))
        monkeypatch.setattr(router_search, "_get_embed_model", lambda: _FakeModel())
        monkeypatch.setattr(router_search, "_get_active_model", lambda: "v_bge-m3")
        return router_search

    @pytest.mark.asyncio
    async def test_qdrant_timeout_returns_503(self, monkeypatch):
        """A Qdrant query that exceeds the bound returns 503, not 500."""

        from fastapi import HTTPException
        from models import VectorSearchRequest

        class _SlowQdrant:
            def query_points(self, **kwargs):
                raise TimeoutError("timed out")

        router_search = self._ready_router(monkeypatch, _SlowQdrant())
        with pytest.raises(HTTPException) as exc_info:
            await router_search.search_vector(
                VectorSearchRequest(query="herbs", limit=5)
            )
        assert exc_info.value.status_code == 503

    @pytest.mark.asyncio
    async def test_qdrant_failure_returns_503(self, monkeypatch):
        """A Qdrant connection/query error returns 503, not 500."""
        from fastapi import HTTPException
        from models import VectorSearchRequest

        class _BadQdrant:
            def query_points(self, **kwargs):
                raise RuntimeError("connection refused")

        router_search = self._ready_router(monkeypatch, _BadQdrant())
        with pytest.raises(HTTPException) as exc_info:
            await router_search.search_vector(
                VectorSearchRequest(query="herbs", limit=5)
            )
        assert exc_info.value.status_code == 503

    @pytest.mark.asyncio
    async def test_success_still_returns_results(self, monkeypatch):
        """A healthy Qdrant query still returns scored results unchanged."""
        from models import VectorSearchRequest, VectorSearchResponse

        class _Hit:
            def __init__(self, url, title, score):
                self.payload = {"url": url, "title": title}
                self.score = score

        class _OkQdrant:
            def query_points(self, **kwargs):
                class _Resp:
                    points = [
                        _Hit("https://a.com", "A", 0.91),
                        _Hit("https://b.com", "B", 0.82),
                    ]

                return _Resp()

        router_search = self._ready_router(monkeypatch, _OkQdrant())
        resp = await router_search.search_vector(
            VectorSearchRequest(query="herbs", limit=5)
        )
        assert isinstance(resp, VectorSearchResponse)
        assert [r.url for r in resp.results] == ["https://a.com", "https://b.com"]
        assert [r.score for r in resp.results] == [0.91, 0.82]
