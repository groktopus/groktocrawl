"""Stage-metric regression tests that exercise full service modules.

This module intentionally lives under ``tests/unit`` (not ``tests/service``):
it imports the scraper and browser service applications (``scraper.app`` /
``browser_svc.app``), which transitively pull in heavy dependencies
(``curl_cffi`` / ``playwright``). Those packages are only available in the
Fast Tests lane; the integration lane runs ``tests/service`` inside the
agent-svc container, which does not have them installed.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from common.metrics import METRICS


def _metrics_text() -> str:
    return METRICS.generate_openmetrics()


# ── Scraper tier/outcome telemetry ────────────────────────────────


@pytest.mark.asyncio
async def test_scrape_tier_metrics_are_bounded(monkeypatch):
    import scraper.app as app

    async def fake_smart_scrape(*_args, **_kwargs):
        return {
            "markdown": "content " * 100,
            "source": "content-negotiation",
            "url": "https://example.test",
        }

    monkeypatch.setattr(app, "smart_scrape", fake_smart_scrape)
    await app.scrape(app.ScrapeRequest(url="https://example.test"))

    text = _metrics_text()
    assert "# TYPE groktocrawl_scrape_tier_total counter" in text
    assert (
        'groktocrawl_scrape_tier_total{outcome="success",tier="content-negotiation"}'
        in text
    )
    assert "# TYPE groktocrawl_scrape_tier_duration_seconds histogram" in text


@pytest.mark.asyncio
async def test_scrape_http_status_error_exposes_source_status(monkeypatch):
    import scraper.app as app
    from scraper.exceptions import UpstreamError

    async def http_status_error(*_args, **_kwargs):
        return {
            "error": "Source navigation returned HTTP 404",
            "error_code": "HTTP_STATUS_ERROR",
            "http_status": 404,
            "markdown": "",
            "source": "playwright",
        }

    monkeypatch.setattr(app, "smart_scrape", http_status_error)

    with pytest.raises(UpstreamError) as error:
        await app.scrape(app.ScrapeRequest(url="https://example.test/missing"))

    assert error.value.details == {"http_status": 404}


# ── Browser-svc capacity signals ──────────────────────────────────


def test_browser_active_sessions_and_destroyed_reason_metrics(monkeypatch):
    import browser_svc.app as bapp

    bapp._sessions.clear()
    monkeypatch.setattr(bapp, "_owners", {})
    monkeypatch.setattr(bapp, "_shutting_down", False)
    bapp._update_active_sessions_gauge()
    bapp._record_session_destroyed("expired")

    text = _metrics_text()
    assert "# TYPE groktocrawl_browser_active_sessions gauge" in text
    assert "groktocrawl_browser_active_sessions " in text
    assert 'groktocrawl_browser_sessions_destroyed_total{reason="expired"}' in text


@pytest.mark.asyncio
async def test_browser_destroy_expired_records_reason(monkeypatch):
    import browser_svc.app as bapp
    from browser_svc.app import SessionData

    bapp._sessions.clear()
    monkeypatch.setattr(bapp, "_owners", {})
    monkeypatch.setattr(bapp, "_shutting_down", False)
    session = SessionData(object(), object(), object(), ttl=300, playwright=object())
    bapp._sessions["sid"] = session

    monkeypatch.setattr(bapp.BrowserOwner, "close", AsyncMock(return_value=True))

    await bapp._destroy_session("sid", reason="expired")

    text = _metrics_text()
    assert 'groktocrawl_browser_sessions_destroyed_total{reason="expired"}' in text
    assert "browser_sessions_expired_total " in text


@pytest.mark.asyncio
async def test_browser_execute_expired_records_reason(monkeypatch):
    import browser_svc.app as bapp
    from browser_svc.app import BrowserExecuteRequest, SessionData

    bapp._sessions.clear()
    monkeypatch.setattr(bapp, "_owners", {})
    monkeypatch.setattr(bapp, "_shutting_down", False)
    session = SessionData(object(), object(), object(), ttl=-1, playwright=object())
    bapp._sessions["sid"] = session

    destroyed: list[tuple[str, str]] = []

    async def fake_destroy(sid, reason="deleted"):
        destroyed.append((sid, reason))

    monkeypatch.setattr(bapp, "_destroy_session", fake_destroy)

    with pytest.raises(bapp.HTTPException, match="Session expired"):
        await bapp.execute_action(
            "sid", BrowserExecuteRequest(action="navigate", url="https://example.test")
        )
    assert destroyed == [("sid", "expired")]


@pytest.mark.asyncio
async def test_resolved_challenge_omits_stale_navigation_status(monkeypatch):
    import browser_svc.app as bapp
    from browser_svc.app import BrowserExecuteRequest, SessionData

    class Response:
        status = 403
        headers = {}

    waited = False

    class Page:
        url = "https://example.test/article"

        async def goto(self, *_args, **_kwargs):
            return Response()

        async def title(self):
            return "Article" if waited else "Just a moment..."

        async def wait_for_timeout(self, _timeout):
            nonlocal waited
            waited = True

    bapp._sessions.clear()
    monkeypatch.setattr(bapp, "_owners", {})
    monkeypatch.setattr(bapp, "_shutting_down", False)
    monkeypatch.setattr(bapp, "_protected_renderer_enabled", lambda: False)
    monkeypatch.setattr(bapp, "is_private_host", lambda _url: False)
    monkeypatch.setattr(bapp, "_store_cookies", AsyncMock())
    bapp._sessions["sid"] = SessionData(
        object(), object(), Page(), ttl=300, playwright=object()
    )

    result = await bapp.execute_action(
        "sid",
        BrowserExecuteRequest(action="navigate", url="https://example.test/article"),
    )

    assert result.success is True
    assert result.result["title"] == "Article"
    assert "http_status" not in result.result


@pytest.mark.asyncio
async def test_browser_list_expired_records_reason(monkeypatch):
    import browser_svc.app as bapp
    from browser_svc.app import SessionData

    bapp._sessions.clear()
    monkeypatch.setattr(bapp, "_owners", {})
    monkeypatch.setattr(bapp, "_shutting_down", False)
    session = SessionData(object(), object(), object(), ttl=-1, playwright=object())
    bapp._sessions["sid"] = session

    destroyed: list[tuple[str, str]] = []

    async def fake_destroy(sid, reason="deleted"):
        destroyed.append((sid, reason))

    monkeypatch.setattr(bapp, "_destroy_session", fake_destroy)

    result = await bapp.list_browsers()
    assert destroyed == [("sid", "expired")]
    assert result.success is True


@pytest.mark.asyncio
async def test_browser_create_success_records_active_sessions(monkeypatch):

    import browser_svc.app as bapp
    from browser_svc.app import BrowserCreateRequest

    bapp._sessions.clear()
    monkeypatch.setattr(bapp, "_owners", {})
    monkeypatch.setattr(bapp, "_shutting_down", False)

    page = MagicMock()
    page.add_init_script = AsyncMock()
    context = MagicMock()
    context.new_page = AsyncMock(return_value=page)
    browser = MagicMock()
    browser.new_context = AsyncMock(return_value=context)
    controller = MagicMock()
    controller.chromium.launch = AsyncMock(return_value=browser)
    factory = MagicMock()
    factory.start = AsyncMock(return_value=controller)
    monkeypatch.setattr(bapp, "async_playwright", MagicMock(return_value=factory))

    await bapp.create_browser(BrowserCreateRequest())

    assert len(bapp._sessions) == 1
    text = _metrics_text()
    assert "groktocrawl_browser_active_sessions 1.0" in text
    assert "browser_sessions_created_total " in text


# ── Scraper tier telemetry on raise ───────────────────────────────


@pytest.mark.asyncio
async def test_scrape_tier_metrics_record_error_when_smart_scrape_raises(monkeypatch):
    import scraper.app as app

    async def raising_smart_scrape(*_args, **_kwargs):
        raise RuntimeError("upstream unavailable")

    monkeypatch.setattr(app, "smart_scrape", raising_smart_scrape)

    with pytest.raises(app.UpstreamError):
        await app.scrape(app.ScrapeRequest(url="https://example.test"))

    text = _metrics_text()
    assert 'groktocrawl_scrape_tier_total{outcome="error",tier="unknown"}' in text
    assert "# TYPE groktocrawl_scrape_tier_duration_seconds histogram" in text


# ── Browser-svc create-failure semantics ──────────────────────────


@pytest.mark.asyncio
async def test_browser_create_failure_does_not_count_as_destroyed(monkeypatch):
    import browser_svc.app as bapp
    from browser_svc.app import BrowserCreateRequest

    bapp._sessions.clear()
    monkeypatch.setattr(bapp, "_owners", {})
    monkeypatch.setattr(bapp, "_shutting_down", False)
    bapp._update_active_sessions_gauge()

    def fail_start(*_args, **_kwargs):
        raise RuntimeError("launch unavailable")

    monkeypatch.setattr(bapp, "async_playwright", fail_start)

    with pytest.raises(bapp.HTTPException):
        await bapp.create_browser(BrowserCreateRequest())

    text = _metrics_text()
    assert (
        'groktocrawl_browser_sessions_destroyed_total{reason="create_failed"}'
        not in text
    )
    assert "groktocrawl_browser_active_sessions 0.0" in text
