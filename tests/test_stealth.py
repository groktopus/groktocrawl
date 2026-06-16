"""Tests for stealth Playwright configuration and bot detection logic.

These test the core detection functions that don't need a running Docker stack.
Run with: python3 -m pytest tests/test_stealth.py -v
"""

import os
import sys

import pytest

# Ensure the scraper module is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scraper-svc"))


def test_stealth_module_imports():
    """Verify the stealth module imports and exposes expected functions."""
    from scraper.stealth import (
        REAL_CHROME_UA,
        STEALTH_BROWSER_ARGS,
        STEALTH_CONTEXT_KWARGS,
        STEALTH_INIT_SCRIPT,
        create_stealth_browser,
        create_stealth_context,
    )

    assert callable(create_stealth_browser)
    assert callable(create_stealth_context)
    assert "--disable-blink-features=AutomationControlled" in STEALTH_BROWSER_ARGS
    assert "user_agent" in STEALTH_CONTEXT_KWARGS
    assert STEALTH_CONTEXT_KWARGS["user_agent"] == REAL_CHROME_UA
    assert "viewport" in STEALTH_CONTEXT_KWARGS
    assert STEALTH_CONTEXT_KWARGS["viewport"] == {"width": 1920, "height": 1080}
    assert "locale" in STEALTH_CONTEXT_KWARGS
    assert STEALTH_CONTEXT_KWARGS["locale"] == "en-US"
    assert (
        "webdriver" in STEALTH_INIT_SCRIPT
    )  # Object.defineProperty(navigator, 'webdriver')
    assert (
        "plugins" not in STEALTH_INIT_SCRIPT
    )  # Stripped for compatibility with browser-svc
    assert (
        "chrome" not in STEALTH_INIT_SCRIPT
    )  # Stripped for compatibility with browser-svc
    assert "getParameter" not in STEALTH_INIT_SCRIPT  # Stripped for compatibility


def test_stealth_browser_args_comprehensive():
    """Verify all expected stealth browser args are present."""
    from scraper.stealth import STEALTH_BROWSER_ARGS

    expected_args = [
        "--disable-blink-features=AutomationControlled",
        "--no-sandbox",
        "--disable-dev-shm-usage",
    ]
    for arg in expected_args:
        assert arg in STEALTH_BROWSER_ARGS, f"Missing expected arg: {arg}"


def test_stealth_context_has_realistic_fingerprint():
    """Verify stealth context has realistic browser fingerprint settings."""
    from scraper.stealth import STEALTH_CONTEXT_KWARGS

    # Should have realistic viewport
    assert STEALTH_CONTEXT_KWARGS["viewport"]["width"] >= 1280
    assert STEALTH_CONTEXT_KWARGS["viewport"]["height"] >= 720
    # Should have locale and timezone
    assert STEALTH_CONTEXT_KWARGS.get("locale")
    assert STEALTH_CONTEXT_KWARGS.get("timezone_id")


def test_stealth_init_script_syntax():
    """Verify the init script is valid JavaScript syntax (basic check)."""
    from scraper.stealth import STEALTH_INIT_SCRIPT

    # Should be a string containing a function
    assert STEALTH_INIT_SCRIPT.startswith("() =>")
    # Should contain the webdriver override
    assert "webdriver" in STEALTH_INIT_SCRIPT
    # Should have matching braces
    open_braces = STEALTH_INIT_SCRIPT.count("{")
    close_braces = STEALTH_INIT_SCRIPT.count("}")
    assert open_braces == close_braces, (
        f"Unbalanced braces: {open_braces} vs {close_braces}"
    )


def test_stealth_ua_matches_chrome_131():
    """Verify the User-Agent string is a real Chrome 131 UA."""
    from scraper.stealth import REAL_CHROME_UA

    assert "Chrome/131" in REAL_CHROME_UA
    assert "Mozilla/5.0" in REAL_CHROME_UA
    assert "Windows NT" in REAL_CHROME_UA
    assert "Safari/537.36" in REAL_CHROME_UA


class TestBotChallengeDetection:
    """Test _is_bot_challenge() for Cloudflare and DDoS-Guard patterns."""

    @staticmethod
    def _import_func():
        from scraper.fetch_quality import _is_bot_challenge

        return _is_bot_challenge

    def test_cloudflare_js_challenge_title(self):
        func = self._import_func()
        assert func("Just a moment...", "https://example.com")
        assert func("Checking your browser before accessing", "https://example.com")
        assert func("DDoS protection by Cloudflare", "https://example.com")

    def test_cloudflare_challenge_url(self):
        func = self._import_func()
        assert func("Example", "https://example.com/cdn-cgi/challenge-platform/")
        assert func("Example", "https://example.com/?cf_chl_tk=abc")

    def test_ddos_guard_challenge_title(self):
        func = self._import_func()
        assert func("DDoS-Guard", "https://example.com")
        assert func(
            "Checking your browser before accessing the site", "https://example.com"
        )

    def test_ddos_guard_challenge_url(self):
        func = self._import_func()
        assert func("Example", "https://example.com/.well-known/ddos-guard/")
        assert func("Example", "https://example.com/?ddos-guard")

    def test_normal_page_not_detected(self):
        func = self._import_func()
        assert not func(
            "Welcome to my blog", "https://blog.example.com/posts/hello-world"
        )
        assert not func(
            "Heather Cox Richardson",
            "https://heathercoxrichardson.substack.com/p/june-1-2025",
        )
        assert not func("The Free Press", "https://www.thefp.com/p/some-article")


class TestSubstackRedirectDetection:
    """Test _is_substack_redirect() for Substack session/channel frame patterns."""

    @staticmethod
    def _import_func():
        from scraper.fetch_quality import _is_substack_redirect

        return _is_substack_redirect

    def test_session_attribution_frame(self):
        func = self._import_func()
        assert func("https://substack.com/session-attribution-frame?origin=...")

    def test_channel_frame(self):
        func = self._import_func()
        assert func("https://substack.com/channel-frame?channel=...")

    def test_gtm_redirect(self):
        func = self._import_func()
        assert func("https://www.googletagmanager.com/ns.html?id=GTM-TFQLSP2")

    def test_normal_substack_url_not_detected(self):
        func = self._import_func()
        assert not func("https://heathercoxrichardson.substack.com/p/june-1-2025")
        assert not func("https://simonsarris.substack.com/p/some-article")

    def test_empty_url_not_detected(self):
        func = self._import_func()
        assert not func("")
        assert not func("https://example.com")


class TestBarrierClassification:
    """Test _classify_barrier() replaces the old _looks_suspicious()."""

    @staticmethod
    def _import():
        from scraper.fetch_quality import BarrierInfo, _classify_barrier

        return _classify_barrier, BarrierInfo

    def _c(self, content: str, title: str = "", url: str = "https://example.com"):
        cls, _ = self._import()
        return cls(title, url, content)

    def test_empty_content_suspicious(self):
        r = self._c("")
        assert r.detected
        r = self._c(None)  # type: ignore
        assert r.detected

    def test_short_content_suspicious(self):
        r = self._c("Hello")
        assert r.detected

    def test_cloudflare_indicators(self):
        r = self._c("Just a moment... checking your browser")
        assert r.detected
        r = self._c("DDoS protection by Cloudflare")
        assert r.detected

    def test_substack_indicators(self):
        r = self._c("substack.com/session-attribution-frame")
        assert r.detected
        r = self._c("substack.com/channel-frame")
        assert r.detected

    def test_normal_content_not_suspicious(self):
        r = self._c("The quick brown fox jumps over the lazy dog. " * 10)
        assert not r.detected
        r = self._c("Article content with multiple paragraphs. " * 20)
        assert not r.detected


class TestCookieKey:
    """Test _cookie_key() TLD+1 domain extraction."""

    @staticmethod
    def _import_func():
        from scraper.cookie_store import _cookie_key

        return _cookie_key

    def test_standard_substack(self):
        func = self._import_func()
        assert (
            func("https://heathercoxrichardson.substack.com/p/june-1-2025")
            == "cf:clearance:substack.com"
        )

    def test_standard_com_domain(self):
        func = self._import_func()
        assert func("https://www.example.com/page") == "cf:clearance:example.com"

    def test_co_uk_domain(self):
        func = self._import_func()
        # Note: simple TLD+1 algorithm extracts last 2 dot-parts.
        # For .co.uk this gives "co.uk" not "example.co.uk".
        # This is a known limitation shared with browser-svc.
        key = func("https://blog.example.co.uk/article")
        assert key.startswith("cf:clearance:")
        assert "co.uk" in key

    def test_ip_address(self):
        func = self._import_func()
        key = func("http://192.168.1.1/admin")
        assert key.startswith("cf:clearance:")

    def test_localhost(self):
        func = self._import_func()
        key = func("http://localhost:8080/health")
        assert key.startswith("cf:clearance:")

    def test_single_label(self):
        func = self._import_func()
        key = func("http://internal-service/page")
        assert key.startswith("cf:clearance:")


class TestCookieStoreGraceful:
    """Test that cookie functions don't raise when Valkey is unavailable.

    These tests verify graceful degradation — the scraper should continue
    working without cookie persistence if no Valkey instance is running.
    """

    @staticmethod
    def _import_inject():
        from scraper.cookie_store import inject_cookies

        return inject_cookies

    @staticmethod
    def _import_store():
        from scraper.cookie_store import store_cookies

        return store_cookies

    @pytest.mark.asyncio
    async def test_inject_no_valkey(self):
        """inject_cookies should not raise when Valkey is unavailable."""
        func = self._import_inject()
        # No Valkey running — should log and return None gracefully
        result = await func("https://example.com", None)
        assert result is None

    @pytest.mark.asyncio
    async def test_store_no_valkey(self):
        """store_cookies should not raise when Valkey is unavailable."""
        func = self._import_store()
        result = await func("https://example.com", None)
        assert result is None

    @pytest.mark.asyncio
    async def test_inject_empty_context(self):
        """inject_cookies should handle a context with no cookies gracefully."""
        func = self._import_inject()
        # Even with no Valkey, passing None context should not crash
        result = await func("https://example.com", {})
        assert result is None
