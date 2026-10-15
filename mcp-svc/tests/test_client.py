"""Tests for the GroktocrawlClient HTTP client.

Uses httpx.MockTransport to simulate agent-svc responses so that no
running agent-svc is required.
"""

from __future__ import annotations

import asyncio
import base64
import json
from typing import Any

import httpx
import pytest
from groktocrawl_client import GroktocrawlClient, _extract_response_detail

# ── helpers ──


def _json_handler(body: dict[str, Any], status_code: int = 200) -> httpx.Handler:
    """Return a mock handler that responds with JSON."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json=body, request=request)

    return handler


def _error_handler(status_code: int, body: dict[str, Any]) -> httpx.Handler:
    """Return a mock handler for an HTTP error."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json=body, request=request)

    return handler


def _make_matched_client(
    responses: dict[tuple[str, str], httpx.Handler],
    api_key: str | None = None,
    default_timeout: float = 5.0,
) -> GroktocrawlClient:
    """Create a GroktocrawlClient backed by a MockTransport."""

    def _dispatch(request: httpx.Request) -> httpx.Response:
        key = (request.method, request.url.path)
        handler = responses.get(key)
        if handler is not None:
            return handler(request)
        return httpx.Response(404, json={"error": "not found"}, request=request)

    transport = httpx.MockTransport(_dispatch)
    client = GroktocrawlClient(
        base_url="http://test:8080",
        api_key=api_key,
        default_timeout=default_timeout,
    )
    client._client = httpx.AsyncClient(
        base_url=client._base_url,
        headers=client._headers(),
        transport=transport,
    )
    return client


# ── _extract_response_detail ──


class TestExtractResponseDetail:
    def test_fastapi_detail_string(self):
        resp = httpx.Response(
            422,
            json={"detail": "Field required"},
            request=httpx.Request("POST", "http://x"),
        )
        assert _extract_response_detail(resp) == "Field required"

    def test_fastapi_detail_list(self):
        resp = httpx.Response(
            422,
            json={"detail": [{"msg": "field required"}, {"msg": "value_error"}]},
            request=httpx.Request("POST", "http://x"),
        )
        detail = _extract_response_detail(resp)
        assert "field required" in detail
        assert "value_error" in detail

    def test_groktocrawl_error_key(self):
        resp = httpx.Response(
            500,
            json={"error": "Internal server failure"},
            request=httpx.Request("POST", "http://x"),
        )
        assert _extract_response_detail(resp) == "Internal server failure"

    def test_groktocrawl_message_key(self):
        resp = httpx.Response(
            400,
            json={"message": "Bad request"},
            request=httpx.Request("POST", "http://x"),
        )
        assert _extract_response_detail(resp) == "Bad request"

    def test_plain_text_fallback(self):
        resp = httpx.Response(
            500, content=b"Something broke", request=httpx.Request("POST", "http://x")
        )
        assert _extract_response_detail(resp) == "Something broke"

    def test_non_json_response(self):
        resp = httpx.Response(
            502,
            content=b"<html>Bad Gateway</html>",
            headers={"Content-Type": "text/html"},
            request=httpx.Request("POST", "http://x"),
        )
        assert _extract_response_detail(resp) == "<html>Bad Gateway</html>"


# ── from_env / constructor ──


class TestFromEnv:
    def test_defaults(self, monkeypatch):
        monkeypatch.delenv("GROKTOCRAWL_URL", raising=False)
        monkeypatch.delenv("GROKTOCRAWL_API_URL", raising=False)
        monkeypatch.delenv("GROKTOCRAWL_API_KEY", raising=False)
        client = GroktocrawlClient.from_env()
        assert client._base_url == "http://localhost:8080"
        assert client._api_key is None
        assert client._default_timeout == 120.0

    def test_groktocrawl_url(self, monkeypatch):
        monkeypatch.setenv("GROKTOCRAWL_URL", "http://deployment.example.internal:8080")
        monkeypatch.delenv("GROKTOCRAWL_API_URL", raising=False)
        monkeypatch.delenv("GROKTOCRAWL_API_KEY", raising=False)
        client = GroktocrawlClient.from_env()
        assert client._base_url == "http://deployment.example.internal:8080"

    def test_fallback_to_groktocrawl_api_url(self, monkeypatch):
        monkeypatch.delenv("GROKTOCRAWL_URL", raising=False)
        monkeypatch.setenv("GROKTOCRAWL_API_URL", "http://legacy:9090")
        monkeypatch.delenv("GROKTOCRAWL_API_KEY", raising=False)
        client = GroktocrawlClient.from_env()
        assert client._base_url == "http://legacy:9090"

    def test_groktocrawl_url_takes_precedence(self, monkeypatch):
        monkeypatch.setenv("GROKTOCRAWL_URL", "http://new:8080")
        monkeypatch.setenv("GROKTOCRAWL_API_URL", "http://old:8080")
        monkeypatch.delenv("GROKTOCRAWL_API_KEY", raising=False)
        client = GroktocrawlClient.from_env()
        assert client._base_url == "http://new:8080"

    def test_api_key(self, monkeypatch):
        monkeypatch.delenv("GROKTOCRAWL_URL", raising=False)
        monkeypatch.setenv("GROKTOCRAWL_API_KEY", "test-key-123")
        client = GroktocrawlClient.from_env()
        assert client._api_key == "test-key-123"

    def test_custom_timeout(self, monkeypatch):
        monkeypatch.delenv("GROKTOCRAWL_URL", raising=False)
        monkeypatch.delenv("GROKTOCRAWL_API_KEY", raising=False)
        client = GroktocrawlClient.from_env(default_timeout=30.0)
        assert client._default_timeout == 30.0


# ── Error propagation ─ VAL-MCP-G03, G04, G05 ──


class TestErrorPropagation:
    """VAL-MCP-G03: HTTP errors translated to structured errors."""

    def test_http_4xx_becomes_structured_error(self):
        """VAL-MCP-G03: HTTP 4xx returns error with status_code."""
        client = _make_matched_client(
            {
                ("POST", "/v2/scrape"): _error_handler(
                    400, {"error": "Invalid URL format"}
                )
            }
        )

        async def run():
            return await client.scrape("not-a-url")

        result = asyncio.run(run())
        assert "error" in result
        assert result["status_code"] == 400
        assert "400" in result["error"]
        assert "Invalid URL format" in result["error"]

    def test_http_5xx_becomes_structured_error(self):
        """VAL-MCP-G03: HTTP 5xx returns error with status_code."""
        client = _make_matched_client(
            {("POST", "/v2/scrape"): _error_handler(502, {"error": "Scraper down"})}
        )

        async def run():
            return await client.scrape("https://example.com")

        result = asyncio.run(run())
        assert "error" in result
        assert result["status_code"] == 502
        assert "502" in result["error"]

    def test_http_error_preserves_machine_readable_error_code(self):
        client = _make_matched_client(
            {
                ("POST", "/v2/scrape"): _error_handler(
                    502,
                    {
                        "success": False,
                        "error": "Barrier or challenge content detected",
                        "error_code": "BARRIER_DETECTED",
                    },
                )
            }
        )

        result = asyncio.run(client.scrape("https://example.com/challenge"))

        assert result["status_code"] == 502
        assert result["error_code"] == "BARRIER_DETECTED"

    def test_http_401_auth_failure(self):
        """VAL-MCP-H03: Invalid API key propagates as 401 error."""
        client = _make_matched_client(
            {
                ("GET", "/v2/activity"): _error_handler(
                    401, {"detail": "Invalid API key"}
                )
            }
        )

        async def run():
            return await client.get_activity()

        result = asyncio.run(run())
        assert "error" in result
        assert result["status_code"] == 401
        assert "401" in result["error"]

    def test_http_404_job_not_found(self):
        """GET nonexistent job returns not-found error."""
        client = _make_matched_client(
            {
                ("GET", "/v2/crawl/nonexistent"): _error_handler(
                    404, {"detail": "Job not found"}
                )
            }
        )

        async def run():
            return await client.get_crawl_status("nonexistent")

        result = asyncio.run(run())
        assert "error" in result
        assert result["status_code"] == 404

    def test_successful_response_not_wrapped_in_error(self):
        """Verify 200 responses are not wrapped as errors."""
        client = _make_matched_client(
            {
                ("POST", "/v2/scrape"): _json_handler(
                    {"success": True, "data": {"markdown": "hello"}}
                )
            }
        )

        async def run():
            return await client.scrape("https://example.com")

        result = asyncio.run(run())
        assert "error" not in result
        assert result["success"] is True

    def test_timeout_with_duration_info(self):
        """VAL-MCP-G04: Timeout includes duration and timeout info."""

        def _timeout(request: httpx.Request) -> httpx.Response:
            raise httpx.TimeoutException("timed out after 0.1s", request=request)

        transport = httpx.MockTransport(_timeout)
        client = GroktocrawlClient(
            base_url="http://test:8080",
            api_key=None,
            default_timeout=5.0,
        )
        client._client = httpx.AsyncClient(
            base_url=client._base_url,
            headers=client._headers(),
            transport=transport,
        )

        async def run():
            return await client.scrape("https://example.com")

        result = asyncio.run(run())
        assert "error" in result
        assert "timed out" in result["error"].lower()
        assert "timeout" in result["error"].lower()
        assert "5s" in result["error"] or "5.0s" in result["error"]

    def test_connect_error_clean_message(self):
        """VAL-MCP-G05: ConnectError returns clean message without traceback."""

        def _refuse(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("Connection refused", request=request)

        transport = httpx.MockTransport(_refuse)
        client = GroktocrawlClient(
            base_url="http://down:9999",
            api_key=None,
            default_timeout=5.0,
        )
        client._client = httpx.AsyncClient(
            base_url=client._base_url,
            headers=client._headers(),
            transport=transport,
        )

        async def run():
            return await client.scrape("https://example.com")

        result = asyncio.run(run())
        assert "error" in result
        assert "connection" in result["error"].lower()
        assert "unable to reach server" in result["error"].lower()
        assert "Traceback" not in result["error"]


# ── Status / cancellation / activity methods ──


class TestStatusMethods:
    def test_get_crawl_status(self):
        client = _make_matched_client(
            {
                ("GET", "/v2/crawl/job-1"): _json_handler(
                    {"status": "completed", "completed": 10, "total": 10}
                )
            }
        )

        async def run():
            return await client.get_crawl_status("job-1")

        result = asyncio.run(run())
        assert result["status"] == "completed"
        assert result["completed"] == 10

    def test_cancel_crawl(self):
        client = _make_matched_client(
            {
                ("DELETE", "/v2/crawl/job-1"): _json_handler(
                    {"success": True, "status": "cancelled"}
                )
            }
        )

        async def run():
            return await client.cancel_crawl("job-1")

        result = asyncio.run(run())
        assert result["success"] is True
        assert result["status"] == "cancelled"

    def test_get_crawl_errors(self):
        client = _make_matched_client(
            {
                ("GET", "/v2/crawl/job-1/errors"): _json_handler(
                    {
                        "errors": [{"url": "https://bad.example", "error": "timeout"}],
                        "robots_blocked": [],
                    }
                )
            }
        )

        async def run():
            return await client.get_crawl_errors("job-1")

        result = asyncio.run(run())
        assert len(result["errors"]) == 1
        assert result["errors"][0]["url"] == "https://bad.example"

    def test_get_agent_status(self):
        client = _make_matched_client(
            {
                ("GET", "/v2/agent/job-1"): _json_handler(
                    {"status": "completed", "data": {"result": "research"}}
                )
            }
        )

        async def run():
            return await client.get_agent_status("job-1")

        result = asyncio.run(run())
        assert result["status"] == "completed"
        assert result["data"]["result"] == "research"

    def test_get_extract_status(self):
        client = _make_matched_client(
            {
                ("GET", "/v2/extract/job-1"): _json_handler(
                    {"status": "completed", "data": {"structured": {"key": "value"}}}
                )
            }
        )

        async def run():
            return await client.get_extract_status("job-1")

        result = asyncio.run(run())
        assert result["status"] == "completed"
        assert result["data"]["structured"]["key"] == "value"

    def test_get_activity(self):
        client = _make_matched_client(
            {
                ("GET", "/v2/activity"): _json_handler(
                    {"active_jobs": 3, "crawls": 1, "agents": 2}
                )
            }
        )

        async def run():
            return await client.get_activity()

        result = asyncio.run(run())
        assert result["active_jobs"] == 3


# ── Auth passthrough ─ VAL-MCP-H01, H02 ──


class TestAuthPassthrough:
    """VAL-MCP-H01: API key passed through as Authorization header."""

    def test_api_key_in_authorization_header(self):
        """Auth header is set when api_key is provided."""
        captured_headers: dict[str, str] = {}

        def _capture(request: httpx.Request) -> httpx.Response:
            captured_headers.update(dict(request.headers))
            return httpx.Response(200, json={"success": True}, request=request)

        transport = httpx.MockTransport(_capture)
        client = GroktocrawlClient(
            base_url="http://test:8080",
            api_key="test-auth-key-xyz",
        )
        client._client = httpx.AsyncClient(
            base_url=client._base_url,
            headers=client._headers(),
            transport=transport,
        )

        async def run():
            await client.scrape("https://example.com")

        asyncio.run(run())
        assert "authorization" in captured_headers
        assert captured_headers["authorization"] == "Bearer test-auth-key-xyz"

    def test_no_auth_header_without_api_key(self):
        """VAL-MCP-H02: No auth header when API key is not set."""
        captured_headers: dict[str, str] = {}

        def _capture(request: httpx.Request) -> httpx.Response:
            captured_headers.update(dict(request.headers))
            return httpx.Response(200, json={"success": True}, request=request)

        transport = httpx.MockTransport(_capture)
        client = GroktocrawlClient(
            base_url="http://test:8080",
            api_key=None,
        )
        client._client = httpx.AsyncClient(
            base_url=client._base_url,
            headers=client._headers(),
            transport=transport,
        )

        async def run():
            await client.scrape("https://example.com")

        asyncio.run(run())
        assert "authorization" not in captured_headers


# ── All 17 tool operations ──


class TestAllTools:
    """Verify each of the 17 tool operations hits the right endpoint."""

    def test_01_scrape_endpoint(self):
        client = _make_matched_client(
            {
                ("POST", "/v2/scrape"): _json_handler(
                    {"success": True, "data": {"markdown": "# hello"}}
                )
            }
        )

        async def run():
            return await client.scrape("https://example.com")

        result = asyncio.run(run())
        assert result["success"] is True

    def test_02_search_endpoint(self):
        client = _make_matched_client(
            {
                ("POST", "/v2/search"): _json_handler(
                    {"data": {"web": [{"url": "https://x.com"}]}}
                )
            }
        )

        async def run():
            return await client.search("test query")

        result = asyncio.run(run())
        assert len(result["data"]["web"]) == 1

    def test_03_crawl_creates_and_polls(self):
        call_count = 0

        def _handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if request.method == "POST" and request.url.path == "/v2/crawl":
                return httpx.Response(200, json={"id": "crawl-99"}, request=request)
            if request.method == "GET" and "/v2/crawl/crawl-99" in request.url.path:
                return httpx.Response(
                    200, json={"status": "completed", "data": []}, request=request
                )
            return httpx.Response(404, json={"error": "not found"}, request=request)

        transport = httpx.MockTransport(_handler)
        client = GroktocrawlClient(base_url="http://test:8080", api_key=None)
        client._client = httpx.AsyncClient(
            base_url=client._base_url, headers=client._headers(), transport=transport
        )

        async def run():
            return await client.crawl("https://example.com", max_pages=5)

        result = asyncio.run(run())
        assert result["status"] == "completed"
        assert call_count >= 2  # create + at least one poll

    def test_04_get_crawl_status_verb(self):
        """Verify get_crawl_status uses GET."""
        client = _make_matched_client(
            {("GET", "/v2/crawl/crawl-1"): _json_handler({"status": "running"})}
        )

        async def run():
            return await client.get_crawl_status("crawl-1")

        result = asyncio.run(run())
        assert result["status"] == "running"

    def test_05_cancel_crawl_verb(self):
        """Verify cancel_crawl uses DELETE."""
        client = _make_matched_client(
            {("DELETE", "/v2/crawl/crawl-1"): _json_handler({"success": True})}
        )

        async def run():
            return await client.cancel_crawl("crawl-1")

        result = asyncio.run(run())
        assert result["success"] is True

    def test_06_get_crawl_errors_verb(self):
        """Verify get_crawl_errors hits correct path."""
        client = _make_matched_client(
            {("GET", "/v2/crawl/crawl-1/errors"): _json_handler({"errors": []})}
        )

        async def run():
            return await client.get_crawl_errors("crawl-1")

        result = asyncio.run(run())
        assert result["errors"] == []

    def test_07_map_endpoint(self):
        client = _make_matched_client(
            {
                ("POST", "/v2/map"): _json_handler(
                    {"links": ["https://a.com", "https://b.com"]}
                )
            }
        )

        async def run():
            return await client.map("https://example.com")

        result = asyncio.run(run())
        assert len(result["links"]) == 2

    def test_08_agent_creates_and_polls(self):
        call_count = 0

        def _handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if request.method == "POST" and request.url.path == "/v2/agent":
                return httpx.Response(200, json={"id": "agent-1"}, request=request)
            if request.method == "GET" and "/v2/agent/agent-1" in request.url.path:
                return httpx.Response(
                    200,
                    json={"status": "completed", "data": {"result": "answer"}},
                    request=request,
                )
            return httpx.Response(404, json={"error": "not found"}, request=request)

        transport = httpx.MockTransport(_handler)
        client = GroktocrawlClient(base_url="http://test:8080", api_key=None)
        client._client = httpx.AsyncClient(
            base_url=client._base_url, headers=client._headers(), transport=transport
        )

        async def run():
            return await client.agent("explain gravity")

        result = asyncio.run(run())
        assert result["status"] == "completed"
        assert call_count >= 2

    def test_09_get_agent_status_verb(self):
        """Verify get_agent_status uses GET."""
        client = _make_matched_client(
            {("GET", "/v2/agent/agent-1"): _json_handler({"status": "processing"})}
        )

        async def run():
            return await client.get_agent_status("agent-1")

        result = asyncio.run(run())
        assert result["status"] == "processing"

    def test_10_answer_endpoint(self):
        client = _make_matched_client(
            {("POST", "/v2/answer"): _json_handler({"answer": "42", "sources": []})}
        )

        async def run():
            return await client.answer("life meaning")

        result = asyncio.run(run())
        assert result["answer"] == "42"

    def test_11_extract_creates_and_polls(self):
        call_count = 0

        def _handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if request.method == "POST" and request.url.path == "/v2/extract":
                return httpx.Response(200, json={"id": "ext-1"}, request=request)
            if request.method == "GET" and "/v2/extract/ext-1" in request.url.path:
                return httpx.Response(
                    200,
                    json={"status": "completed", "data": {"key": "val"}},
                    request=request,
                )
            return httpx.Response(404, json={"error": "not found"}, request=request)

        transport = httpx.MockTransport(_handler)
        client = GroktocrawlClient(base_url="http://test:8080", api_key=None)
        client._client = httpx.AsyncClient(
            base_url=client._base_url, headers=client._headers(), transport=transport
        )

        async def run():
            return await client.extract("https://example.com", {"type": "object"})

        result = asyncio.run(run())
        assert result["status"] == "completed"

    def test_12_get_extract_status_verb(self):
        """Verify get_extract_status uses GET."""
        client = _make_matched_client(
            {("GET", "/v2/extract/ext-1"): _json_handler({"status": "completed"})}
        )

        async def run():
            return await client.get_extract_status("ext-1")

        result = asyncio.run(run())
        assert result["status"] == "completed"

    def test_13_enrich_endpoint(self):
        client = _make_matched_client(
            {("POST", "/v2/enrich"): _json_handler({"summary": "enriched content"})}
        )

        async def run():
            return await client.enrich("https://example.com")

        result = asyncio.run(run())
        assert result["summary"] == "enriched content"

    def test_14_find_similar_endpoint(self):
        client = _make_matched_client(
            {
                ("POST", "/v2/find-similar"): _json_handler(
                    {"similar": ["https://b.com"]}
                )
            }
        )

        async def run():
            return await client.find_similar("https://a.com")

        result = asyncio.run(run())
        assert result["similar"] == ["https://b.com"]

    def test_15_batch_scrape_creates_and_polls(self):
        call_count = 0

        def _handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if request.method == "POST" and request.url.path == "/v2/batch/scrape":
                return httpx.Response(200, json={"id": "batch-1"}, request=request)
            if (
                request.method == "GET"
                and "/v2/batch/scrape/batch-1" in request.url.path
            ):
                return httpx.Response(
                    200, json={"status": "completed"}, request=request
                )
            return httpx.Response(404, json={"error": "not found"}, request=request)

        transport = httpx.MockTransport(_handler)
        client = GroktocrawlClient(base_url="http://test:8080", api_key=None)
        client._client = httpx.AsyncClient(
            base_url=client._base_url, headers=client._headers(), transport=transport
        )

        async def run():
            return await client.batch_scrape(["https://a.com", "https://b.com"])

        result = asyncio.run(run())
        assert result["status"] == "completed"

    def test_16_generate_llmstxt_creates_and_polls(self):
        call_count = 0

        def _handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if request.method == "POST" and request.url.path == "/v2/generate-llmstxt":
                return httpx.Response(200, json={"id": "llmstxt-1"}, request=request)
            if (
                request.method == "GET"
                and "/v2/generate-llmstxt/llmstxt-1" in request.url.path
            ):
                return httpx.Response(
                    200, json={"status": "completed"}, request=request
                )
            return httpx.Response(404, json={"error": "not found"}, request=request)

        transport = httpx.MockTransport(_handler)
        client = GroktocrawlClient(base_url="http://test:8080", api_key=None)
        client._client = httpx.AsyncClient(
            base_url=client._base_url, headers=client._headers(), transport=transport
        )

        async def run():
            return await client.generate_llmstxt("https://example.com")

        result = asyncio.run(run())
        assert result["status"] == "completed"

    def test_17_get_activity_verb(self):
        """Verify get_activity uses GET."""
        client = _make_matched_client(
            {("GET", "/v2/activity"): _json_handler({"jobs": 0})}
        )

        async def run():
            return await client.get_activity()

        result = asyncio.run(run())
        assert result["jobs"] == 0

    def test_18_resolve_citations_endpoint(self):
        """Verify resolve_citations hits POST /v2/citations/resolve."""
        client = _make_matched_client(
            {
                ("POST", "/v2/citations/resolve"): _json_handler(
                    {
                        "resolved_text": "See [1](https://a.com)",
                        "citations": [{"index": 1, "url": "https://a.com"}],
                        "citation_count": 1,
                        "style": "compact",
                    }
                )
            }
        )

        async def run():
            return await client.resolve_citations(
                text="See [1]",
                sources=[{"url": "https://a.com", "title": "A"}],
                style="compact",
            )

        result = asyncio.run(run())
        assert result["resolved_text"] == "See [1](https://a.com)"
        assert result["citation_count"] == 1
        assert result["style"] == "compact"


# ── New surface: agent create/cancel, batch cancel/errors, browser, monitor ──


class TestExpandedSurface:
    """Verify the expanded client surface hits the right endpoints."""

    def test_experimental_research_create_sends_idempotency_key(self):
        captured: dict[str, Any] = {}

        def _handler(request: httpx.Request) -> httpx.Response:
            captured["path"] = request.url.path
            captured["key"] = request.headers.get("idempotency-key")
            return httpx.Response(
                202,
                json={"run_id": "run-1", "state": "accepted"},
                request=request,
            )

        client = GroktocrawlClient(base_url="http://test:8080", api_key=None)
        client._client = httpx.AsyncClient(
            base_url=client._base_url,
            headers=client._headers(),
            transport=httpx.MockTransport(_handler),
        )

        result = asyncio.run(
            client.experimental_research_create("objective", "stable-key")
        )

        assert result["run_id"] == "run-1"
        assert captured == {
            "path": "/experimental/research/v1/runs",
            "key": "stable-key",
        }

    def test_experimental_research_artifact_returns_exact_bytes(self):
        client = GroktocrawlClient(base_url="http://test:8080", api_key=None)

        def _handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                content=b"# exact artifact",
                headers={"content-type": "text/markdown"},
                request=request,
            )

        client._client = httpx.AsyncClient(
            base_url=client._base_url,
            headers=client._headers(),
            transport=httpx.MockTransport(_handler),
        )
        result = asyncio.run(client.experimental_research_artifact("a-1"))

        assert result["artifact_id"] == "a-1"
        assert result["content_type"] == "text/markdown"
        assert result["content_base64"]

    def test_experimental_research_attach_posts_expected_revision(self):
        captured: dict[str, Any] = {}

        def _handler(request: httpx.Request) -> httpx.Response:
            captured["path"] = request.url.path
            captured["body"] = json.loads(request.content)
            return httpx.Response(
                200, json={"session_id": "s-1", "revision": 1}, request=request
            )

        client = GroktocrawlClient(base_url="http://test:8080", api_key=None)
        client._client = httpx.AsyncClient(
            base_url=client._base_url,
            headers=client._headers(),
            transport=httpx.MockTransport(_handler),
        )
        result = asyncio.run(client.experimental_research_attach("s-1", "run-1", 0))

        assert result["revision"] == 1
        assert captured == {
            "path": "/experimental/research/v1/sessions/s-1/attachments",
            "body": {"run_id": "run-1", "expected_revision": 0},
        }

    def test_experimental_research_capabilities_uses_unversioned_route(self):
        client = _make_matched_client(
            {
                (
                    "GET",
                    "/experimental/research/v1/capabilities",
                ): _json_handler(
                    {
                        "protocol_version": "research/1",
                        "implementation_stage": "contract_and_golden_traces",
                        "recovery_mode": "not_advertised",
                        "operations": {},
                    }
                )
            }
        )

        result = asyncio.run(client.experimental_research_capabilities())

        assert result["protocol_version"] == "research/1"

    def test_create_agent_creates_without_polling(self):
        call_count = 0

        def _handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if request.method == "POST" and request.url.path == "/v2/agent":
                return httpx.Response(
                    200, json={"success": True, "id": "agent-1"}, request=request
                )
            return httpx.Response(404, json={"error": "not found"}, request=request)

        transport = httpx.MockTransport(_handler)
        client = GroktocrawlClient(base_url="http://test:8080", api_key=None)
        client._client = httpx.AsyncClient(
            base_url=client._base_url, headers=client._headers(), transport=transport
        )

        async def run():
            return await client.create_agent("explain gravity", model="gpt-4o")

        result = asyncio.run(run())
        assert result["id"] == "agent-1"
        # create_agent must NOT poll — exactly one request
        assert call_count == 1

    def test_create_agent_passes_optional_params(self):
        captured: dict[str, Any] = {}

        def _handler(request: httpx.Request) -> httpx.Response:
            import json

            captured["body"] = json.loads(request.content or b"{}")
            return httpx.Response(
                200, json={"success": True, "id": "a1"}, request=request
            )

        transport = httpx.MockTransport(_handler)
        client = GroktocrawlClient(base_url="http://test:8080", api_key=None)
        client._client = httpx.AsyncClient(
            base_url=client._base_url, headers=client._headers(), transport=transport
        )

        async def run():
            return await client.create_agent(
                "p",
                urls=["https://a.com"],
                output_schema={"type": "object"},
                citation_style="compact",
                max_credits=3,
                include_images=True,
                force_fresh=True,
                search_type="focused",
            )

        asyncio.run(run())
        body = captured["body"]
        assert body["urls"] == ["https://a.com"]
        assert body["output_schema"] == {"type": "object"}
        assert body["citation_style"] == "compact"
        assert body["max_credits"] == 3
        assert body["include_images"] is True
        assert body["force_fresh"] is True
        assert body["search_type"] == "focused"
        assert "max_results_per_query" not in body

    def test_cancel_agent_verb(self):
        client = _make_matched_client(
            {("DELETE", "/v2/agent/agent-1"): _json_handler({"success": True})}
        )

        async def run():
            return await client.cancel_agent("agent-1")

        result = asyncio.run(run())
        assert result["success"] is True

    def test_cancel_batch_scrape_verb(self):
        client = _make_matched_client(
            {("DELETE", "/v2/batch/scrape/batch-1"): _json_handler({"success": True})}
        )

        async def run():
            return await client.cancel_batch_scrape("batch-1")

        result = asyncio.run(run())
        assert result["success"] is True

    def test_get_batch_scrape_errors_verb(self):
        client = _make_matched_client(
            {
                ("GET", "/v2/batch/scrape/batch-1/errors"): _json_handler(
                    {"errors": [{"url": "https://x.com"}]}
                )
            }
        )

        async def run():
            return await client.get_batch_scrape_errors("batch-1")

        result = asyncio.run(run())
        assert result["errors"][0]["url"] == "https://x.com"

    def test_get_active_crawls_verb(self):
        client = _make_matched_client(
            {
                ("GET", "/v2/crawl/active"): _json_handler(
                    {"data": [{"id": "c1", "status": "processing"}]}
                )
            }
        )

        async def run():
            return await client.get_active_crawls()

        result = asyncio.run(run())
        assert result["data"][0]["id"] == "c1"

    def test_browser_list_verb(self):
        client = _make_matched_client(
            {("GET", "/v2/browser"): _json_handler({"sessions": [{"id": "s1"}]})}
        )

        async def run():
            return await client.browser_list()

        result = asyncio.run(run())
        assert result["sessions"][0]["id"] == "s1"

    def test_monitor_get_verb(self):
        client = _make_matched_client(
            {
                ("GET", "/v2/monitor/m-1"): _json_handler(
                    {"id": "m-1", "monitor_type": "scrape", "schedule": "* * * * *"}
                )
            }
        )

        async def run():
            return await client.monitor_get("m-1")

        result = asyncio.run(run())
        assert result["id"] == "m-1"

    def test_monitor_update_verb(self):
        captured: dict[str, Any] = {}

        def _handler(request: httpx.Request) -> httpx.Response:
            import json

            captured["method"] = request.method
            captured["body"] = json.loads(request.content or b"{}")
            return httpx.Response(200, json={"success": True}, request=request)

        transport = httpx.MockTransport(_handler)
        client = GroktocrawlClient(base_url="http://test:8080", api_key=None)
        client._client = httpx.AsyncClient(
            base_url=client._base_url, headers=client._headers(), transport=transport
        )

        async def run():
            return await client.monitor_update("m-1", schedule="0 * * * *")

        asyncio.run(run())
        assert captured["method"] == "PATCH"
        assert captured["body"]["schedule"] == "0 * * * *"

    def test_monitor_run_verb(self):
        client = _make_matched_client(
            {
                ("POST", "/v2/monitor/m-1/run"): _json_handler(
                    {"id": "m-1", "last_result": "changed"}
                )
            }
        )

        async def run():
            return await client.monitor_run("m-1")

        result = asyncio.run(run())
        assert result["last_result"] == "changed"

    def test_monitor_create_search_config(self):
        captured: dict[str, Any] = {}

        def _handler(request: httpx.Request) -> httpx.Response:
            import json

            captured["body"] = json.loads(request.content or b"{}")
            return httpx.Response(200, json={"success": True}, request=request)

        transport = httpx.MockTransport(_handler)
        client = GroktocrawlClient(base_url="http://test:8080", api_key=None)
        client._client = httpx.AsyncClient(
            base_url=client._base_url, headers=client._headers(), transport=transport
        )

        async def run():
            return await client.monitor_create(
                monitor_type="search",
                search_config={"query": "rust", "numResults": 5},
            )

        asyncio.run(run())
        body = captured["body"]
        assert body["monitor_type"] == "search"
        assert body["search_config"]["query"] == "rust"
        assert body["search_config"]["numResults"] == 5
        assert "url" not in body


# ── 429 retry behaviour (ADR-0053 rate-limit contract) ──


class TestRateLimitRetry:
    """Bounded retry for HTTP 429 responses honoring Retry-After."""

    def test_429_retries_then_succeeds(self):
        call_count = 0

        def _handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return httpx.Response(
                    429,
                    json={"error": "Rate limit exceeded", "retry_after_seconds": 1},
                    headers={"Retry-After": "1"},
                    request=request,
                )
            return httpx.Response(
                200, json={"success": True, "data": {}}, request=request
            )

        transport = httpx.MockTransport(_handler)
        client = GroktocrawlClient(base_url="http://test:8080", api_key=None)
        client._client = httpx.AsyncClient(
            base_url=client._base_url, headers=client._headers(), transport=transport
        )

        async def run():
            return await client.scrape("https://example.com")

        result = asyncio.run(run())
        assert result.get("success") is True
        assert call_count == 2

    def test_429_exhausts_retries(self):
        call_count = 0

        def _handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return httpx.Response(
                429,
                json={"error": "Rate limit exceeded"},
                headers={"Retry-After": "1"},
                request=request,
            )

        transport = httpx.MockTransport(_handler)
        client = GroktocrawlClient(base_url="http://test:8080", api_key=None)
        client._client = httpx.AsyncClient(
            base_url=client._base_url, headers=client._headers(), transport=transport
        )

        async def run():
            return await client.search("test")

        result = asyncio.run(run())
        assert result["status_code"] == 429
        # initial + _MAX_429_RETRIES retries
        assert call_count == 3

    def test_429_retry_after_clamped(self):
        """Retry-After is clamped to the bounded max wait."""
        import groktocrawl_client

        assert groktocrawl_client._MIN_RETRY_WAIT_SECONDS >= 1.0
        assert groktocrawl_client._MAX_RETRY_WAIT_SECONDS <= 10.0


# ── SSRF guard for server-side downloads (parse tool) ─────────────


class TestParseSSRFGuard:
    """_validate_download_url blocks internal fetches (security review P1)."""

    def test_rejects_non_http_scheme(self):
        from groktocrawl_client import _validate_download_url

        with pytest.raises(ValueError):
            _validate_download_url("file:///etc/passwd")
        with pytest.raises(ValueError):
            _validate_download_url("ftp://example.com/file")

    def test_rejects_localhost_and_loopback(self):
        from groktocrawl_client import _validate_download_url

        for url in (
            "http://localhost:8000/x",
            "http://127.0.0.1:8000/x",
            "http://[::1]:8000/x",
        ):
            with pytest.raises(ValueError, match="not allowed"):
                _validate_download_url(url)

    def test_rejects_private_ip_literals(self):
        from groktocrawl_client import _validate_download_url

        for ip in ("10.0.0.1", "192.168.1.1", "172.16.0.1", "169.254.169.254"):
            with pytest.raises(ValueError, match="private"):
                _validate_download_url(f"http://{ip}/x")

    def test_rejects_hostname_resolving_to_private_ip(self, monkeypatch):
        from groktocrawl_client import _validate_download_url

        def _fake_getaddrinfo(host, *args, **kwargs):
            return [(2, 1, 6, "", ("10.0.0.5", 0))]

        monkeypatch.setattr("groktocrawl_client.socket.getaddrinfo", _fake_getaddrinfo)
        with pytest.raises(ValueError, match="private"):
            _validate_download_url("http://internal.example.com/x")

    def test_accepts_public_ip_literal(self):
        from groktocrawl_client import _validate_download_url

        assert _validate_download_url("http://93.184.216.34/x") == (
            "http://93.184.216.34/x"
        )

    def test_accepts_public_hostname(self, monkeypatch):
        from groktocrawl_client import _validate_download_url

        def _fake_getaddrinfo(host, *args, **kwargs):
            return [(2, 1, 6, "", ("93.184.216.34", 0))]

        monkeypatch.setattr("groktocrawl_client.socket.getaddrinfo", _fake_getaddrinfo)
        assert _validate_download_url("https://example.com/doc.pdf") == (
            "https://example.com/doc.pdf"
        )


class TestParseDownload:
    """parse() download path: redirect re-validation and happy path."""

    class _FakeResponse:
        def __init__(
            self, status_code: int, headers: dict | None = None, content: bytes = b""
        ):
            self.status_code = status_code
            self.headers = headers or {}
            self._content = content

        def raise_for_status(self):
            if self.status_code >= 400:
                req = httpx.Request("GET", "http://placeholder")
                raise httpx.HTTPStatusError(
                    f"HTTP {self.status_code}",
                    request=req,
                    response=httpx.Response(self.status_code, request=req),
                )

        async def aiter_bytes(self):
            for i in range(0, len(self._content), 1024):
                yield self._content[i : i + 1024]

    class _FakeClient:
        def __init__(self, handler):
            self._handler = handler
            self.headers: dict[str, str] = {}
            self.closed = False

        async def get(self, url):
            return self._handler(url)

        async def aclose(self):
            self.closed = True

    def _patch_download_client(self, monkeypatch, handler):
        import groktocrawl_client

        monkeypatch.setattr(
            groktocrawl_client.httpx,
            "AsyncClient",
            lambda *args, **kwargs: self._FakeClient(handler),
        )

    def _set_shared_client(self, client):
        """Wire the shared client (upload path) BEFORE patching AsyncClient.

        parse() uses ``httpx.AsyncClient`` for both the shared client and
        the download client, so the shared client must exist before the
        download client is faked.
        """
        client._client = httpx.AsyncClient(
            base_url=client._base_url,
            headers=client._headers(),
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    json={"success": True, "data": {"markdown": "# parsed"}},
                    request=request,
                )
            ),
        )

    def test_parse_rejects_private_url_without_network(self, monkeypatch):
        client = GroktocrawlClient(base_url="http://test:8080", api_key=None)
        self._set_shared_client(client)

        def _handler(url):
            raise AssertionError(f"must not download: {url}")

        self._patch_download_client(monkeypatch, _handler)

        async def run():
            return await client.parse("http://10.0.0.1/secret")

        result = asyncio.run(run())
        assert "private" in result["error"]

    def test_parse_blocks_redirect_to_private_host(self, monkeypatch):
        client = GroktocrawlClient(base_url="http://test:8080", api_key=None)
        self._set_shared_client(client)

        def _fake_getaddrinfo(host, *args, **kwargs):
            return [(2, 1, 6, "", ("93.184.216.34", 0))]

        monkeypatch.setattr("groktocrawl_client.socket.getaddrinfo", _fake_getaddrinfo)

        def _handler(url):
            # First hop (public) redirects to an internal host.
            return self._FakeResponse(302, {"location": "http://10.0.0.1/secret"})

        self._patch_download_client(monkeypatch, _handler)

        async def run():
            return await client.parse("https://example.com/doc.pdf")

        result = asyncio.run(run())
        assert "private" in result["error"]

    def test_parse_happy_path(self, monkeypatch):
        client = GroktocrawlClient(base_url="http://test:8080", api_key=None)
        self._set_shared_client(client)

        def _fake_getaddrinfo(host, *args, **kwargs):
            return [(2, 1, 6, "", ("93.184.216.34", 0))]

        monkeypatch.setattr("groktocrawl_client.socket.getaddrinfo", _fake_getaddrinfo)

        def _handler(url):
            return self._FakeResponse(
                200,
                {"content-type": "application/pdf"},
                b"%PDF-1.4 fake content",
            )

        self._patch_download_client(monkeypatch, _handler)

        async def run():
            return await client.parse("https://example.com/doc.pdf")

        result = asyncio.run(run())
        assert result["success"] is True
        assert result["data"]["markdown"] == "# parsed"

    def test_parse_rejects_oversized_download(self, monkeypatch):
        import groktocrawl_client

        client = GroktocrawlClient(base_url="http://test:8080", api_key=None)
        self._set_shared_client(client)

        def _fake_getaddrinfo(host, *args, **kwargs):
            return [(2, 1, 6, "", ("93.184.216.34", 0))]

        monkeypatch.setattr("groktocrawl_client.socket.getaddrinfo", _fake_getaddrinfo)

        def _handler(url):
            return self._FakeResponse(
                200,
                {"content-type": "application/pdf"},
                b"x" * (groktocrawl_client._MAX_DOWNLOAD_BYTES + 1024),
            )

        self._patch_download_client(monkeypatch, _handler)

        async def run():
            return await client.parse("https://example.com/huge.pdf")

        result = asyncio.run(run())
        assert "exceeds" in result["error"]


# ── VAL-MCP-K04: Recovery after transient outage ──


class TestRecoveryAfterOutage:
    """VAL-MCP-K04: agent-svc recovery after transient outage."""

    def test_recovery_after_connect_error(self):
        """After a ConnectError, subsequent requests succeed without restart."""
        failures = 0

        def _flaky(request: httpx.Request) -> httpx.Response:
            nonlocal failures
            if failures < 2:
                failures += 1
                raise httpx.ConnectError("Connection refused", request=request)
            return httpx.Response(200, json={"success": True}, request=request)

        transport = httpx.MockTransport(_flaky)
        client = GroktocrawlClient(
            base_url="http://test:8080",
            api_key=None,
            default_timeout=5.0,
        )
        client._client = httpx.AsyncClient(
            base_url=client._base_url,
            headers=client._headers(),
            transport=transport,
        )

        async def run():
            r1 = await client.scrape("https://example.com")
            r2 = await client.scrape("https://example.com")
            r3 = await client.scrape("https://example.com")
            return r1, r2, r3

        r1, r2, r3 = asyncio.run(run())
        assert "error" in r1
        assert "connection" in r1["error"].lower()
        assert "error" in r2
        assert "connection" in r2["error"].lower()
        assert r3.get("success") is True
        assert failures == 2


# ── URL construction / lifecycle ──


class TestURLConstruction:
    def test_trailing_slash_stripped(self):
        client = GroktocrawlClient(
            base_url="http://deployment.example.internal:8080/", api_key=None
        )
        assert client._base_url == "http://deployment.example.internal:8080"

    def test_subpath_in_base_url(self):
        client = GroktocrawlClient(
            base_url="http://deployment.example.internal:8080/api", api_key=None
        )
        assert client._base_url == "http://deployment.example.internal:8080/api"


class TestLifecycle:
    def test_context_manager_closes_client(self):
        async def run():
            async with GroktocrawlClient.from_env() as client:
                client._client = httpx.AsyncClient(
                    base_url=client._base_url, headers=client._headers()
                )
                return client._client is not None

        was_open = asyncio.run(run())
        assert was_open is True


def test_search_preserves_bounded_media_payload():
    media = {"media_type": "image", "url": "https://example.org/full.jpg", "width": 640}
    row = {
        "url": "https://example.org/page",
        "media": media,
        "engines": ["brave", "images"],
    }
    client = _make_matched_client(
        {
            ("POST", "/v2/search"): _json_handler(
                {"success": True, "data": {"web": [row]}}
            )
        }
    )
    result = asyncio.run(client.search("fixture"))
    assert result["data"]["web"][0] == row


@pytest.mark.asyncio
async def test_document_client_bytes_and_scoped_metadata_workflow():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            200, json={"ref_id": "doc_fixture", "markdown": "Private source"}
        )

    client = GroktocrawlClient(base_url="http://fixture", api_key="fixture-key")
    client._client = httpx.AsyncClient(
        base_url="http://fixture",
        headers=client._headers(),
        transport=httpx.MockTransport(handler),
    )
    try:
        await client.document_attach(
            "session",
            "report.pdf",
            base64.b64encode(b"private file bytes").decode(),
            media_type="application/pdf",
        )
        assert calls[-1].headers["Authorization"] == "Bearer fixture-key"
        assert b'filename="report.pdf"' in calls[-1].content
        assert b"private file bytes" in calls[-1].content
        await client.document_list("session")
        assert calls[-1].method == "GET"
        await client.document_read("session", "doc_fixture", 4, 16)
        assert calls[-1].url.params == httpx.QueryParams({"start": 4, "end": 16})
        await client.session_query(
            "session",
            "Compare documents and web",
            ["doc_fixture", "ref_1_2"],
            evidence_budget_chars=32000,
            model="fixture",
        )
        assert json.loads(calls[-1].content)["params"] == {
            "question": "Compare documents and web",
            "ref_ids": ["doc_fixture", "ref_1_2"],
            "evidence_budget_chars": 32000,
            "model": "fixture",
        }
        await client.document_detach("session", "doc_fixture")
        assert calls[-1].method == "DELETE"
        await client.session_delete("session")
        assert calls[-1].url.path == "/v2/session/session"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_invalid_document_bytes_do_not_send_network_requests():
    client = GroktocrawlClient(base_url="http://fixture")
    calls = []
    client._client = httpx.AsyncClient(
        base_url="http://fixture",
        transport=httpx.MockTransport(lambda request: calls.append(request)),
    )
    try:
        with pytest.raises(ValueError):
            await client.document_attach("session", "report.txt", "%%%")
        with pytest.raises(ValueError, match="exactly one"):
            await client.document_attach("session", "report.txt", "dGV4dA==", "upload")
        assert calls == []
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_staged_attachment_does_not_read_remote_url_or_local_path():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"ref_id": "doc_fixture"})

    client = GroktocrawlClient(base_url="http://fixture")
    client._client = httpx.AsyncClient(
        base_url="http://fixture", transport=httpx.MockTransport(handler)
    )
    try:
        await client.document_attach("session", "unused", upload_id="scoped-upload")
        assert calls[0].url.host == "fixture"
        assert calls[0].content == b"upload_id=scoped-upload"
        assert len(calls) == 1
    finally:
        await client.close()


def test_workspace_client_lists_selects_and_resumes_exact_revision():
    captured = []

    def handler(request):
        captured.append(
            (
                request.method,
                request.url.path,
                json.loads(request.content) if request.content else None,
            )
        )
        return httpx.Response(
            200,
            json={"revision": "observed", "markdown": "exact evidence"},
            request=request,
        )

    client = GroktocrawlClient(base_url="http://test:8080", api_key=None)
    client._client = httpx.AsyncClient(
        base_url=client._base_url, transport=httpx.MockTransport(handler)
    )

    async def exercise():
        await client.experimental_research_workspace()
        await client.experimental_research_workspace("root-1")
        await client.experimental_research_resume(
            "root-1", "render", expected_revision="observed"
        )

    asyncio.run(exercise())
    assert captured == [
        ("GET", "/experimental/research/v1/workspace", None),
        ("GET", "/experimental/research/v1/workspace/root-1", None),
        (
            "POST",
            "/experimental/research/v1/workspace/root-1/actions",
            {"action": "render", "expected_revision": "observed"},
        ),
    ]


@pytest.mark.asyncio
async def test_search_preserves_bounded_date_contract():
    captured = []

    def handler(request):
        captured.append(json.loads(request.content))
        return httpx.Response(
            200, json={"success": True, "date_filter": {"status": "enforced"}}
        )

    client = _make_matched_client({("POST", "/v2/search"): handler})
    try:
        result = await client.search(
            "paper", date_from="2026-01-01", date_to="2026-01-31", engines=["openalex"]
        )
        assert captured[0]["date_from"] == "2026-01-01"
        assert captured[0]["date_to"] == "2026-01-31"
        assert captured[0]["engines"] == ["openalex"]
        assert result["date_filter"]["status"] == "enforced"
    finally:
        await client.close()
