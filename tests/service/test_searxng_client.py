"""Tests for agent-svc/agent/searxng_client.py — SearXNG client.

Tests category translation, engine health parsing, and search API calls.
"""

import httpx
import pytest


@pytest.fixture
def client():
    from agent.searxng_client import SearXNGClient

    return SearXNGClient(base_url="http://searxng.test")


class TestTranslate:
    def setup_method(self):
        from agent.searxng_client import SearXNGClient

        self.translate = SearXNGClient._translate

    def test_empty_sources_and_categories_defaults_to_general(self):
        assert self.translate(None, None) == ["general"]

    def test_empty_lists_defaults_to_general(self):
        assert self.translate([], []) == ["general"]

    def test_maps_sources_to_categories(self):
        result = self.translate(["news", "web", "images"], None)
        assert "news" in result
        assert "general" in result  # web -> general, images not mapped
        # images maps to nothing known, should pass through as "images"
        assert "images" in result

    def test_maps_categories(self):
        result = self.translate(None, ["research", "github"])
        assert "science" in result  # research -> science
        assert "it" in result  # github -> it

    def test_dedupes_categories(self):
        result = self.translate(["news"], ["news"])
        assert result.count("news") == 1
        assert len(result) == 1

    def test_passes_unknown_values_through(self):
        result = self.translate(["custom-engine"], None)
        assert "custom-engine" in result

    def test_merges_sources_and_categories(self):
        result = self.translate(["news"], ["research"])
        assert "news" in result
        assert "science" in result


class TestParseEngineHealth:
    def setup_method(self):
        from agent.searxng_client import SearXNGClient

        self.parse = SearXNGClient._parse_engine_health

    def test_all_engines_healthy(self):
        data = {
            "engines": [
                {"engine": "google", "results": 10},
                {"engine": "brave", "results": 5},
            ]
        }
        health = self.parse(data, [{"url": "x"}])
        assert health.engines_total == 2
        assert health.engines_responding == 2
        assert health.empty_result is False
        assert health.degraded is False
        assert "Healthy" in health.detail

    def test_no_engine_status(self):
        data = {"engines": []}
        health = self.parse(data, [])
        assert health.engines_total == 0
        assert health.engines_responding == 0
        assert health.empty_result is False
        assert health.degraded is False
        assert "No engine status" in health.detail

    def test_degraded_when_fewer_than_half_respond(self):
        data = {
            "engines": [
                {"engine": "google", "results": 10},
                {"engine": "brave", "results": 0},
                {"engine": "duckduckgo", "results": 0},
            ]
        }
        health = self.parse(data, [{"url": "https://example.com"}])
        assert health.engines_total == 3
        assert health.engines_responding == 1
        assert health.degraded is True
        assert "Degraded" in health.detail

    def test_empty_result_when_engines_respond_no_urls(self):
        """Engines responded but returned no results with valid URLs."""
        data = {
            "engines": [
                {"engine": "google", "results": 1},
            ]
        }
        # Results exist but have no URL
        health = self.parse(data, [{"url": ""}])
        assert health.engines_responding == 1
        assert health.empty_result is True
        assert "no results" in health.detail


class TestSearch:
    @pytest.mark.asyncio
    async def test_successful_search(self, client):
        mock_data = {
            "results": [
                {
                    "url": "https://a.com",
                    "title": "Page A",
                    "content": "Desc A",
                    "engine": "google",
                },
                {
                    "url": "https://b.com",
                    "title": "Page B",
                    "content": "Desc B",
                    "engine": "brave",
                },
            ],
            "engines": [
                {"engine": "google", "results": 1},
                {"engine": "brave", "results": 1},
            ],
        }

        with pytest.MonkeyPatch.context() as mp:

            async def mock_get(url, params=None):
                import types

                r = types.SimpleNamespace()
                r.status_code = 200
                r.json = lambda: mock_data
                return r

            mp.setattr(client._client, "get", mock_get)

            results, health = await client.search("test query")
            assert len(results) == 2
            assert results[0]["url"] == "https://a.com"
            assert health.engines_total == 2
            assert health.engines_responding == 2

    @pytest.mark.asyncio
    async def test_non_200_response(self, client):
        with pytest.MonkeyPatch.context() as mp:

            async def mock_get(url, params=None):
                import types

                r = types.SimpleNamespace()
                r.status_code = 500
                r.text = "Server Error"
                return r

            mp.setattr(client._client, "get", mock_get)

            results, health = await client.search("test")
            assert results == []
            assert "HTTP 500" in health.detail

    @pytest.mark.asyncio
    async def test_timeout_returns_empty(self, client):

        with pytest.MonkeyPatch.context() as mp:

            async def mock_get(url, params=None):
                raise httpx.TimeoutException("timed out")

            mp.setattr(client._client, "get", mock_get)

            results, health = await client.search("test")
            assert results == []
            assert "timed out" in health.detail.lower()

    @pytest.mark.asyncio
    async def test_general_exception_returns_empty(self, client):
        with pytest.MonkeyPatch.context() as mp:

            async def mock_get(url, params=None):
                raise ValueError("something broke")

            mp.setattr(client._client, "get", mock_get)

            results, health = await client.search("test")
            assert results == []
            assert "failed" in health.detail.lower()

    @pytest.mark.asyncio
    async def test_failure_diagnostics_do_not_expose_query_text(self, client, caplog):
        secret_query = "private fixture query"

        with pytest.MonkeyPatch.context() as mp:

            async def mock_get(url, params=None):
                raise ValueError(f"failed request for {secret_query}")

            mp.setattr(client._client, "get", mock_get)

            results, health = await client.search(secret_query)
            assert results == []
            assert secret_query not in health.detail
            assert secret_query not in caplog.text

    @pytest.mark.asyncio
    async def test_respects_limit(self, client):
        mock_data = {
            "results": [
                {
                    "url": f"https://{i}.com",
                    "title": f"Page {i}",
                    "content": "",
                    "engine": "google",
                }
                for i in range(20)
            ],
            "engines": [],
        }

        with pytest.MonkeyPatch.context() as mp:

            async def mock_get(url, params=None):
                import types

                r = types.SimpleNamespace()
                r.status_code = 200
                r.json = lambda: mock_data
                return r

            mp.setattr(client._client, "get", mock_get)

            results, _ = await client.search("test query", limit=5)
            assert len(results) == 5

    @pytest.mark.asyncio
    async def test_passes_categories_param(self, client):
        with pytest.MonkeyPatch.context() as mp:
            captured_params = {}

            async def mock_get(url, params=None):
                captured_params.update(params or {})
                import types

                r = types.SimpleNamespace()
                r.status_code = 200
                r.json = lambda: {"results": [], "engines": []}
                return r

            mp.setattr(client._client, "get", mock_get)

            await client.search("test query", categories=["news", "research"])
            assert "categories" in captured_params
            assert captured_params["categories"] == "news,science"

    @pytest.mark.asyncio
    async def test_unscoped_search_omits_categories(self, client):
        captured_params = {}

        async def mock_get(url, params=None):
            captured_params.update(params or {})
            import types

            r = types.SimpleNamespace()
            r.status_code = 200
            r.json = lambda: {"results": [], "engines": []}
            return r

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(client._client, "get", mock_get)
            await client.search("npm package provenance")

        assert "categories" not in captured_params

    @pytest.mark.asyncio
    async def test_explicit_general_scope_is_preserved(self, client):
        captured_params = {}

        async def mock_get(url, params=None):
            captured_params.update(params or {})
            import types

            r = types.SimpleNamespace()
            r.status_code = 200
            r.json = lambda: {"results": [], "engines": []}
            return r

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(client._client, "get", mock_get)
            await client.search("npm package provenance", categories=["general"])

        assert captured_params["categories"] == "general"

    @pytest.mark.asyncio
    async def test_forwards_scenario_param(self, client):
        """search() must forward the scenario parameter in the HTTP request.

        The scenario identifies the SlopSearX twin scenario to serve; dropping
        it would silently change which scenario the upstream returns.
        """
        with pytest.MonkeyPatch.context() as mp:
            captured_params = {}

            async def mock_get(url, params=None):
                captured_params.update(params or {})
                import types

                r = types.SimpleNamespace()
                r.status_code = 200
                r.json = lambda: {"results": [], "engines": []}
                return r

            mp.setattr(client._client, "get", mock_get)

            await client.search("test query", scenario="rate-limit-retry-after")
            assert captured_params.get("scenario") == "rate-limit-retry-after"

    @pytest.mark.asyncio
    async def test_passes_sources_category_param(self, client):
        """search() must forward the categories translated from ``sources``.

        sources→category translation is exercised through search() itself, not
        only via the standalone ``_translate()`` helper.
        """
        with pytest.MonkeyPatch.context() as mp:
            captured_params = {}

            async def mock_get(url, params=None):
                captured_params.update(params or {})
                import types

                r = types.SimpleNamespace()
                r.status_code = 200
                r.json = lambda: {"results": [], "engines": []}
                return r

            mp.setattr(client._client, "get", mock_get)

            await client.search("test query", sources=["news"])
            assert captured_params["categories"] == "news"

    @pytest.mark.asyncio
    async def test_search_budget_exhausted_raises_rate_limited(self):
        """A client serves its search budget, then raises RateLimitedError.

        The budget is enforced across calls: exactly ``max_searches`` searches
        succeed and the next one is rejected before any HTTP request is made.
        """
        from agent.exceptions import RateLimitedError
        from agent.searxng_client import SearXNGClient

        budget_client = SearXNGClient(base_url="http://searxng.test", max_searches=2)
        with pytest.MonkeyPatch.context() as mp:

            async def mock_get(url, params=None):
                import types

                r = types.SimpleNamespace()
                r.status_code = 200
                r.json = lambda: {"results": [], "engines": []}
                return r

            mp.setattr(budget_client._client, "get", mock_get)

            # The two in-budget searches succeed.
            results, _ = await budget_client.search("one")
            assert results == []
            results, _ = await budget_client.search("two")
            assert results == []

            # The next search exceeds the budget and must be rejected.
            with pytest.raises(RateLimitedError):
                await budget_client.search("three")

    @pytest.mark.asyncio
    async def test_close(self, client):
        with pytest.MonkeyPatch.context() as mp:
            closed = False

            async def mock_close():
                nonlocal closed
                closed = True

            mp.setattr(client._client, "aclose", mock_close)

            await client.close()
            assert closed


class TestRateLimitClassification:
    """Downstream 429 classification (ADR-0053)."""

    def test_parse_retry_after_valid_seconds(self):
        from agent.searxng_client import _parse_retry_after

        assert _parse_retry_after("37") == 37.0
        assert _parse_retry_after("2.5") == 2.5

    def test_parse_retry_after_invalid_values(self):
        from agent.searxng_client import _parse_retry_after

        assert _parse_retry_after(None) is None
        assert _parse_retry_after("") is None
        assert _parse_retry_after("soon") is None
        assert _parse_retry_after("-3") is None
        assert _parse_retry_after("Tue, 15 Nov 1994 08:12:31 GMT") is None
        # Non-finite values must be treated as absent, never relayed.
        assert _parse_retry_after("inf") is None
        assert _parse_retry_after("nan") is None
        assert _parse_retry_after("1e309") is None

    def test_parse_retry_after_zero_seconds(self):
        """Zero seconds is a valid Retry-After and must be relayed as 0.0."""
        from agent.searxng_client import _parse_retry_after

        assert _parse_retry_after("0") == 0.0

    def test_parse_retry_after_fractional_seconds(self):
        """Sub-second Retry-After values must be relayed as their fractional float."""
        from agent.searxng_client import _parse_retry_after

        assert _parse_retry_after("0.5") == 0.5

    @pytest.mark.asyncio
    async def test_429_raises_retryable_error_with_retry_after(self, client):
        from agent.exceptions import RetryableRateLimitError

        with pytest.MonkeyPatch.context() as mp:

            async def mock_get(url, params=None):
                import types

                r = types.SimpleNamespace()
                r.status_code = 429
                r.headers = {"Retry-After": "37"}
                r.text = "rate limited"
                return r

            mp.setattr(client._client, "get", mock_get)

            with pytest.raises(RetryableRateLimitError) as exc:
                await client.search("test", raise_on_rate_limit=True)
            assert exc.value.retry_after_seconds == 37.0
            assert exc.value.error_code == "RATE_LIMITED"

    @pytest.mark.asyncio
    async def test_429_without_retry_metadata_uses_none_delay(self, client):
        from agent.exceptions import RetryableRateLimitError

        with pytest.MonkeyPatch.context() as mp:

            async def mock_get(url, params=None):
                import types

                r = types.SimpleNamespace()
                r.status_code = 429
                r.headers = {}
                r.text = "rate limited"
                return r

            mp.setattr(client._client, "get", mock_get)

            with pytest.raises(RetryableRateLimitError) as exc:
                await client.search("test", raise_on_rate_limit=True)
            assert exc.value.retry_after_seconds is None

    @pytest.mark.asyncio
    async def test_429_is_not_swallowed_as_empty_results_when_opted_in(self, client):
        """An opted-in call site must never see a capacity condition as an empty set."""
        from agent.exceptions import RetryableRateLimitError

        with pytest.MonkeyPatch.context() as mp:

            async def mock_get(url, params=None):
                import types

                r = types.SimpleNamespace()
                r.status_code = 429
                r.headers = {}
                r.text = "rate limited"
                return r

            mp.setattr(client._client, "get", mock_get)

            with pytest.raises(RetryableRateLimitError):
                await client.search("test", raise_on_rate_limit=True)

    @pytest.mark.asyncio
    async def test_default_call_site_degrades_to_empty_results_on_429(self, client):
        """Degrading call sites (session steps, /v2/search) keep legacy behavior.

        An upstream 429 must not hard-fail a search step that tolerates
        empty results (ADR-0053 opt-in classification).
        """
        with pytest.MonkeyPatch.context() as mp:

            async def mock_get(url, params=None):
                import types

                r = types.SimpleNamespace()
                r.status_code = 429
                r.headers = {"Retry-After": "37"}
                r.text = "rate limited"
                return r

            mp.setattr(client._client, "get", mock_get)

            results, health = await client.search("test")
            assert results == []
            assert "429" in health.detail
