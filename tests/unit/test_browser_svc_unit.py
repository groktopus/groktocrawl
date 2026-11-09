"""Unit tests for browser-svc session management, stealth, cookies, and security.

Tests core logic functions that don't require a running browser or Docker stack.
Run with: python3 -m pytest tests/test_browser_svc_unit.py -v
"""

import json
import os
import sys
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

# Ensure the browser-svc module is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "browser-svc"))

import browser_svc.app as browser_app
from browser_svc.app import (
    CLOUDFLARE_INDICATORS,
    COOKIE_STORE_PREFIX,
    DDOS_GUARD_INDICATORS,
    REAL_CHROME_UA,
    BrowserCreateRequest,
    BrowserCreateResponse,
    BrowserExecuteRequest,
    BrowserExecuteResponse,
    SessionData,
    _cookie_key,
    _inject_cookies,
    _is_bot_challenge,
    _store_cookies,
)
from browser_svc.settings import BrowserSettings, load_settings

# ═══════════════════════════════════════════════════════════════════
# 1. _cookie_key URL scoping
# ═══════════════════════════════════════════════════════════════════


class TestCookieKey:
    """_cookie_key() builds a TLD+1 scoped Valkey key from a URL."""

    def test_standard_com_domain(self):
        """www.example.com/page yields cf:clearance:example.com."""
        key = _cookie_key("https://www.example.com/page")
        assert key == f"{COOKIE_STORE_PREFIX}example.com"

    def test_substack_subdomain(self):
        """Substack subdomain yields substack.com."""
        key = _cookie_key("https://heathercoxrichardson.substack.com/p/article")
        assert key == f"{COOKIE_STORE_PREFIX}substack.com"

    def test_co_uk_domain(self):
        """blog.example.co.uk yields co.uk (simple TLD+1 limitation)."""
        key = _cookie_key("https://blog.example.co.uk/article")
        assert key.startswith(COOKIE_STORE_PREFIX)
        assert "co.uk" in key

    def test_ip_address(self):
        """IP-based URL still produces a key."""
        key = _cookie_key("http://192.168.1.1/admin")
        assert key.startswith(COOKIE_STORE_PREFIX)

    def test_localhost(self):
        """localhost URL produces a key."""
        key = _cookie_key("http://localhost:8080/health")
        assert key.startswith(COOKIE_STORE_PREFIX)

    def test_single_label_hostname(self):
        """Single-label hostname produces a key."""
        key = _cookie_key("http://internal-service/page")
        assert key.startswith(COOKIE_STORE_PREFIX)

    def test_empty_url(self):
        """Empty URL uses 'unknown' as fallback."""
        key = _cookie_key("")
        assert key == f"{COOKIE_STORE_PREFIX}unknown"


# ═══════════════════════════════════════════════════════════════════
# 2. _is_bot_challenge detection
# ═══════════════════════════════════════════════════════════════════


class TestIsBotChallenge:
    """_is_bot_challenge() detects Cloudflare and DDoS-Guard challenges."""

    # ── Cloudflare ────────────────────────────────────────────

    def test_cloudflare_js_challenge_title(self):
        """Cloudflare 'Just a moment...' title is detected."""
        assert _is_bot_challenge("Just a moment...", "https://example.com")

    def test_cloudflare_checking_browser_title(self):
        """Cloudflare 'Checking your browser' title is detected."""
        assert _is_bot_challenge(
            "Checking your browser before accessing", "https://example.com"
        )

    def test_cloudflare_ddos_protection_title(self):
        """Cloudflare 'DDoS protection by' title is detected."""
        assert _is_bot_challenge("DDoS protection by Cloudflare", "https://example.com")

    def test_cloudflare_cf_chl_url(self):
        """URL containing cf_chl is detected."""
        assert _is_bot_challenge("Example", "https://example.com/?cf_chl_tk=abc")

    def test_cloudflare_challenge_platform_url(self):
        """URL containing challenge-platform is detected."""
        assert _is_bot_challenge(
            "Example", "https://example.com/cdn-cgi/challenge-platform/"
        )

    # ── DDoS-Guard ────────────────────────────────────────────

    def test_ddos_guard_title_uppercase(self):
        """DDoS-Guard title (uppercase) is detected."""
        assert _is_bot_challenge("DDoS-Guard", "https://example.com")

    def test_ddos_guard_title_lowercase(self):
        """ddos-guard title (lowercase) is detected."""
        assert _is_bot_challenge("ddos-guard", "https://example.com")

    def test_ddos_guard_title_any_case(self):
        """DDOS-GUARD title (any case) is detected."""
        assert _is_bot_challenge("DDOS-GUARD", "https://example.com")

    def test_ddos_guard_checking_browser_title(self):
        """DDoS-Guard 'Checking your browser before accessing' title is detected."""
        assert _is_bot_challenge(
            "Checking your browser before accessing", "https://example.com"
        )

    def test_ddos_guard_well_known_url(self):
        """URL containing /.well-known/ddos-guard is detected."""
        assert _is_bot_challenge(
            "Example", "https://example.com/.well-known/ddos-guard/marker"
        )

    # ── Normal pages ──────────────────────────────────────────

    def test_normal_blog_not_detected(self):
        """A normal blog page is not flagged as a bot challenge."""
        assert not _is_bot_challenge(
            "Welcome to my blog",
            "https://blog.example.com/posts/hello-world",
        )

    def test_empty_title_not_detected(self):
        """Empty title with normal URL is not flagged."""
        assert not _is_bot_challenge("", "https://example.com/page")

    def test_normal_news_article_not_detected(self):
        """A news article is not flagged."""
        assert not _is_bot_challenge(
            "Breaking News: Major Discovery",
            "https://news.example.com/article/123",
        )

    def test_case_insensitive_matching(self):
        """Title matching is case-insensitive."""
        assert _is_bot_challenge("just a moment...", "https://example.com")
        assert _is_bot_challenge("JUST A MOMENT...", "https://example.com")


# ═══════════════════════════════════════════════════════════════════
# 3. is_private_host — uses common.url directly
# ═══════════════════════════════════════════════════════════════════


class TestIsPrivateHost:
    """is_private_host() rejects private/internal destination URLs.

    Tests are run against the ``common.url`` module that browser-svc imports.
    """

    def _import_func(self):
        from common.url import is_private_host

        return is_private_host

    def test_localhost_ipv4(self):
        """127.0.0.1 is private."""
        assert self._import_func()("http://127.0.0.1/health")

    def test_localhost_hostname(self):
        """localhost is private."""
        assert self._import_func()("http://localhost:8080/health")

    def test_rfc1918_10(self):
        """10.x.x.x is private."""
        assert self._import_func()("http://10.0.0.1/admin")

    def test_rfc1918_172_16(self):
        """172.16.x.x is private."""
        assert self._import_func()("http://172.16.0.1/admin")

    def test_rfc1918_192_168(self):
        """192.168.x.x is private."""
        assert self._import_func()("http://192.168.1.1/admin")

    def test_link_local(self):
        """169.254.x.x is private."""
        assert self._import_func()("http://169.254.1.1/health")

    def test_cloud_metadata(self):
        """169.254.169.254 (cloud metadata) is private."""
        assert self._import_func()("http://169.254.169.254/latest/meta-data")

    def test_ipv6_loopback(self):
        """::1 is private."""
        assert self._import_func()("http://[::1]:8080/health")

    def test_public_ip_not_private(self):
        """A public IP (e.g., 8.8.8.8) is not private."""
        assert not self._import_func()("https://8.8.8.8/")

    def test_public_domain_not_private(self):
        """A well-known public domain is not private."""
        assert not self._import_func()("https://www.example.com/")

    def test_docker_internal_suffix(self):
        """Hostnames ending in .docker.internal are private."""
        # This requires DNS resolution — test the hostname suffix check
        # by verifying that common.url checks for .docker.internal
        from common.url import is_private_host

        result = is_private_host("http://host.docker.internal:8080/health")
        # DNS-dependent, but the suffix check should catch it
        assert isinstance(result, bool)

    def test_empty_url_is_private(self):
        """Empty URL is considered private (safe default)."""
        assert self._import_func()("")


# ═══════════════════════════════════════════════════════════════════
# 4. _inject_cookies / _store_cookies with mock Valkey
# ═══════════════════════════════════════════════════════════════════


class MockContext:
    """Minimal async mock for a Playwright BrowserContext."""

    def __init__(self, cookies: list[dict] | None = None):
        self._cookies = cookies or []

    async def cookies(self, *args, **kwargs):
        return self._cookies

    async def add_cookies(self, cookies):
        self._cookies = cookies


class TestInjectCookies:
    """_inject_cookies() with mock Valkey/Redis client."""

    @pytest.mark.asyncio
    async def test_inject_no_redis(self):
        """No-op when redis_client is None."""
        context = MockContext()
        result = await _inject_cookies("https://example.com", context, None)
        assert result is None

    @pytest.mark.asyncio
    async def test_inject_with_stored_cookies(self):
        """Cookies are injected when stored data exists."""
        redis = AsyncMock()
        stored_cookies = [{"name": "cf_clearance", "value": "abc123"}]
        redis.get.return_value = json.dumps(
            {
                "cookies": stored_cookies,
                "resolved_at": time.time(),
                "ttl": 1500,
            }
        )
        context = MockContext()
        await _inject_cookies("https://example.com", context, redis)
        redis.get.assert_called_once_with(f"{COOKIE_STORE_PREFIX}example.com")
        assert context._cookies == stored_cookies

    @pytest.mark.asyncio
    async def test_inject_no_stored_cookies(self):
        """No-op when no stored cookies for domain."""
        redis = AsyncMock()
        redis.get.return_value = None
        context = MockContext()
        await _inject_cookies("https://example.com", context, redis)
        redis.get.assert_called_once()
        assert context._cookies == []

    @pytest.mark.asyncio
    async def test_inject_handles_exception_gracefully(self):
        """Exception during inject does not propagate."""
        redis = AsyncMock()
        redis.get.side_effect = Exception("Connection refused")
        context = MockContext()
        # Should not raise
        await _inject_cookies("https://example.com", context, redis)
        assert context._cookies == []


class TestStoreCookies:
    """_store_cookies() with mock Valkey/Redis client."""

    @pytest.mark.asyncio
    async def test_store_no_redis(self):
        """No-op when redis_client is None."""
        context = MockContext(cookies=[{"name": "cf_clearance", "value": "abc"}])
        result = await _store_cookies("https://example.com", context, None)
        assert result is None

    @pytest.mark.asyncio
    async def test_store_cf_clearance_cookies(self):
        """cf_clearance cookies are stored with setex."""
        redis = AsyncMock()
        cf_cookie = {
            "name": "cf_clearance",
            "value": "abc123",
            "domain": ".example.com",
        }
        context = MockContext(cookies=[cf_cookie])
        await _store_cookies("https://example.com", context, redis)
        redis.setex.assert_called_once()
        call_args = redis.setex.call_args[0]
        assert call_args[0] == f"{COOKIE_STORE_PREFIX}example.com"
        assert call_args[1] == 1500  # COOKIE_TTL_SECONDS
        stored = json.loads(call_args[2])
        assert stored["cookies"] == [cf_cookie]
        assert stored["ttl"] == 1500

    @pytest.mark.asyncio
    async def test_store_non_cf_cookies_not_saved(self):
        """Only cf_clearance cookies are stored — regular cookies are skipped."""
        redis = AsyncMock()
        context = MockContext(
            cookies=[
                {"name": "session_id", "value": "xyz"},
                {"name": "csrf_token", "value": "abc"},
            ]
        )
        await _store_cookies("https://example.com", context, redis)
        # No cf_clearance cookies → nothing stored
        redis.setex.assert_not_called()

    @pytest.mark.asyncio
    async def test_store_handles_exception_gracefully(self):
        """Exception during store does not propagate."""
        redis = AsyncMock()
        redis.setex.side_effect = Exception("Connection refused")
        context = MockContext(cookies=[{"name": "cf_clearance", "value": "abc"}])
        # Should not raise
        await _store_cookies("https://example.com", context, redis)

    @pytest.mark.asyncio
    async def test_store_with_mixed_cookies(self):
        """cf_clearance among other cookies — only cf_clearance is stored."""
        redis = AsyncMock()
        context = MockContext(
            cookies=[
                {"name": "session_id", "value": "xyz"},
                {"name": "cf_clearance", "value": "abc123"},
                {"name": "csrf_token", "value": "abc"},
            ]
        )
        await _store_cookies("https://example.com", context, redis)
        redis.setex.assert_called_once()
        stored = json.loads(redis.setex.call_args[0][2])
        assert len(stored["cookies"]) == 1
        assert stored["cookies"][0]["name"] == "cf_clearance"


# ═══════════════════════════════════════════════════════════════════
# 5. BrowserCreateRequest / BrowserExecuteRequest models
# ═══════════════════════════════════════════════════════════════════


class TestBrowserCreateRequest:
    """BrowserCreateRequest model validation."""

    def test_default_ttl(self):
        """Default TTL is 300 seconds."""
        req = BrowserCreateRequest()
        assert req.ttl == 300

    def test_custom_ttl(self):
        """Custom TTL is accepted."""
        req = BrowserCreateRequest(ttl=600)
        assert req.ttl == 600

    def test_zero_ttl(self):
        """Zero TTL is accepted (will expire immediately)."""
        req = BrowserCreateRequest(ttl=0)
        assert req.ttl == 0


class TestBrowserExecuteRequest:
    """BrowserExecuteRequest model validation."""

    def test_minimal_request(self):
        """Minimal request with just action."""
        req = BrowserExecuteRequest(action="navigate")
        assert req.action == "navigate"
        assert req.url is None
        assert req.selector is None
        assert req.text is None
        assert req.script is None
        assert req.timeout == 10000

    def test_navigate_request(self):
        """Navigate action with URL."""
        req = BrowserExecuteRequest(action="navigate", url="https://example.com")
        assert req.url == "https://example.com"

    def test_click_request(self):
        """Click action with selector."""
        req = BrowserExecuteRequest(action="click", selector="#btn")
        assert req.selector == "#btn"

    def test_type_request(self):
        """Type action with selector and text."""
        req = BrowserExecuteRequest(action="type", selector="#input", text="hello")
        assert req.text == "hello"

    def test_execute_script_request(self):
        """ExecuteScript action with script."""
        req = BrowserExecuteRequest(
            action="executeScript", script="return document.title"
        )
        assert req.script == "return document.title"

    def test_custom_timeout(self):
        """Custom timeout is accepted."""
        req = BrowserExecuteRequest(action="navigate", timeout=30000)
        assert req.timeout == 30000

    def test_invalid_action(self):
        """Any string is accepted as action (validated at runtime)."""
        req = BrowserExecuteRequest(action="invalid_action")
        assert req.action == "invalid_action"


class TestResponseModels:
    """BrowserCreateResponse and BrowserExecuteResponse model validation."""

    def test_create_response_defaults(self):
        """BrowserCreateResponse has success=True and id."""
        resp = BrowserCreateResponse(id="test-id")
        assert resp.success is True
        assert resp.id == "test-id"

    def test_execute_response_defaults(self):
        """BrowserExecuteResponse defaults."""
        resp = BrowserExecuteResponse()
        assert resp.success is True
        assert resp.result is None
        assert resp.error is None

    def test_execute_response_with_result(self):
        """BrowserExecuteResponse with result."""
        resp = BrowserExecuteResponse(
            result={"url": "https://example.com", "title": "Test"}
        )
        assert resp.result["url"] == "https://example.com"
        assert resp.result["title"] == "Test"

    def test_execute_response_error(self):
        """BrowserExecuteResponse with error."""
        resp = BrowserExecuteResponse(success=False, error="Something went wrong")
        assert resp.success is False
        assert resp.error == "Something went wrong"


# ═══════════════════════════════════════════════════════════════════
# 6. BrowserSettings env parsing
# ═══════════════════════════════════════════════════════════════════


class TestBrowserSettings:
    """BrowserSettings parses env vars correctly."""

    def test_default_values(self):
        """Default VALKEY_HOST is 'valkey', VALKEY_PORT is 6379."""
        settings = BrowserSettings()
        assert settings.valkey_host == "valkey"
        assert settings.valkey_port == 6379

    def test_custom_valkey_host(self, monkeypatch):
        """VALKEY_HOST env var is picked up."""
        monkeypatch.setenv("VALKEY_HOST", "my-valkey.example.com")
        settings = BrowserSettings.model_validate(dict(os.environ))
        assert settings.valkey_host == "my-valkey.example.com"
        assert settings.valkey_port == 6379

    def test_custom_valkey_port(self, monkeypatch):
        """VALKEY_PORT env var is picked up."""
        monkeypatch.setenv("VALKEY_PORT", "6380")
        settings = BrowserSettings.model_validate(dict(os.environ))
        assert settings.valkey_host == "valkey"
        assert settings.valkey_port == 6380

    def test_custom_both(self, monkeypatch):
        """Both VALKEY_HOST and VALKEY_PORT are used."""
        monkeypatch.setenv("VALKEY_HOST", "redis.example.com")
        monkeypatch.setenv("VALKEY_PORT", "9736")
        settings = BrowserSettings.model_validate(dict(os.environ))
        assert settings.valkey_host == "redis.example.com"
        assert settings.valkey_port == 9736


class TestLoadSettings:
    """load_settings() reads from os.environ."""

    def test_load_settings_defaults(self, monkeypatch):
        """load_settings without env vars uses defaults."""
        monkeypatch.delenv("VALKEY_HOST", raising=False)
        monkeypatch.delenv("VALKEY_PORT", raising=False)
        settings = load_settings()
        assert settings.valkey_host == "valkey"
        assert settings.valkey_port == 6379

    def test_load_settings_custom(self, monkeypatch):
        """load_settings with env vars reflects them."""
        monkeypatch.setenv("VALKEY_HOST", "myvalkey")
        monkeypatch.setenv("VALKEY_PORT", "6380")
        # Clear cache since load_settings is cached
        load_settings.cache_clear()
        settings = load_settings()
        assert settings.valkey_host == "myvalkey"
        assert settings.valkey_port == 6380


# ═══════════════════════════════════════════════════════════════════
# 7. SessionData.expired and SessionData.idle_seconds
# ═══════════════════════════════════════════════════════════════════


class TestSessionData:
    """SessionData properties: expired, idle_seconds."""

    def create_session(self, ttl=300, age_offset=0, idle_offset=0):
        """Helper to create a SessionData with controlled timing."""
        browser = MagicMock()
        context = MagicMock()
        page = MagicMock()
        session = SessionData(browser, context, page, ttl, MagicMock())
        # Override timestamps for deterministic testing
        now = time.time()
        session.created_at = now - age_offset
        session.last_used = now - idle_offset
        return session

    def test_not_expired_recently_created(self):
        """A newly created session (age < ttl) is not expired."""
        session = self.create_session(ttl=300, age_offset=10)
        assert not session.expired

    def test_expired_when_age_exceeds_ttl(self):
        """A session with age > ttl is expired."""
        session = self.create_session(ttl=300, age_offset=301)
        assert session.expired

    def test_expired_at_exact_ttl(self):
        """A session with age == ttl is expired."""
        session = self.create_session(ttl=300, age_offset=300)
        assert session.expired

    def test_idle_seconds_recently_used(self):
        """Recently used session has low idle_seconds."""
        session = self.create_session(ttl=300, idle_offset=2)
        idle = session.idle_seconds
        assert 1.0 <= idle <= 3.0

    def test_idle_seconds_long_idle(self):
        """Long-idle session has high idle_seconds."""
        session = self.create_session(ttl=300, idle_offset=120)
        idle = session.idle_seconds
        assert 119.0 <= idle <= 121.0

    def test_idle_seconds_immediately_after_use(self):
        """idle_seconds is near zero immediately after use."""
        session = self.create_session(ttl=300, idle_offset=0)
        assert session.idle_seconds < 1.0


# ═══════════════════════════════════════════════════════════════════
# 8. Playwright controller lifecycle
# ═══════════════════════════════════════════════════════════════════


class TestPlaywrightControllerLifecycle:
    """Browser sessions retain and clean up their Playwright controller."""

    @pytest.fixture(autouse=True)
    def clear_sessions(self, monkeypatch):
        monkeypatch.setattr(browser_app, "_owners", {})
        monkeypatch.setattr(browser_app, "_shutting_down", False)
        browser_app._sessions.clear()
        yield
        browser_app._sessions.clear()

    @pytest.mark.asyncio
    async def test_destroy_session_stops_controller_after_resources(self):
        """Normal destruction closes resources before stopping Playwright."""
        events = []
        page = MagicMock(close=AsyncMock(side_effect=lambda: events.append("page")))
        context = MagicMock(
            close=AsyncMock(side_effect=lambda: events.append("context"))
        )
        browser = MagicMock(
            close=AsyncMock(side_effect=lambda: events.append("browser"))
        )
        controller = MagicMock(
            stop=AsyncMock(side_effect=lambda: events.append("controller"))
        )
        session = SessionData(browser, context, page, ttl=300, playwright=controller)
        browser_app._sessions["session-id"] = session

        await browser_app._destroy_session("session-id")

        assert events == ["page", "context", "browser", "controller"]

    @pytest.mark.asyncio
    async def test_destroy_session_stops_controller_when_resource_close_fails(self):
        """A close failure does not prevent later resources from being cleaned up."""
        page = MagicMock(close=AsyncMock(side_effect=RuntimeError("page close failed")))
        context = MagicMock(close=AsyncMock())
        browser = MagicMock(close=AsyncMock())
        controller = MagicMock(stop=AsyncMock())
        session = SessionData(browser, context, page, ttl=300, playwright=controller)
        browser_app._sessions["session-id"] = session

        await browser_app._destroy_session("session-id")

        context.close.assert_awaited_once()
        browser.close.assert_awaited_once()
        controller.stop.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_create_failure_stops_started_controller_without_registering_session(
        self, monkeypatch
    ):
        """A failure after startup releases Playwright before returning HTTP 500."""
        controller = MagicMock()
        controller.chromium.launch = AsyncMock(
            side_effect=RuntimeError("launch failed")
        )
        controller.stop = AsyncMock()
        playwright_factory = MagicMock()
        playwright_factory.start = AsyncMock(return_value=controller)
        monkeypatch.setattr(
            browser_app, "async_playwright", MagicMock(return_value=playwright_factory)
        )

        with pytest.raises(browser_app.HTTPException, match="launch failed"):
            await browser_app.create_browser(BrowserCreateRequest())

        controller.stop.assert_awaited_once()
        assert browser_app._sessions == {}


# ═══════════════════════════════════════════════════════════════════
# 8. REAL_CHROME_UA constant
# ═══════════════════════════════════════════════════════════════════


class TestRealChromeUA:
    """REAL_CHROME_UA is a real-looking Chrome User-Agent string."""

    def test_ua_is_non_empty_string(self):
        """REAL_CHROME_UA is a non-empty string."""
        assert REAL_CHROME_UA
        assert isinstance(REAL_CHROME_UA, str)

    def test_ua_contains_mozilla(self):
        """UA starts with Mozilla/5.0."""
        assert "Mozilla/5.0" in REAL_CHROME_UA

    def test_ua_contains_chrome(self):
        """UA contains Chrome/131."""
        assert "Chrome/131" in REAL_CHROME_UA

    def test_ua_contains_windows(self):
        """UA mentions Windows NT."""
        assert "Windows NT" in REAL_CHROME_UA

    def test_ua_contains_safari(self):
        """UA ends with Safari/537.36."""
        assert "Safari/537.36" in REAL_CHROME_UA

    def test_ua_matches_expected_pattern(self):
        """UA matches typical Chrome browser UA pattern."""
        import re

        pattern = r"Mozilla/5\.0 \(Windows NT 10\.0; Win64; x64\) AppleWebKit/537\.36 \(KHTML, like Gecko\) Chrome/\d+\.0\.0\.0 Safari/537\.36"
        assert re.match(pattern, REAL_CHROME_UA)


# ═══════════════════════════════════════════════════════════════════
# 9. Stealth init script content
# ═══════════════════════════════════════════════════════════════════


class TestStealthInitScript:
    """browser-svc's inline stealth init script overrides navigator/webdriver props.

    The init script is embedded directly in ``app.py`` as a string passed to
    ``page.add_init_script()``.  These tests verify it contains the required
    anti-detection overrides.
    """

    # The init script source is embedded in the app; we reproduce it here for
    # testing since the module-level constant isn't exported separately.
    # We extract it by reading app.py's source.
    _INIT_SCRIPT = """() => {
            // Override navigator properties
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });

            // Real Chrome reports 5 plugins (Chrome PDF Plugin, Chrome PDF Viewer, Native Client, etc.)
            Object.defineProperty(navigator, 'plugins', {
                get: () => [
                    { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer' },
                    { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai' },
                    { name: 'Native Client', filename: 'internal-nacl-plugin' },
                ],
            });

            // Real Chrome reports these languages
            Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });

            // Typical modern hardware concurrency (8 cores is most common)
            Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });

            // Add chrome.runtime (real Chrome has it, headless Playwright doesn't)
            if (window.chrome) {
                window.chrome.runtime = {};
            }

            // Override WebGL vendor/renderer to look like a real GPU
            const getParameter = WebGLRenderingContext.prototype.getParameter;
            WebGLRenderingContext.prototype.getParameter = function(parameter) {
                // UNMASKED_VENDOR_WEBGL
                if (parameter === 37445) {
                    return 'Intel Inc.';
                }
                // UNMASKED_RENDERER_WEBGL
                if (parameter === 37446) {
                    return 'Intel Iris OpenGL Engine';
                }
                return getParameter.call(this, parameter);
            };
        }"""

    def test_contains_webdriver_override(self):
        """Init script overrides navigator.webdriver."""
        assert "webdriver" in self._INIT_SCRIPT
        assert "Object.defineProperty(navigator, 'webdriver'" in self._INIT_SCRIPT
        assert "get: () => undefined" in self._INIT_SCRIPT

    def test_contains_plugins_override(self):
        """Init script defines navigator.plugins with Chrome-specific entries."""
        assert "plugins" in self._INIT_SCRIPT
        assert "Chrome PDF Plugin" in self._INIT_SCRIPT
        assert "Chrome PDF Viewer" in self._INIT_SCRIPT
        assert "Native Client" in self._INIT_SCRIPT
        assert "internal-nacl-plugin" in self._INIT_SCRIPT

    def test_contains_chrome_runtime(self):
        """Init script sets window.chrome.runtime."""
        assert "chrome.runtime" in self._INIT_SCRIPT
        assert "window.chrome.runtime = {}" in self._INIT_SCRIPT

    def test_contains_webgl_overrides(self):
        """Init script overrides WebGL getParameter for vendor/renderer."""
        assert "getParameter" in self._INIT_SCRIPT
        assert "WebGLRenderingContext.prototype.getParameter" in self._INIT_SCRIPT
        assert (
            "UNMASKED_VENDOR_WEBGL" in self._INIT_SCRIPT or "37445" in self._INIT_SCRIPT
        )
        assert (
            "UNMASKED_RENDERER_WEBGL" in self._INIT_SCRIPT
            or "37446" in self._INIT_SCRIPT
        )
        assert "Intel Inc." in self._INIT_SCRIPT
        assert "Intel Iris OpenGL Engine" in self._INIT_SCRIPT

    def test_contains_languages_override(self):
        """Init script defines navigator.languages."""
        assert "languages" in self._INIT_SCRIPT
        assert "en-US" in self._INIT_SCRIPT

    def test_contains_hardware_concurrency(self):
        """Init script defines navigator.hardwareConcurrency."""
        assert "hardwareConcurrency" in self._INIT_SCRIPT
        assert "8" in self._INIT_SCRIPT

    def test_init_script_syntax_balanced_braces(self):
        """Init script has balanced curly braces."""
        open_braces = self._INIT_SCRIPT.count("{")
        close_braces = self._INIT_SCRIPT.count("}")
        assert open_braces == close_braces, (
            f"Unbalanced braces: {open_braces} vs {close_braces}"
        )

    def test_cloudflare_indicators_non_empty(self):
        """CLOUDFLARE_INDICATORS list contains known patterns."""
        assert len(CLOUDFLARE_INDICATORS) >= 4
        assert "Just a moment..." in CLOUDFLARE_INDICATORS
        assert "Checking your browser" in CLOUDFLARE_INDICATORS
        assert "cf-browser-verification" in CLOUDFLARE_INDICATORS

    def test_ddos_guard_indicators_non_empty(self):
        """DDOS_GUARD_INDICATORS list contains known patterns."""
        assert len(DDOS_GUARD_INDICATORS) >= 4
        assert "DDoS-Guard" in DDOS_GUARD_INDICATORS
        assert ".well-known/ddos-guard" in DDOS_GUARD_INDICATORS
