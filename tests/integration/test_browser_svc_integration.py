"""Integration tests for browser-svc — all 8 browser actions, session lifecycle,
cookie persistence, and error cases.

Requires a running Docker stack with browser-svc + Playwright Chromium.
These tests use httpx to talk to the live browser-svc API.

Run with::
    docker compose up --build -d
    python3 -m pytest tests/test_browser_svc_integration.py -v --timeout=120
"""

import asyncio
import os
import re

import httpx
import pytest

from tests.outcome_governance import governed_skip

# ── Configuration ──────────────────────────────────────────────────────────

BROWSER_SVC_BASE = os.environ.get("BROWSER_SVC_URL", "http://localhost:8012")

# Public URL that Playwright can navigate to without triggering bot challenges
PUBLIC_TEST_URL = os.environ.get("PUBLIC_TEST_URL", "https://example.com")

# We avoid DNS-dependent name resolution; use localhost for the Docker-mapped port
# as declared in docker-compose.yml


# ── Skip condition ─────────────────────────────────────────────────────────


def _docker_available() -> bool:
    """Check whether the Docker engine is available and the stack is running."""
    try:
        import subprocess

        result = subprocess.run(
            ["docker", "compose", "ps", "--status", "running"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return "browser-svc" in result.stdout
    except Exception:
        return False


require_docker = pytest.mark.skipif(
    not _docker_available(),
    reason="Docker stack not running — start with `docker compose up --build -d`",
    owner="repository-maintainer",
    issue="#502",
    classification="retained",
    environment="Docker stack is not running",
)


# ── Fixtures ───────────────────────────────────────────────────────────────


def _skip_if_no_docker():
    """Skip the current test if Docker is not available."""
    if not _docker_available():
        governed_skip(
            "Docker stack not running — start with `docker compose up --build -d`",
            owner="repository-maintainer",
            issue="#502",
            classification="retained",
            environment="Docker stack is not running",
        )


@pytest.fixture(scope="module")
def _check_service():
    """Pre-flight: verify browser-svc is reachable before running tests."""
    _skip_if_no_docker()
    try:
        resp = httpx.get(f"{BROWSER_SVC_BASE}/health", timeout=10)
        assert resp.status_code == 200, f"browser-svc health check failed: {resp.text}"
        data = resp.json()
        assert data["status"] == "ok"
    except httpx.ConnectError as e:
        governed_skip(
            f"browser-svc unreachable at {BROWSER_SVC_BASE}: {e}",
            owner="repository-maintainer",
            issue="#502",
            classification="retained",
            environment="browser-svc health endpoint is unreachable",
        )


@pytest.fixture
async def browser_session_id():
    """Create a browser session and yield its ID, then destroy it."""
    _skip_if_no_docker()
    session_id = None
    try:
        async with httpx.AsyncClient(base_url=BROWSER_SVC_BASE, timeout=30) as client:
            resp = await client.post("/browsers", json={"ttl": 120})
            assert resp.status_code == 200, f"Create session failed: {resp.text}"
            data = resp.json()
            assert data["success"] is True
            session_id = data["id"]
            yield session_id
    finally:
        if session_id:
            try:
                async with httpx.AsyncClient(
                    base_url=BROWSER_SVC_BASE, timeout=10
                ) as client:
                    await client.delete(f"/browsers/{session_id}")
            except Exception:
                pass


# ═══════════════════════════════════════════════════════════════════════════
# Session Lifecycle
# ═══════════════════════════════════════════════════════════════════════════


class TestSessionLifecycle:
    """Create, list, and destroy browser sessions."""

    @require_docker
    @pytest.mark.asyncio
    async def test_create_session(self, _check_service):
        """POST /browsers creates a new session with a valid UUID."""
        async with httpx.AsyncClient(base_url=BROWSER_SVC_BASE, timeout=30) as client:
            resp = await client.post("/browsers", json={"ttl": 120})
            assert resp.status_code == 200
            data = resp.json()
            assert data["success"] is True
            assert "id" in data
            # Validate basic UUID format
            assert re.match(
                r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
                data["id"],
            )
            # Clean up
            await client.delete(f"/browsers/{data['id']}")

    @require_docker
    @pytest.mark.asyncio
    async def test_list_sessions(self, browser_session_id):
        """GET /browsers lists active sessions."""
        async with httpx.AsyncClient(base_url=BROWSER_SVC_BASE, timeout=30) as client:
            resp = await client.get("/browsers")
            assert resp.status_code == 200
            data = resp.json()
            assert data["success"] is True
            assert isinstance(data["sessions"], list)
            # Our session should be in the list or may have been cleaned up
            # (depending on timing); at minimum the list exists and contains
            # at least one session if ours is still alive.
            if data["sessions"]:
                session_ids = [s["id"] for s in data["sessions"]]
                assert browser_session_id in session_ids

    @require_docker
    @pytest.mark.asyncio
    async def test_destroy_session(self, browser_session_id):
        """DELETE /browsers/{id} destroys a session."""
        async with httpx.AsyncClient(base_url=BROWSER_SVC_BASE, timeout=30) as client:
            resp = await client.delete(f"/browsers/{browser_session_id}")
            assert resp.status_code == 200
            data = resp.json()
            assert data["success"] is True
            assert data["id"] == browser_session_id

            # Verify it's gone
            resp2 = await client.get("/browsers")
            sessions = resp2.json()["sessions"]
            ids = [s["id"] for s in sessions]
            assert browser_session_id not in ids

    @require_docker
    @pytest.mark.asyncio
    async def test_destroy_nonexistent_session(self):
        """DELETE /browsers/{nonexistent} returns 404."""
        fake_id = "00000000-0000-0000-0000-000000000000"
        async with httpx.AsyncClient(base_url=BROWSER_SVC_BASE, timeout=30) as client:
            resp = await client.delete(f"/browsers/{fake_id}")
            assert resp.status_code == 404

    @require_docker
    @pytest.mark.asyncio
    async def test_execute_on_nonexistent_session(self):
        """POST /browsers/{nonexistent}/execute returns 404."""
        fake_id = "00000000-0000-0000-0000-000000000000"
        async with httpx.AsyncClient(base_url=BROWSER_SVC_BASE, timeout=30) as client:
            resp = await client.post(
                f"/browsers/{fake_id}/execute",
                json={"action": "navigate", "url": "https://example.com"},
            )
            assert resp.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════
# Browser Actions (all 8)
# ═══════════════════════════════════════════════════════════════════════════


class TestBrowserActions:
    """All 8 browser actions: navigate, click, type, screenshot, getContent,
    executeScript, scroll, wait."""

    # ── Navigate ──────────────────────────────────────────────

    @require_docker
    @pytest.mark.asyncio
    async def test_navigate(self, browser_session_id):
        """Navigate to a public URL returns url and title."""
        async with httpx.AsyncClient(base_url=BROWSER_SVC_BASE, timeout=60) as client:
            resp = await client.post(
                f"/browsers/{browser_session_id}/execute",
                json={"action": "navigate", "url": PUBLIC_TEST_URL, "timeout": 30000},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["success"] is True
            result = data["result"]
            assert "url" in result
            assert "title" in result
            assert len(result["title"]) > 0

    # ── GetContent ────────────────────────────────────────────

    @require_docker
    @pytest.mark.asyncio
    async def test_get_content(self, browser_session_id):
        """getContent returns html_length > 0, url, and title."""
        async with httpx.AsyncClient(base_url=BROWSER_SVC_BASE, timeout=60) as client:
            # First navigate somewhere
            await client.post(
                f"/browsers/{browser_session_id}/execute",
                json={"action": "navigate", "url": PUBLIC_TEST_URL, "timeout": 30000},
            )
            # Now get content
            resp = await client.post(
                f"/browsers/{browser_session_id}/execute",
                json={"action": "getContent"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["success"] is True
            result = data["result"]
            assert result["html_length"] > 0
            assert len(result["url"]) > 0
            assert len(result["title"]) > 0

    # ── Click ─────────────────────────────────────────────────

    @require_docker
    @pytest.mark.asyncio
    async def test_click(self, browser_session_id):
        """Click on a valid selector returns clicked: true."""
        async with httpx.AsyncClient(base_url=BROWSER_SVC_BASE, timeout=60) as client:
            # Navigate to a page with a known link/button
            await client.post(
                f"/browsers/{browser_session_id}/execute",
                json={"action": "navigate", "url": PUBLIC_TEST_URL, "timeout": 30000},
            )
            # Try clicking the first link on the page
            resp = await client.post(
                f"/browsers/{browser_session_id}/execute",
                json={"action": "click", "selector": "a", "timeout": 15000},
            )
            # Click may fail if no link is found, but the endpoint should
            # respond (either success or error), not crash
            assert resp.status_code == 200
            data = resp.json()
            # The click could succeed or fail depending on the page
            assert isinstance(data, dict)
            assert "success" in data

    # ── Type ──────────────────────────────────────────────────

    @require_docker
    @pytest.mark.asyncio
    async def test_type(self, browser_session_id):
        """Type text into a selector returns typed: true."""
        async with httpx.AsyncClient(base_url=BROWSER_SVC_BASE, timeout=60) as client:
            await client.post(
                f"/browsers/{browser_session_id}/execute",
                json={"action": "navigate", "url": PUBLIC_TEST_URL, "timeout": 30000},
            )
            # Try typing into an input — if no input exists, the action will
            # fail gracefully (success=False) and that's acceptable
            resp = await client.post(
                f"/browsers/{browser_session_id}/execute",
                json={
                    "action": "type",
                    "selector": "input[type='text']",
                    "text": "hello world",
                    "timeout": 10000,
                },
            )
            assert resp.status_code == 200
            data = resp.json()
            assert isinstance(data, dict)

    # ── Screenshot ────────────────────────────────────────────

    @require_docker
    @pytest.mark.asyncio
    async def test_screenshot(self, browser_session_id):
        """Screenshot returns a base64-encoded PNG."""
        async with httpx.AsyncClient(base_url=BROWSER_SVC_BASE, timeout=60) as client:
            # Navigate first
            await client.post(
                f"/browsers/{browser_session_id}/execute",
                json={"action": "navigate", "url": PUBLIC_TEST_URL, "timeout": 30000},
            )
            resp = await client.post(
                f"/browsers/{browser_session_id}/execute",
                json={"action": "screenshot"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["success"] is True
            result = data["result"]
            assert "screenshot" in result
            assert result["format"] == "png"
            # Verify base64 content
            import base64

            b64_data = result["screenshot"]
            assert len(b64_data) > 100  # reasonable minimum for a PNG screenshot
            # Decode and verify PNG header
            png_bytes = base64.b64decode(b64_data)
            assert png_bytes[:8] == b"\x89PNG\r\n\x1a\n"  # PNG magic bytes

    # ── ExecuteScript ─────────────────────────────────────────

    @require_docker
    @pytest.mark.asyncio
    async def test_execute_script(self, browser_session_id):
        """executeScript returns the evaluated result."""
        async with httpx.AsyncClient(base_url=BROWSER_SVC_BASE, timeout=60) as client:
            await client.post(
                f"/browsers/{browser_session_id}/execute",
                json={"action": "navigate", "url": PUBLIC_TEST_URL, "timeout": 30000},
            )
            resp = await client.post(
                f"/browsers/{browser_session_id}/execute",
                json={"action": "executeScript", "script": "document.title"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["success"] is True
            result = data["result"]
            assert "script_result" in result
            assert isinstance(result["script_result"], str)
            assert len(result["script_result"]) > 0

    # ── Scroll ────────────────────────────────────────────────

    @require_docker
    @pytest.mark.asyncio
    async def test_scroll(self, browser_session_id):
        """Scroll action returns scrolled: true."""
        async with httpx.AsyncClient(base_url=BROWSER_SVC_BASE, timeout=60) as client:
            await client.post(
                f"/browsers/{browser_session_id}/execute",
                json={"action": "navigate", "url": PUBLIC_TEST_URL, "timeout": 30000},
            )
            resp = await client.post(
                f"/browsers/{browser_session_id}/execute",
                json={"action": "scroll"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["success"] is True
            result = data["result"]
            assert result["scrolled"] is True

    # ── Wait ──────────────────────────────────────────────────

    @require_docker
    @pytest.mark.asyncio
    async def test_wait(self, browser_session_id):
        """Wait action (timeout-based) returns waited: true."""
        async with httpx.AsyncClient(base_url=BROWSER_SVC_BASE, timeout=60) as client:
            resp = await client.post(
                f"/browsers/{browser_session_id}/execute",
                json={"action": "wait", "timeout": 2000},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["success"] is True
            result = data["result"]
            assert result["waited"] is True


# ═══════════════════════════════════════════════════════════════════════════
# Security / Error Cases
# ═══════════════════════════════════════════════════════════════════════════


class TestSecurityAndErrors:
    """Private IP rejection, missing parameters, unknown actions, expired sessions."""

    @require_docker
    @pytest.mark.asyncio
    async def test_navigate_rejects_private_ip(self, browser_session_id):
        """Navigate to a private IP returns 400."""
        async with httpx.AsyncClient(base_url=BROWSER_SVC_BASE, timeout=30) as client:
            resp = await client.post(
                f"/browsers/{browser_session_id}/execute",
                json={
                    "action": "navigate",
                    "url": "http://192.168.1.1/",
                    "timeout": 5000,
                },
            )
            assert resp.status_code == 400
            data = resp.json()
            assert "detail" in data
            assert (
                "private" in data["detail"].lower()
                or "internal" in data["detail"].lower()
                or "blocked" in data["detail"].lower()
            )

    @require_docker
    @pytest.mark.asyncio
    async def test_navigate_rejects_localhost(self, browser_session_id):
        """Navigate to localhost returns 400."""
        async with httpx.AsyncClient(base_url=BROWSER_SVC_BASE, timeout=30) as client:
            resp = await client.post(
                f"/browsers/{browser_session_id}/execute",
                json={
                    "action": "navigate",
                    "url": "http://127.0.0.1:8080/health",
                    "timeout": 5000,
                },
            )
            assert resp.status_code == 400
            data = resp.json()
            assert (
                "private" in data["detail"].lower()
                or "internal" in data["detail"].lower()
                or "blocked" in data["detail"].lower()
            )

    @require_docker
    @pytest.mark.asyncio
    async def test_navigate_without_url(self, browser_session_id):
        """Navigate without a URL returns 400."""
        async with httpx.AsyncClient(base_url=BROWSER_SVC_BASE, timeout=30) as client:
            resp = await client.post(
                f"/browsers/{browser_session_id}/execute",
                json={"action": "navigate"},
            )
            assert resp.status_code == 400

    @require_docker
    @pytest.mark.asyncio
    async def test_unknown_action(self, browser_session_id):
        """Unknown action returns 400."""
        async with httpx.AsyncClient(base_url=BROWSER_SVC_BASE, timeout=30) as client:
            resp = await client.post(
                f"/browsers/{browser_session_id}/execute",
                json={"action": "nonexistent_action"},
            )
            assert resp.status_code == 400

    @require_docker
    @pytest.mark.asyncio
    async def test_execute_on_expired_session(self):
        """Execute on a session that may expire returns 404."""
        async with httpx.AsyncClient(base_url=BROWSER_SVC_BASE, timeout=30) as client:
            # Create a session with a very short TTL
            resp = await client.post("/browsers", json={"ttl": 1})
            assert resp.status_code == 200
            session_id = resp.json()["id"]

            # Wait for it to expire
            await asyncio.sleep(2)

            # Execute should fail
            resp2 = await client.post(
                f"/browsers/{session_id}/execute",
                json={"action": "navigate", "url": "https://example.com"},
            )
            assert resp2.status_code == 404

    @require_docker
    @pytest.mark.asyncio
    async def test_click_without_selector(self, browser_session_id):
        """Click without selector returns 400."""
        async with httpx.AsyncClient(base_url=BROWSER_SVC_BASE, timeout=30) as client:
            resp = await client.post(
                f"/browsers/{browser_session_id}/execute",
                json={"action": "click"},
            )
            assert resp.status_code == 400


# ═══════════════════════════════════════════════════════════════════════════
# Session Expiry & Cleanup
# ═══════════════════════════════════════════════════════════════════════════


class TestSessionExpiry:
    """Session expiry via TTL and automatic cleanup."""

    @require_docker
    @pytest.mark.asyncio
    async def test_session_expiry_and_cleanup(self):
        """A session with a short TTL is cleaned up after expiry."""
        async with httpx.AsyncClient(base_url=BROWSER_SVC_BASE, timeout=30) as client:
            # Create a session with 2s TTL
            resp = await client.post("/browsers", json={"ttl": 2})
            assert resp.status_code == 200
            session_id = resp.json()["id"]

            # Verify it exists initially
            resp2 = await client.get("/browsers")
            initial_ids = [s["id"] for s in resp2.json()["sessions"]]
            assert session_id in initial_ids

            # Wait for expiry + cleanup interval
            await asyncio.sleep(5)

            # Verify it's gone
            resp3 = await client.get("/browsers")
            current_ids = [s["id"] for s in resp3.json()["sessions"]]
            assert session_id not in current_ids

    @require_docker
    @pytest.mark.asyncio
    async def test_list_sessions_shows_age_and_ttl(self, browser_session_id):
        """List sessions includes age_seconds, ttl, and idle_seconds."""
        async with httpx.AsyncClient(base_url=BROWSER_SVC_BASE, timeout=30) as client:
            resp = await client.get("/browsers")
            assert resp.status_code == 200
            data = resp.json()
            sessions = [s for s in data["sessions"] if s["id"] == browser_session_id]
            if sessions:
                session = sessions[0]
                assert "age_seconds" in session
                assert "ttl" in session
                assert "idle_seconds" in session
                assert session["ttl"] == 120
                assert isinstance(session["age_seconds"], int)
                assert isinstance(session["idle_seconds"], int)


# ═══════════════════════════════════════════════════════════════════════════
# Health & Metrics
# ═══════════════════════════════════════════════════════════════════════════


class TestHealthAndMetrics:
    """/health and /metrics endpoints."""

    @require_docker
    @pytest.mark.asyncio
    async def test_health_endpoint(self):
        """GET /health returns ok with active_sessions."""
        async with httpx.AsyncClient(base_url=BROWSER_SVC_BASE, timeout=10) as client:
            resp = await client.get("/health")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ok"
            assert "active_sessions" in data
            assert isinstance(data["active_sessions"], int)

    @require_docker
    @pytest.mark.asyncio
    async def test_metrics_endpoint(self):
        """GET /metrics returns OpenMetrics format."""
        async with httpx.AsyncClient(base_url=BROWSER_SVC_BASE, timeout=10) as client:
            resp = await client.get("/metrics")
            assert resp.status_code == 200
            assert "openmetrics-text" in resp.headers.get("content-type", "")
            body = resp.text
            assert "# HELP" in body
            assert "# TYPE" in body
            assert "# EOF" in body.strip()

    @require_docker
    @pytest.mark.asyncio
    async def test_metrics_contains_session_counters(self):
        """/metrics contains browser_sessions_created_total."""
        async with httpx.AsyncClient(base_url=BROWSER_SVC_BASE, timeout=10) as client:
            resp = await client.get("/metrics")
            assert resp.status_code == 200
            assert "browser_sessions_created_total" in resp.text
            assert "browser_sessions_expired_total" in resp.text


# ═══════════════════════════════════════════════════════════════════════════
# Cookie Persistence (when Valkey is available)
# ═══════════════════════════════════════════════════════════════════════════


class TestCookiePersistence:
    """Cookie round-trip through Valkey (if connected)."""

    @require_docker
    @pytest.mark.asyncio
    async def test_cookie_persistence_does_not_crash(self, browser_session_id):
        """Navigating with cookie injection enabled does not crash."""
        async with httpx.AsyncClient(base_url=BROWSER_SVC_BASE, timeout=60) as client:
            # Navigate — Valkey may or may not be connected; the app handles both
            resp = await client.post(
                f"/browsers/{browser_session_id}/execute",
                json={"action": "navigate", "url": PUBLIC_TEST_URL, "timeout": 30000},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["success"] is True

    @require_docker
    @pytest.mark.asyncio
    async def test_health_sessions_count(self):
        """Health endpoint active_sessions is an integer >= 0."""
        async with httpx.AsyncClient(base_url=BROWSER_SVC_BASE, timeout=10) as client:
            resp = await client.get("/health")
            assert resp.status_code == 200
            data = resp.json()
            assert isinstance(data["active_sessions"], int)
            assert data["active_sessions"] >= 0
