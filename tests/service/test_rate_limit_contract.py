"""Route/API contract tests for the retryable rate-limit behavior (ADR-0053).

Exercises the real router and the real ``GroktoCrawlError`` exception
handler against a deterministic fake limiter: 429 body + headers, no job
record on rejection, bucket separation (agent/answer share ``search``,
crawl uses ``crawl``), fail-open behavior, and the OpenAPI model fields.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from agent.app import groktocrawl_error_handler
from agent.exceptions import GroktoCrawlError
from agent.models import AgentStatusResponse, CrawlStatusResponse
from agent.routes import router
from fastapi import FastAPI
from fastapi.testclient import TestClient


class FakeLimiter:
    """Deterministic limiter recording every checked key."""

    def __init__(self, allowed: bool = True, limit: int = 10, window: int = 60):
        self.allowed = allowed
        self.limit = limit
        self.window = window
        self.keys: list[str] = []

    async def check(self, key: str) -> tuple[bool, int]:
        self.keys.append(key)
        return self.allowed, (self.limit if self.allowed else 0)

    def retry_after_seconds(self, now: float | None = None) -> int:
        return 37

    def reset_at_iso(self, now: float | None = None) -> str:
        return "2026-08-15T16:01:00Z"


def _build_app(limiter: FakeLimiter, job_store=None) -> FastAPI:
    app = FastAPI()
    app.state.rate_limiter = limiter
    app.state.job_store = job_store if job_store is not None else MagicMock()
    app.state.max_searches_per_request = 5
    # State used by the allowed (non-rejected) route paths.
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
    limiter = FakeLimiter(allowed=False)
    app = _build_app(limiter)
    return TestClient(app), limiter


def _counter_value(counter_name: str, labels: dict[str, str]) -> float:
    """Read a counter's value by name and label set (order-insensitive)."""
    from agent.metrics import METRICS

    counter = METRICS._counters.get(counter_name)
    if counter is None:
        return 0.0
    key = tuple(sorted(labels.items()))
    for collected_key, value in counter[1]._collect():
        if collected_key == key:
            return value
    return 0.0


class TestAdmission429Contract:
    def test_agent_rejection_has_retry_metadata(self, client):
        test_client, _ = client
        resp = test_client.post(
            "/v2/agent", json={"prompt": "What is the capital of France?"}
        )
        assert resp.status_code == 429
        body = resp.json()
        assert body["success"] is False
        assert body["error_code"] == "RATE_LIMITED"
        assert body["retryable"] is True
        assert body["retry_after_seconds"] == 37
        assert body["details"]["bucket"] == "search"
        assert body["details"]["limit"] == 10
        assert body["details"]["remaining"] == 0
        assert body["details"]["reset_at"] == "2026-08-15T16:01:00Z"
        assert "client" not in json_lower_keys(body)

    def test_agent_rejection_has_standard_headers(self, client):
        test_client, _ = client
        resp = test_client.post(
            "/v2/agent", json={"prompt": "What is the capital of France?"}
        )
        assert resp.headers["Retry-After"] == "37"
        assert resp.headers["RateLimit-Limit"] == "10"
        assert resp.headers["RateLimit-Remaining"] == "0"
        assert resp.headers["RateLimit-Reset"] == "37"

    def test_rejected_agent_request_creates_no_job(self, client):
        test_client, _ = client
        resp = test_client.post(
            "/v2/agent", json={"prompt": "What is the capital of France?"}
        )
        assert resp.status_code == 429
        app = test_client.app
        app.state.job_store.create_job.assert_not_called()
        app.state.job_store.fail_job.assert_not_called()

    def test_answer_rejection_shares_search_bucket(self, client):
        test_client, limiter = client
        resp = test_client.post(
            "/v2/answer", json={"query": "What is the capital of France?"}
        )
        assert resp.status_code == 429
        assert limiter.keys and limiter.keys[0].endswith(":search")
        assert resp.json()["details"]["bucket"] == "search"

    def test_crawl_rejection_uses_crawl_bucket(self):
        limiter = FakeLimiter(allowed=False)
        app = _build_app(limiter)
        with TestClient(app) as test_client:
            resp = test_client.post("/v2/crawl", json={"url": "https://example.com"})
        assert resp.status_code == 429
        assert limiter.keys and limiter.keys[0].endswith(":crawl")
        assert resp.json()["details"]["bucket"] == "crawl"

    def test_rejection_increments_admission_metric_without_failed_job(self, client):
        test_client, _ = client
        before = _counter_value(
            "rate_limited_admissions_total", {"operation": "agent", "bucket": "search"}
        )
        failed_before = _counter_value("jobs_failed_total", {"type": "agent"})
        resp = test_client.post(
            "/v2/agent", json={"prompt": "What is the capital of France?"}
        )
        assert resp.status_code == 429
        after = _counter_value(
            "rate_limited_admissions_total", {"operation": "agent", "bucket": "search"}
        )
        failed_after = _counter_value("jobs_failed_total", {"type": "agent"})
        assert after == before + 1
        # A rejected admission is not a failed job: jobs_failed_total is
        # never incremented for it (AC-001.3).
        assert failed_after == failed_before


def json_lower_keys(obj):
    """Flatten all JSON keys (lowercased) for redaction assertions."""
    keys: list[str] = []

    def _walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                keys.append(k.lower())
                _walk(v)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(obj)
    return keys


class TestFailOpen:
    def test_limiter_backend_failure_is_not_labeled_rate_limited(self):
        class FailingLimiter(FakeLimiter):
            async def check(self, key):
                self.keys.append(key)
                return True, self.limit  # fail open

        limiter = FailingLimiter()
        app = _build_app(limiter)
        app.state.job_store.create_job.return_value = "job_1"
        with TestClient(app) as test_client:
            resp = test_client.post(
                "/v2/agent", json={"prompt": "hello", "stream": False}
            )
        # Fail-open must NOT label the request rate limited: the request is
        # admitted and proceeds past the rate-limit check.
        assert resp.status_code != 429
        assert "retryable" not in (resp.json() or {})


class TestOpenAPIModels:
    def test_agent_status_response_exposes_retry_fields(self):
        schema = AgentStatusResponse.model_json_schema()
        props = schema["properties"]
        for field in (
            "retry_at",
            "retry_attempt",
            "retry_limit",
            "retryable",
            "retry_reason",
        ):
            assert field in props, field

    def test_agent_status_exposes_safe_provider_failure_details(self):
        payload = AgentStatusResponse(
            status="failed",
            error="LLM provider stopped before completing the response",
            error_code="LLM_OUTPUT_TRUNCATED",
            error_details={
                "finish_reason": "length",
                "refusal_present": False,
                "prompt_tokens": 123,
                "completion_tokens": 8192,
            },
        ).model_dump(by_alias=True, exclude_none=True)

        assert payload["errorCode"] == "LLM_OUTPUT_TRUNCATED"
        assert payload["errorDetails"] == {
            "finish_reason": "length",
            "refusal_present": False,
            "prompt_tokens": 123,
            "completion_tokens": 8192,
        }

    def test_crawl_status_response_exposes_retry_fields(self):
        schema = CrawlStatusResponse.model_json_schema()
        # CrawlStatusResponse uses to_camel aliases, so the schema exposes
        # camelCase names for the new retry fields.
        props = schema["properties"]
        for field in (
            "retryAt",
            "retryAttempt",
            "retryLimit",
            "retryable",
            "retryReason",
        ):
            assert field in props, field

    def test_legacy_rate_limited_error_without_metadata_keeps_legacy_body(self):
        from agent.exceptions import RateLimitedError

        app = FastAPI()
        app.add_exception_handler(GroktoCrawlError, groktocrawl_error_handler)

        @app.get("/boom")
        async def boom():
            raise RateLimitedError("budget exhausted")

        with TestClient(app) as test_client:
            resp = test_client.get("/boom")
        assert resp.status_code == 429
        body = resp.json()
        assert body["error_code"] == "RATE_LIMITED"
        assert "retryable" not in body
        assert "retry_after_seconds" not in body
        assert "Retry-After" not in resp.headers

    def test_non_finite_retry_delay_is_not_relayed(self):
        """inf/nan delays must not crash the handler into a 500 (review P2)."""
        from agent.exceptions import RetryableRateLimitError

        app = FastAPI()
        app.add_exception_handler(GroktoCrawlError, groktocrawl_error_handler)

        @app.get("/boom")
        async def boom():
            raise RetryableRateLimitError(
                "downstream capacity", retry_after_seconds=float("inf")
            )

        with TestClient(app) as test_client:
            resp = test_client.get("/boom")
        assert resp.status_code == 429
        body = resp.json()
        assert "retryable" not in body
        assert "Retry-After" not in resp.headers

    def test_excessive_retry_delay_is_clamped_on_relay(self):
        """The relayed delay is clamped to the documented retry ceiling."""
        from agent.exceptions import RetryableRateLimitError

        app = FastAPI()
        app.add_exception_handler(GroktoCrawlError, groktocrawl_error_handler)

        @app.get("/boom")
        async def boom():
            raise RetryableRateLimitError(
                "downstream capacity", retry_after_seconds=99999
            )

        with TestClient(app) as test_client:
            resp = test_client.get("/boom")
        assert resp.status_code == 429
        assert resp.json()["retry_after_seconds"] == 60
        assert resp.headers["Retry-After"] == "60"
