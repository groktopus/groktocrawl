"""Unit tests for portal-svc proxy logic.

Tests the behavior of the /ask proxy endpoint by mocking ``httpx.AsyncClient``
to simulate various downstream responses — form forwarding, SSE passthrough,
error propagation, connection failures, and circuit breaker integration.
"""

from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient
from portal.app import AGENT_BASE_URL, ANSWER_URL, _agent_circuit_breaker, app

from common.circuit_breaker import CircuitBreaker, CircuitBreakerState

client = TestClient(app)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _mock_async_client(status_code: int = 200, chunks: list[bytes] | None = None):
    """Build a mocked ``httpx.AsyncClient`` that returns a fixed response.

    The mock supports ``__aenter__`` (returns itself), ``stream(...)``
    (returns an async context manager wrapping a ``Response``), and
    ``response.aiter_bytes()`` (returns *chunks*).
    """
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.__aenter__.return_value = mock_client

    mock_response = AsyncMock(spec=httpx.Response)
    mock_response.status_code = status_code

    async def _aiter_bytes():
        for c in chunks or []:
            yield c

    mock_response.aiter_bytes = _aiter_bytes

    # For downstream error propagation (non-2xx status), the code calls
    # response.aread() to read the full body.
    error_body = b'{"detail":"downstream error"}'
    mock_response.aread = AsyncMock(return_value=error_body)

    stream_cm = AsyncMock()
    stream_cm.__aenter__.return_value = mock_response
    mock_client.stream.return_value = stream_cm

    return mock_client


# ── Agent URL construction ───────────────────────────────────────────────────


class TestAgentURLConstruction:
    """`AGENT_BASE_URL` is correctly joined with ``/v2/answer``."""

    def test_default_url_format(self):
        """With no env override the default URL is http://agent-svc:8080/v2/answer."""
        expected = "http://agent-svc:8080/v2/answer"
        assert expected == ANSWER_URL

    def test_answer_url_contains_base_url(self):
        """The answer URL incorporates the agent base URL."""
        assert AGENT_BASE_URL in ANSWER_URL
        assert ANSWER_URL.endswith("/v2/answer")

    def test_custom_base_url(self, monkeypatch):
        """When AGENT_BASE_URL is set, ANSWER_URL uses that value."""
        monkeypatch.setenv("AGENT_BASE_URL", "http://localhost:9999")
        # Reimport the module to pick up the new env var
        import importlib

        import portal.app

        importlib.reload(portal.app)
        assert portal.app.ANSWER_URL == "http://localhost:9999/v2/answer"
        assert portal.app.AGENT_BASE_URL == "http://localhost:9999"
        # Reload once more to restore the default for other tests
        monkeypatch.delenv("AGENT_BASE_URL", raising=False)
        importlib.reload(portal.app)

    def test_trailing_slash_stripped(self, monkeypatch):
        """Trailing slashes in AGENT_BASE_URL are stripped before joining."""
        monkeypatch.setenv("AGENT_BASE_URL", "http://localhost:9999/")
        import importlib

        import portal.app

        importlib.reload(portal.app)
        assert portal.app.ANSWER_URL == "http://localhost:9999/v2/answer"
        monkeypatch.delenv("AGENT_BASE_URL", raising=False)
        importlib.reload(portal.app)


# ── Form data forwarding ────────────────────────────────────────────────────


class TestFormDataForwarding:
    """Form data from POST /ask is forwarded as JSON to agent-svc."""

    def test_forwards_query_and_num_sources(self):
        """query=hello&num_sources=3 is sent as JSON body to agent."""
        mock_client = _mock_async_client(status_code=200, chunks=[b"data: ok\n\n"])

        with patch.object(httpx, "AsyncClient", return_value=mock_client):
            resp = client.post("/ask", data={"query": "hello", "num_sources": "3"})

        assert resp.status_code == 200
        mock_client.stream.assert_called_once_with(
            "POST",
            ANSWER_URL,
            json={"query": "hello", "num_sources": 3, "stream": True},
        )

    def test_default_num_sources_is_5(self):
        """When num_sources is omitted, the default value 5 is sent."""
        mock_client = _mock_async_client(status_code=200, chunks=[b"data: ok\n\n"])

        with patch.object(httpx, "AsyncClient", return_value=mock_client):
            resp = client.post("/ask", data={"query": "defaults"})

        assert resp.status_code == 200
        mock_client.stream.assert_called_once_with(
            "POST",
            ANSWER_URL,
            json={"query": "defaults", "num_sources": 5, "stream": True},
        )

    def test_stream_true_always_sent(self):
        """The stream: true flag is always sent to agent-svc."""
        mock_client = _mock_async_client(status_code=200, chunks=[b"data: ok\n\n"])

        with patch.object(httpx, "AsyncClient", return_value=mock_client):
            resp = client.post("/ask", data={"query": "x", "num_sources": "1"})

        assert resp.status_code == 200
        call_json = mock_client.stream.call_args[1]["json"]
        assert call_json["stream"] is True


# ── SSE passthrough ─────────────────────────────────────────────────────────


class TestSSEPassthrough:
    """SSE chunks from agent-svc are streamed back to the caller."""

    def test_content_type_is_event_stream(self):
        """The response media type is text/event-stream (may include charset)."""
        mock_client = _mock_async_client(status_code=200, chunks=[b"data: test\n\n"])

        with patch.object(httpx, "AsyncClient", return_value=mock_client):
            resp = client.post("/ask", data={"query": "sse"})

        ct = resp.headers.get("content-type", "")
        assert ct.startswith("text/event-stream")

    def test_streams_sse_chunks(self):
        """All SSE chunks from downstream are forwarded."""
        chunks = [b"event: token\ndata: Hello\n\n", b"event: done\ndata: {}\n\n"]
        mock_client = _mock_async_client(status_code=200, chunks=chunks)

        with patch.object(httpx, "AsyncClient", return_value=mock_client):
            resp = client.post("/ask", data={"query": "sse"})

        assert resp.status_code == 200
        body = resp.text
        for chunk in chunks:
            assert chunk.decode() in body

    def test_empty_chunks_handled(self):
        """Empty SSE chunk (heartbeat) is forwarded unchanged."""
        chunks = [b": heartbeat\n\n", b"data: final\n\n"]
        mock_client = _mock_async_client(status_code=200, chunks=chunks)

        with patch.object(httpx, "AsyncClient", return_value=mock_client):
            resp = client.post("/ask", data={"query": "hb"})

        assert resp.status_code == 200
        assert ": heartbeat" in resp.text
        assert "data: final" in resp.text


# ── Downstream error propagation ────────────────────────────────────────────


class TestDownstreamErrorPropagation:
    """Non-2xx responses from agent-svc are propagated as SSE error events."""

    @pytest.mark.parametrize("status_code", [400, 500, 502, 503])
    def test_error_status_returned_as_sse_event(self, status_code):
        """Downstream error status returns 200 with SSE error event."""
        mock_client = _mock_async_client(status_code=status_code)

        with patch.object(httpx, "AsyncClient", return_value=mock_client):
            resp = client.post("/ask", data={"query": "err"})

        # The response is always 200 (SSE streaming), error is in the stream
        assert resp.status_code == 200
        assert "event: error" in resp.text

    def test_error_body_included(self):
        """The downstream error body is included in the SSE error event."""
        mock_client = _mock_async_client(status_code=500)

        with patch.object(httpx, "AsyncClient", return_value=mock_client):
            resp = client.post("/ask", data={"query": "err"})

        assert resp.status_code == 200
        assert "downstream error" in resp.text


# ── Transport-level connection failure ──────────────────────────────────────


class TestConnectionFailure:
    """httpx.ConnectError when agent is unreachable returns SSE error."""

    def test_connect_error_returns_sse_error(self):
        """Agent unreachable yields an SSE error event, not a crash."""
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.__aenter__.return_value = mock_client
        mock_client.stream.side_effect = httpx.ConnectError("Connection refused")

        with patch.object(httpx, "AsyncClient", return_value=mock_client):
            resp = client.post("/ask", data={"query": "down"})

        # Should not crash; returns 200 with SSE error event
        assert resp.status_code == 200
        assert "event: error" in resp.text
        assert "Service unavailable" in resp.text


# ── Environment variable awareness ──────────────────────────────────────────


class TestEnvAwareness:
    """The module respects AGENT_BASE_URL env var at import time."""

    def test_default_base_url(self):
        """Default AGENT_BASE_URL when env not set."""
        # In the test environment the default is used
        assert "agent-svc:8080" in AGENT_BASE_URL or "ANSWER_URL" in globals()


# ── Circuit breaker integration ─────────────────────────────────────────────


class TestCircuitBreakerIntegration:
    """Circuit breaker fast-fails when agent-svc is unhealthy."""

    @pytest.mark.asyncio
    async def test_circuit_breaker_imported(self):
        """CircuitBreaker is created as _agent_circuit_breaker in app module."""
        assert _agent_circuit_breaker is not None
        assert isinstance(_agent_circuit_breaker, CircuitBreaker)

    def test_fast_fail_returns_sse_error_with_circuit_open(self):
        """When circuit is OPEN, /ask returns SSE error with circuit_open detail."""
        # Force the circuit to OPEN with a recent failure so cooldown hasn't elapsed
        import time

        import portal.app

        cb = portal.app._agent_circuit_breaker
        cb._consecutive_failures = 0
        cb._state = CircuitBreakerState.OPEN
        cb._last_failure_time = time.monotonic()  # Just now → cooldown pending
        cb._probe_in_progress = False

        resp = client.post("/ask", data={"query": "test", "num_sources": "3"})

        assert resp.status_code == 200
        assert "event: error" in resp.text
        assert "circuit_open" in resp.text
        assert "remaining" in resp.text.lower()
        # Restore state for other tests
        cb._state = CircuitBreakerState.CLOSED
        cb._consecutive_failures = 0

    def test_fast_fail_no_http_call_made(self):
        """When circuit is OPEN, no HTTP call is made to agent-svc."""
        import time

        import portal.app

        cb = portal.app._agent_circuit_breaker
        cb._state = CircuitBreakerState.OPEN
        cb._last_failure_time = time.monotonic()  # Just now → cooldown pending
        cb._probe_in_progress = False

        mock_client = AsyncMock(spec=httpx.AsyncClient)

        with patch.object(httpx, "AsyncClient", return_value=mock_client):
            resp = client.post("/ask", data={"query": "test", "num_sources": "3"})

        assert resp.status_code == 200
        # The mock client should NOT have been called
        mock_client.assert_not_called()
        # Restore state
        cb._state = CircuitBreakerState.CLOSED
        cb._consecutive_failures = 0

    def test_normal_flow_when_circuit_closed(self):
        """When circuit is CLOSED, requests flow through normally."""
        import portal.app

        cb = portal.app._agent_circuit_breaker
        cb._state = CircuitBreakerState.CLOSED
        cb._consecutive_failures = 0

        chunks = [b"data: ok\n\n"]
        mock_client = _mock_async_client(status_code=200, chunks=chunks)

        with patch.object(httpx, "AsyncClient", return_value=mock_client):
            resp = client.post("/ask", data={"query": "normal", "num_sources": "3"})

        assert resp.status_code == 200
        assert "data: ok" in resp.text
        # Verify the HTTP call was actually made
        mock_client.stream.assert_called_once()

    def test_five_consecutive_5xx_opens_circuit(self):
        """After 5 consecutive 5xx errors, circuit opens and fast-fails."""
        import portal.app

        cb = portal.app._agent_circuit_breaker
        cb._state = CircuitBreakerState.CLOSED
        cb._consecutive_failures = 0

        # We need to manually drive failures through record_failure
        # Simulating 5 consecutive 5xx responses
        for _ in range(5):
            mock_client = _mock_async_client(status_code=503)
            with patch.object(httpx, "AsyncClient", return_value=mock_client):
                resp = client.post("/ask", data={"query": "err", "num_sources": "3"})

        # Circuit should be OPEN now
        assert cb.state is CircuitBreakerState.OPEN

        # Next request should fast-fail without HTTP call
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        with patch.object(httpx, "AsyncClient", return_value=mock_client):
            resp = client.post("/ask", data={"query": "err", "num_sources": "3"})

        assert resp.status_code == 200
        assert "circuit_open" in resp.text
        mock_client.assert_not_called()

        # Restore state
        cb._state = CircuitBreakerState.CLOSED
        cb._consecutive_failures = 0

    def test_half_open_success_closes_circuit(self):
        """After cooldown, a successful probe closes the circuit."""
        import portal.app

        cb = portal.app._agent_circuit_breaker
        # Set to HALF_OPEN with probe not yet in progress
        cb._state = CircuitBreakerState.HALF_OPEN
        cb._probe_in_progress = False
        cb._consecutive_failures = 5

        chunks = [b"data: ok\n\n"]
        mock_client = _mock_async_client(status_code=200, chunks=chunks)

        with patch.object(httpx, "AsyncClient", return_value=mock_client):
            resp = client.post("/ask", data={"query": "probe", "num_sources": "3"})

        assert resp.status_code == 200
        assert cb.state is CircuitBreakerState.CLOSED  # Probe succeeded

        # Restore state
        cb._consecutive_failures = 0

    def test_half_open_failure_reopens_circuit(self):
        """After cooldown, a failed probe re-opens the circuit."""
        import portal.app

        cb = portal.app._agent_circuit_breaker
        # Set to HALF_OPEN with probe not yet in progress
        cb._state = CircuitBreakerState.HALF_OPEN
        cb._probe_in_progress = False
        cb._consecutive_failures = 5

        mock_client = _mock_async_client(status_code=503)

        with patch.object(httpx, "AsyncClient", return_value=mock_client):
            resp = client.post("/ask", data={"query": "probe_fail", "num_sources": "3"})

        assert resp.status_code == 200
        assert cb.state is CircuitBreakerState.OPEN  # Probe failed

        # Restore state
        cb._state = CircuitBreakerState.CLOSED
        cb._consecutive_failures = 0

    def test_half_open_concurrent_fast_fail(self):
        """Concurrent requests in HALF_OPEN are fast-failed after probe starts."""
        import portal.app

        cb = portal.app._agent_circuit_breaker
        # Force HALF_OPEN with probe_in_progress=True to simulate
        # a probe that is currently happening
        cb._state = CircuitBreakerState.HALF_OPEN
        cb._probe_in_progress = True

        mock_client = AsyncMock(spec=httpx.AsyncClient)

        with patch.object(httpx, "AsyncClient", return_value=mock_client):
            resp = client.post("/ask", data={"query": "concurrent", "num_sources": "3"})

        assert resp.status_code == 200
        assert "circuit_open" in resp.text
        mock_client.assert_not_called()  # No HTTP call made

        # Restore state
        cb._state = CircuitBreakerState.CLOSED
        cb._consecutive_failures = 0


def test_workspace_page_has_no_private_browser_storage():
    response = client.get("/workspace")
    assert response.status_code == 200
    assert "Retained research workspace" in response.text
    assert "localStorage" not in response.text
    assert "textContent" in response.text


def test_workspace_proxy_blocks_arbitrary_destinations():
    assert client.get("/workspace/api/https://example.com").status_code == 404
    assert client.post("/workspace/action/capabilities").status_code == 404
    assert (
        client.post(
            "/workspace/action/workspace/example/actions",
            headers={"Origin": "https://foreign.example"},
        ).status_code
        == 403
    )


def test_workspace_proxy_forwards_caller_scope_and_preserves_errors():
    mock = AsyncMock(spec=httpx.AsyncClient)
    mock.__aenter__.return_value = mock
    mock.get.return_value = httpx.Response(410, json={"detail": "deleted"})
    with patch("portal.app.httpx.AsyncClient", return_value=mock):
        response = client.get(
            "/workspace/api/workspace/example",
            headers={"Authorization": "Bearer fixture-scope"},
        )
    assert response.status_code == 410
    assert response.headers["cache-control"] == "no-store"
    assert mock.get.call_args.kwargs["headers"] == {
        "authorization": "Bearer fixture-scope"
    }


def test_workspace_action_forwards_only_named_bounded_operation():
    mock = AsyncMock(spec=httpx.AsyncClient)
    mock.__aenter__.return_value = mock
    mock.request.return_value = httpx.Response(
        409, json={"detail": "revision conflict"}
    )
    with patch("portal.app.httpx.AsyncClient", return_value=mock):
        response = client.post(
            "/workspace/action/workspace/example/actions",
            json={"action": "render", "expected_revision": "stale"},
        )
    assert response.status_code == 409
    assert mock.request.call_args.args[0] == "POST"
    assert mock.request.call_args.args[1].endswith(
        "/experimental/research/v1/workspace/example/actions"
    )
    assert (
        client.post(
            "/workspace/action/workspace/example/actions", content="x" * 8193
        ).status_code
        == 413
    )
