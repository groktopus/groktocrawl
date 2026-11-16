"""Stage-metric coverage for the Playwright fetch lifecycle branches.

These tests drive ``_playwright_fetch_unbounded`` through its defensive
branches (CAPTCHA-unresolved, barrier-detected, challenge-not-resolved, and
cleanup failure) so the browser lifecycle telemetry added in #528 is covered.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from common.metrics import METRICS

SCRAPER_SVC = Path(__file__).resolve().parents[2] / "scraper-svc"
if str(SCRAPER_SVC) not in sys.path:
    sys.path.insert(0, str(SCRAPER_SVC))


class _Page:
    url = "https://example.test"

    async def goto(self, *_args, **_kwargs):
        return None

    async def title(self):
        return "Article"

    async def content(self):
        return "<html><body><article>content</article></body></html>"

    async def wait_for_timeout(self, _timeout):
        return None

    async def evaluate(self, _script):
        return None


class _Context:
    async def new_page(self):
        return _Page()

    async def cookies(self):
        return []


class _Browser:
    async def close(self):
        return None


class _Playwright:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, *_args):
        return None


def _extraction_hist_count() -> float:
    """Return the observed _count for the unlabeled extraction histogram."""
    text = METRICS.generate_openmetrics()
    marker = "groktocrawl_browser_extraction_seconds_count"
    if marker not in text:
        return 0.0
    tail = text[text.index(marker) + len(marker) :].lstrip()
    return float(tail.split()[0])


def _install_fakes(monkeypatch):
    import scraper.cookie_store as cookie_store
    import scraper.stealth as stealth

    monkeypatch.setitem(
        sys.modules,
        "playwright.async_api",
        types.SimpleNamespace(async_playwright=lambda: _Playwright()),
    )

    async def create_browser(*_a):
        return _Browser(), False

    async def create_context(*a, **k):
        return _Context()

    monkeypatch.setattr(stealth, "create_stealth_browser", create_browser)
    monkeypatch.setattr(stealth, "create_stealth_context", create_context)

    async def noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr(cookie_store, "inject_cookies", noop)
    monkeypatch.setattr(cookie_store, "store_cookies", noop)


@pytest.mark.asyncio
async def test_captcha_unresolved_records_extraction(monkeypatch):
    import scraper.captcha as captcha
    import scraper.fetch_tiers as tiers
    from scraper.barrier import BarrierInfo

    _install_fakes(monkeypatch)
    monkeypatch.setattr(tiers, "html_to_markdown", lambda _html: "content " * 100)

    async def unresolved(_page, _url):
        return (
            BarrierInfo(
                detected=True,
                barrier_type="captcha",
                confidence=0.9,
                provider="recaptcha",
            ),
            [],
        )

    monkeypatch.setattr(captcha, "resolve_captcha", unresolved)

    result = await tiers._playwright_fetch_unbounded("https://example.test", None)
    assert result is not None
    assert result["error_code"] == "CAPTCHA_UNRESOLVED"
    assert (
        "groktocrawl_browser_extraction_seconds_count" in METRICS.generate_openmetrics()
    )


@pytest.mark.asyncio
async def test_empty_content_records_extraction(monkeypatch):
    """An empty/falsy-HTML extraction still samples the extraction histogram."""
    import scraper.captcha as captcha
    import scraper.fetch_tiers as tiers

    _install_fakes(monkeypatch)
    # Force every extraction to produce no markdown so the lifecycle falls
    # through to `return None` after the SPA retry loop.
    monkeypatch.setattr(tiers, "html_to_markdown", lambda _html: "")

    async def no_captcha(_page, _url):
        return None, []

    monkeypatch.setattr(captcha, "resolve_captcha", no_captcha)

    before = _extraction_hist_count()
    result = await tiers._playwright_fetch_unbounded("https://example.test", None)
    assert result is None
    assert _extraction_hist_count() == before + 1


@pytest.mark.asyncio
async def test_http_error_navigation_returns_status_without_extracting_page(
    monkeypatch,
):
    import scraper.captcha as captcha
    import scraper.fetch_tiers as tiers

    _install_fakes(monkeypatch)

    class NotFoundResponse:
        status = 404

    async def goto_not_found(self, *_args, **_kwargs):
        return NotFoundResponse()

    async def no_captcha(_page, _url):
        return None, []

    monkeypatch.setattr(_Page, "goto", goto_not_found)
    monkeypatch.setattr(captcha, "resolve_captcha", no_captcha)
    monkeypatch.setattr(
        tiers,
        "html_to_markdown",
        lambda _html: pytest.fail("HTTP error documents must not be extracted"),
    )

    result = await tiers._playwright_fetch_unbounded(
        "https://example.test/missing", None
    )

    assert result == {
        "error": "Source navigation returned HTTP 404 for https://example.test/missing",
        "error_code": "HTTP_STATUS_ERROR",
        "http_status": 404,
        "markdown": "",
        "source": "playwright",
        "url": "https://example.test/missing",
    }


@pytest.mark.asyncio
async def test_barrier_detected_records_extraction(monkeypatch):
    import scraper.captcha as captcha
    import scraper.fetch_tiers as tiers
    from scraper.barrier import BarrierInfo

    _install_fakes(monkeypatch)
    monkeypatch.setattr(tiers, "html_to_markdown", lambda _html: "content " * 100)

    async def no_captcha(_page, _url):
        return None, []

    monkeypatch.setattr(captcha, "resolve_captcha", no_captcha)
    monkeypatch.setattr(
        tiers,
        "_classify_barrier",
        lambda *a, **k: BarrierInfo(
            detected=True, barrier_type="bot", confidence=0.9, provider=None
        ),
    )

    result = await tiers._playwright_fetch_unbounded("https://example.test", None)
    assert result is not None
    assert "Barrier detected" in result["error"]


@pytest.mark.asyncio
async def test_challenge_not_resolved_records_navigation(monkeypatch):
    import scraper.fetch_tiers as tiers

    _install_fakes(monkeypatch)
    monkeypatch.setattr(tiers, "_is_bot_challenge", lambda *_a: True)

    result = await tiers._playwright_fetch_unbounded("https://example.test", None)
    assert result is None
    assert (
        "groktocrawl_browser_navigation_seconds_count" in METRICS.generate_openmetrics()
    )


@pytest.mark.asyncio
async def test_cleanup_error_records_outcome(monkeypatch):
    import scraper.fetch_tiers as tiers

    class FailingBrowser:
        async def close(self):
            raise RuntimeError("close failed")

    import scraper.stealth as stealth

    monkeypatch.setitem(
        sys.modules,
        "playwright.async_api",
        types.SimpleNamespace(async_playwright=lambda: _Playwright()),
    )

    async def create_browser(*_a):
        return FailingBrowser(), False

    async def create_context(*a, **k):
        return _Context()

    monkeypatch.setattr(stealth, "create_stealth_browser", create_browser)
    monkeypatch.setattr(stealth, "create_stealth_context", create_context)
    import scraper.cookie_store as cookie_store

    async def noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr(cookie_store, "inject_cookies", noop)
    monkeypatch.setattr(cookie_store, "store_cookies", noop)
    monkeypatch.setattr(tiers, "html_to_markdown", lambda _html: "content " * 100)

    with pytest.raises(tiers.BrowserCleanupIncompleteError):
        await tiers._playwright_fetch_unbounded("https://example.test", None)

    assert (
        'groktocrawl_browser_cleanup_total{outcome="error"}'
        in METRICS.generate_openmetrics()
    )
    assert (
        'groktocrawl_browser_cleanup_total{outcome="quarantined"}'
        in METRICS.generate_openmetrics()
    )
    # This test simulates process-wide quarantine but runs in the shared unit
    # test interpreter; restore the service baseline explicitly for later tests.
    tiers._browser_quarantined = False
    tiers._browser_quarantine_reason = None
