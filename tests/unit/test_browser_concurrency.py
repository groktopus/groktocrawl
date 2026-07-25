"""Regression tests for bounded scraper browser lifecycles."""

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scraper-svc"))


@pytest.mark.asyncio
async def test_playwright_fetches_bound_complete_browser_lifecycles(monkeypatch):
    import scraper.fetch_tiers as tiers

    monkeypatch.setattr(
        tiers, "_browser_semaphore", asyncio.Semaphore(2), raising=False
    )
    active = 0
    peak = 0

    async def lifecycle(_url: str, _proxy: dict | None) -> None:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1

    monkeypatch.setattr(tiers, "_playwright_fetch_unbounded", lifecycle, raising=False)

    await asyncio.gather(
        *(
            tiers._playwright_fetch_with_proxy(f"https://example{i}.test", None)
            for i in range(6)
        )
    )

    assert peak == 2


@pytest.mark.asyncio
async def test_browser_slot_is_released_after_failed_lifecycle(monkeypatch):
    import scraper.fetch_tiers as tiers

    monkeypatch.setattr(tiers, "_browser_semaphore", asyncio.Semaphore(1))
    attempts = 0

    async def lifecycle(_url: str, _proxy: dict | None) -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("launch failed")
        return "recovered"

    monkeypatch.setattr(tiers, "_playwright_fetch_unbounded", lifecycle)

    with pytest.raises(RuntimeError, match="launch failed"):
        await tiers._playwright_fetch_with_proxy("https://failed.test", None)

    assert (
        await tiers._playwright_fetch_with_proxy("https://recovered.test", None)
        == "recovered"
    )


@pytest.mark.asyncio
async def test_launched_browser_closes_when_context_creation_fails(monkeypatch):
    import playwright.async_api
    import scraper.fetch_tiers as tiers
    import scraper.stealth as stealth

    class Browser:
        closed = False

        async def close(self) -> None:
            self.closed = True

    class PlaywrightManager:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *_args):
            return None

    browser = Browser()

    async def create_browser(_playwright, _url):
        return browser, False

    async def fail_context(*_args, **_kwargs):
        raise RuntimeError("context failed")

    monkeypatch.setattr(
        playwright.async_api, "async_playwright", lambda: PlaywrightManager()
    )
    monkeypatch.setattr(stealth, "create_stealth_browser", create_browser)
    monkeypatch.setattr(stealth, "create_stealth_context", fail_context)

    with pytest.raises(RuntimeError, match="context failed"):
        await tiers._playwright_fetch_unbounded("https://example.test", None)

    assert browser.closed is True


@pytest.mark.asyncio
async def test_cancellation_completes_cleanup_before_releasing_browser_slot(
    monkeypatch,
):
    import playwright.async_api
    import scraper.fetch_tiers as tiers
    import scraper.stealth as stealth

    events: list[str] = []

    class Browser:
        async def close(self) -> None:
            events.append("browser-closed")

    class PlaywrightManager:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *_args):
            events.append("playwright-exited")
            return None

    browser = Browser()
    context_started = asyncio.Event()
    semaphore = asyncio.Semaphore(1)
    monkeypatch.setattr(tiers, "_browser_semaphore", semaphore)

    async def create_browser(_playwright, _url):
        return browser, False

    async def wait_forever_for_context(*_args, **_kwargs):
        context_started.set()
        await asyncio.Future()

    monkeypatch.setattr(
        playwright.async_api, "async_playwright", lambda: PlaywrightManager()
    )
    monkeypatch.setattr(stealth, "create_stealth_browser", create_browser)
    monkeypatch.setattr(stealth, "create_stealth_context", wait_forever_for_context)

    task = asyncio.create_task(
        tiers._playwright_fetch_with_proxy("https://example.test", None)
    )
    await context_started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert events == ["browser-closed", "playwright-exited"]
    assert semaphore.locked() is False


def test_browser_concurrency_setting_defaults_to_four():
    from scraper.settings import ScraperSettings

    settings = ScraperSettings.model_validate({})

    assert settings.max_browser_concurrency == 4


def test_browser_concurrency_setting_rejects_zero():
    from pydantic import ValidationError
    from scraper.settings import ScraperSettings

    with pytest.raises(ValidationError):
        ScraperSettings.model_validate({"SCRAPER_MAX_BROWSER_CONCURRENCY": "0"})
