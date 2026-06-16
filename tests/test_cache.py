"""Unit tests for intelligent scrape cache (ADR-0019).

Tests the pure functions used by the freshness-aware cache revalidation
system. These tests do NOT require Valkey or Docker — they test only
the stateless utility functions in cache.py.
"""

import hashlib
import json
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scraper-svc"))

# Import the module under test
from scraper.cache import (
    _compute_content_hash,
    _enrich_cache_entry,
    _merge_cache_metadata,
    _normalize_url_for_cache,
    _parse_domain_ttls,
    _resolve_cache_ttl,
    _scrape_cache_key,
)

# ── Content hash tests ──────────────────────────────────────────


class TestComputeContentHash:
    def test_returns_sha256_hexdigest(self):
        text = "Hello, world!"
        expected = hashlib.sha256(text.encode("utf-8")).hexdigest()
        assert _compute_content_hash(text) == expected

    def test_deterministic_same_input(self):
        text = "The quick brown fox jumps over the lazy dog"
        assert _compute_content_hash(text) == _compute_content_hash(text)

    def test_different_input_different_hash(self):
        assert _compute_content_hash("abc") != _compute_content_hash("def")

    def test_empty_string(self):
        expected = hashlib.sha256(b"").hexdigest()
        assert _compute_content_hash("") == expected

    def test_unicode(self):
        text = "你好，世界！😊"
        assert _compute_content_hash(text) == _compute_content_hash(text)


# ── URL normalization tests ─────────────────────────────────────


class TestNormalizeUrlForCache:
    def test_lowercases_scheme_and_hostname_and_path(self):
        url = "HTTP://EXAMPLE.com/Path"
        result = _normalize_url_for_cache(url)
        assert result == "http://example.com/path"

    def test_strips_trailing_slash(self):
        url = "https://example.com/page/"
        result = _normalize_url_for_cache(url)
        assert result == "https://example.com/page"

    def test_preserves_root_slash(self):
        url = "https://example.com/"
        result = _normalize_url_for_cache(url)
        assert result == "https://example.com/"

    def test_path_without_slash(self):
        url = "https://example.com/page"
        result = _normalize_url_for_cache(url)
        assert result == "https://example.com/page"

    def test_sorts_query_parameters(self):
        url = "https://example.com/page?z=1&a=2&m=3"
        result = _normalize_url_for_cache(url)
        assert result == "https://example.com/page?a=2&m=3&z=1"

    def test_preserves_fragment(self):
        url = "https://example.com/page#section"
        result = _normalize_url_for_cache(url)
        assert result == "https://example.com/page#section"

    def test_deterministic(self):
        urls = [
            "https://example.com/page?a=1&b=2",
            "HTTPS://EXAMPLE.COM/Page/?B=2&A=1",
            "https://example.com/page/?b=2&a=1",
        ]
        results = [_normalize_url_for_cache(u) for u in urls]
        # All should normalize to the same key
        assert len(set(results)) == 1


class TestScrapeCacheKey:
    def test_uses_sha256_of_normalized_url(self):
        url = "https://example.com/page"
        normalized = _normalize_url_for_cache(url)
        expected_digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        assert _scrape_cache_key(url) == f"scrape_cache:{expected_digest}"

    def test_deterministic_for_same_url(self):
        assert _scrape_cache_key("https://example.com/a") == _scrape_cache_key(
            "HTTPS://EXAMPLE.COM/a/"
        )

    def test_different_for_different_urls(self):
        assert _scrape_cache_key("https://example.com/a") != _scrape_cache_key(
            "https://example.com/b"
        )


# ── Domain TTL parsing tests ────────────────────────────────────


class TestParseDomainTtls:
    def test_empty_env_var(self):
        with patch.dict(os.environ, {"SCRAPE_CACHE_DOMAIN_TTLS": ""}, clear=False):
            assert _parse_domain_ttls() == {}

    def test_default_empty(self):
        with patch.dict(os.environ, {}, clear=True):
            assert _parse_domain_ttls() == {}

    def test_valid_json(self):
        raw = '{"news.ycombinator.com": 300, "docs.python.org": 86400}'
        with patch.dict(os.environ, {"SCRAPE_CACHE_DOMAIN_TTLS": raw}, clear=False):
            result = _parse_domain_ttls()
            assert result == {"news.ycombinator.com": 300, "docs.python.org": 86400}

    def test_invalid_json_returns_empty(self):
        with patch.dict(
            os.environ, {"SCRAPE_CACHE_DOMAIN_TTLS": "not-json"}, clear=False
        ):
            assert _parse_domain_ttls() == {}


# ── Cache TTL resolution tests ──────────────────────────────────


class TestResolveCacheTtl:
    def test_default_ttl_when_no_domain_match(self):
        with patch.dict(os.environ, {"SCRAPE_CACHE_DOMAIN_TTLS": "{}"}, clear=False):
            ttl = _resolve_cache_ttl("https://example.com/page")
            assert ttl == 3600

    def test_matches_root_domain_exactly(self):
        raw = json.dumps({"example.com": 1800})
        with patch.dict(os.environ, {"SCRAPE_CACHE_DOMAIN_TTLS": raw}, clear=False):
            ttl = _resolve_cache_ttl("https://example.com/page")
            assert ttl == 1800

    def test_longest_suffix_match_wins(self):
        raw = json.dumps({"python.org": 7200, "docs.python.org": 86400})
        with patch.dict(os.environ, {"SCRAPE_CACHE_DOMAIN_TTLS": raw}, clear=False):
            ttl = _resolve_cache_ttl("https://docs.python.org/3/")
            assert ttl == 86400  # Longer match wins

    def test_subdomain_matches_parent(self):
        raw = json.dumps({"python.org": 7200})
        with patch.dict(os.environ, {"SCRAPE_CACHE_DOMAIN_TTLS": raw}, clear=False):
            ttl = _resolve_cache_ttl("https://docs.python.org/3/")
            assert ttl == 7200

    def test_applies_stability_multiplier(self):
        ttl = _resolve_cache_ttl("https://example.com", stability_multiplier=2.0)
        assert ttl == 7200  # 3600 * 2.0

    def test_clamps_to_min_ttl(self):
        with (
            patch("scraper.cache.SCRAPE_CACHE_TTL", 10),
            patch("scraper.cache.SCRAPE_CACHE_MIN_TTL", 60),
        ):
            ttl = _resolve_cache_ttl("https://example.com")
            assert ttl == 60  # Clamped from 10 to 60

    def test_clamps_to_max_ttl(self):
        with (
            patch("scraper.cache.SCRAPE_CACHE_TTL", 999999),
            patch("scraper.cache.SCRAPE_CACHE_MAX_TTL", 86400),
        ):
            ttl = _resolve_cache_ttl("https://example.com")
            assert ttl == 86400  # Clamped from 999999 to 86400

    def test_no_hostname_falls_back_to_default(self):
        ttl = _resolve_cache_ttl("invalid-url")
        assert ttl == 3600


# ── Cache metadata merging tests ────────────────────────────────


class TestMergeCacheMetadata:
    def test_preserves_and_increments_fetch_count(self):
        fresh = {
            "markdown": "new content",
            "source": "content-negotiation",
            "url": "https://example.com",
        }
        cached = {"fetch_count": 5, "first_fetched_at": 1000.0, "change_count": 2}
        result = _merge_cache_metadata(fresh, cached)
        assert result["fetch_count"] == 6  # Incremented
        assert result["first_fetched_at"] == 1000.0  # Preserved
        assert result["change_count"] == 2  # Preserved
        assert "last_checked_at" in result  # Added

    def test_handles_missing_fields(self):
        fresh = {"markdown": "new", "source": "test", "url": "https://example.com"}
        cached: dict = {}
        result = _merge_cache_metadata(fresh, cached)
        assert result["fetch_count"] == 1
        assert result["first_fetched_at"] > 0
        assert result["change_count"] == 0
        assert "last_checked_at" in result


# ── Cache entry enrichment tests ────────────────────────────────


class TestEnrichCacheEntry:
    def test_adds_content_hash(self):
        result = {
            "markdown": "hello world",
            "source": "llms.txt",
            "url": "https://example.com",
        }
        enriched = _enrich_cache_entry(result.copy(), "https://example.com")
        expected_hash = hashlib.sha256(b"hello world").hexdigest()
        assert enriched["content_hash"] == expected_hash

    def test_sets_source_tier_from_source(self):
        result = {
            "markdown": "hello",
            "source": "playwright",
            "url": "https://example.com",
        }
        enriched = _enrich_cache_entry(result.copy(), "https://example.com")
        assert enriched["source_tier"] == "playwright"

    def test_stores_etag_and_last_modified(self):
        result = {
            "markdown": "hello",
            "source": "llms.txt",
            "url": "https://example.com",
        }
        enriched = _enrich_cache_entry(
            result.copy(),
            "https://example.com",
            etag='"abc123"',
            last_modified="Mon, 01 Jan 2026 00:00:00 GMT",
        )
        assert enriched["etag"] == '"abc123"'
        assert enriched["last_modified"] == "Mon, 01 Jan 2026 00:00:00 GMT"

    def test_without_prior_entry_sets_initial_counts(self):
        result = {
            "markdown": "hello",
            "source": "llms.txt",
            "url": "https://example.com",
        }
        enriched = _enrich_cache_entry(result.copy(), "https://example.com")
        assert enriched["fetch_count"] == 1
        assert enriched["change_count"] == 0
        assert enriched["first_fetched_at"] > 0

    def test_with_prior_entry_detects_content_change(self):
        prior = {
            "content_hash": hashlib.sha256(b"old content").hexdigest(),
            "fetch_count": 3,
            "change_count": 1,
            "first_fetched_at": 1000.0,
        }
        result = {
            "markdown": "new content",
            "source": "llms.txt",
            "url": "https://example.com",
        }
        enriched = _enrich_cache_entry(
            result.copy(), "https://example.com", prior_entry=prior
        )
        assert enriched["fetch_count"] == 4
        assert enriched["change_count"] == 2  # Incremented because content changed
        assert enriched["first_fetched_at"] == 1000.0

    def test_with_prior_entry_no_content_change(self):
        same_hash = hashlib.sha256(b"same content").hexdigest()
        prior = {
            "content_hash": same_hash,
            "fetch_count": 3,
            "change_count": 0,
            "first_fetched_at": 2000.0,
        }
        result = {
            "markdown": "same content",
            "source": "llms.txt",
            "url": "https://example.com",
        }
        enriched = _enrich_cache_entry(
            result.copy(), "https://example.com", prior_entry=prior
        )
        assert enriched["change_count"] == 0  # Not incremented — content unchanged
        assert enriched["fetch_count"] == 4
