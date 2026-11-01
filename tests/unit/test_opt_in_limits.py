"""ADR-0077: request-rate and local count caps are opt-in.

These tests pin the *defaults* and the "0 / empty means disabled" semantics so
the caps cannot silently return as implicit throttles for a single operator
running several agents.
"""

from __future__ import annotations

import pytest
from agent.rate_limiter import (
    DisabledRateLimiter,
    SlidingWindowRateLimiter,
    build_rate_limiter,
)
from agent.settings import AgentSettings


class TestDefaults:
    def test_agent_settings_defaults_do_not_throttle(self, monkeypatch):
        for key in (
            "AGENT_SEARCH_RATE_LIMIT",
            "AGENT_MAX_SEARCHES_PER_REQUEST",
            "ADMISSION_LIGHT_FETCH_LIMIT",
            "ADMISSION_BROWSER_LIMIT",
            "ADMISSION_LLM_LIMIT",
            "CRAWL_MAX_DURATION_SECONDS",
        ):
            monkeypatch.delenv(key, raising=False)
        settings = AgentSettings.model_validate({})
        assert settings.search_rate_limit == ""
        assert settings.max_searches_per_request == 0
        assert settings.admission_light_fetch_limit == 0
        assert settings.admission_browser_limit == 0
        assert settings.admission_llm_limit == 0
        assert settings.crawl_max_duration_seconds == 0

    def test_crawl_idle_timeout_remains_the_stall_detector(self):
        assert AgentSettings.model_validate({}).crawl_idle_timeout_seconds == 300


class TestBuildRateLimiter:
    @pytest.mark.parametrize("spec", ["", "  ", "off", "OFF", "none", "disabled", "0"])
    def test_disabled_specs_build_a_noop_limiter(self, spec):
        limiter = build_rate_limiter(object(), spec)
        assert isinstance(limiter, DisabledRateLimiter)
        assert limiter.enabled is False
        assert limiter.limit == 0
        assert limiter.retry_after_seconds() == 0
        assert limiter.reset_at_iso() is None

    @pytest.mark.asyncio
    async def test_disabled_limiter_always_admits(self):
        allowed, remaining = await DisabledRateLimiter().check("1.2.3.4:search")
        assert allowed is True
        assert remaining == 0

    def test_configured_spec_builds_a_real_limiter(self):
        limiter = build_rate_limiter(object(), "10/60s")
        assert isinstance(limiter, SlidingWindowRateLimiter)
        assert limiter.enabled is True
        assert (limiter.limit, limiter.window) == (10, 60)


class TestSearchBudget:
    def test_zero_budget_is_unbounded(self):
        from agent.searxng_client import SearXNGClient

        client = SearXNGClient(max_searches=0)
        for _ in range(50):
            client._reserve_search_slot()
        assert client._search_count == 50

    def test_positive_budget_still_applies(self):
        from agent.exceptions import RateLimitedError
        from agent.searxng_client import SearXNGClient

        client = SearXNGClient(max_searches=2)
        client._reserve_search_slot()
        client._reserve_search_slot()
        with pytest.raises(RateLimitedError):
            client._reserve_search_slot()

    def test_apply_search_budget_keeps_every_query_when_unbounded(self):
        from agent.research.discovery import apply_search_budget

        queries = [f"q{i}" for i in range(9)]
        assert apply_search_budget(queries, 0) == queries

    def test_apply_search_budget_truncates_when_configured(self):
        from agent.research.discovery import apply_search_budget

        queries = [f"q{i}" for i in range(9)]
        assert apply_search_budget(queries, 5) == queries[:5]


class TestResponseHeaders:
    def test_disabled_limiter_and_unbounded_budget_emit_no_headers(self):
        from agent.routes._helpers import _apply_rate_headers

        headers: dict[str, str] = {}
        _apply_rate_headers(headers, DisabledRateLimiter(), 0, max_searches=0)
        assert headers == {}

    def test_enabled_limiter_and_budget_emit_headers(self):
        from agent.routes._helpers import _apply_rate_headers

        headers: dict[str, str] = {}
        _apply_rate_headers(
            headers,
            SlidingWindowRateLimiter(redis=object(), limit=10, window_seconds=60),
            7,
            max_searches=5,
        )
        assert headers == {
            "X-Search-Budget": "5/5",
            "X-Search-Rate-Remaining": "7/10",
        }
