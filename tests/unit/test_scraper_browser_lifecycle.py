"""Bounded and fail-closed behavior for the embedded Playwright tier."""

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scraper-svc"))


def _fresh_admission(monkeypatch, tiers):
    from common.admission import AdmissionController

    monkeypatch.setattr(tiers, "_browser_semaphore", asyncio.Semaphore(1))
    monkeypatch.setattr(
        tiers,
        "_browser_admission",
        AdmissionController(limits={"browser": 1}, weights={"browser": 1}),
    )
    # This is process-wide service state, so restore the non-quarantined
    # baseline explicitly rather than via monkeypatch (which would restore a
    # prior test's intentionally simulated quarantine).
    tiers._browser_quarantined = False
    tiers._browser_quarantine_reason = None
    monkeypatch.setattr(
        tiers,
        "process_capacity",
        lambda: {"state": "ok", "reason": "process_budget_available"},
    )


@pytest.mark.asyncio
async def test_complete_lifecycle_has_hard_deadline_and_releases_after_cleanup(
    monkeypatch,
):
    import scraper.fetch_tiers as tiers

    _fresh_admission(monkeypatch, tiers)
    monkeypatch.setattr(tiers, "_BROWSER_LIFECYCLE_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(tiers, "_BROWSER_CLEANUP_GRACE_SECONDS", 0.1)

    async def wait_until_cancelled(_url, _proxy):
        await asyncio.Future()

    monkeypatch.setattr(tiers, "_playwright_fetch_unbounded", wait_until_cancelled)
    with pytest.raises(TimeoutError, match="lifecycle exceeded"):
        await asyncio.wait_for(
            tiers._playwright_fetch_with_proxy("https://example.test", None), 0.2
        )

    assert not tiers._browser_semaphore.locked()
    assert tiers._browser_admission.active("browser") == 0
    assert tiers.browser_readiness()["admission"] == "available"


@pytest.mark.asyncio
async def test_uncertain_cleanup_quarantines_and_retains_browser_permit(monkeypatch):
    import scraper.fetch_tiers as tiers

    _fresh_admission(monkeypatch, tiers)
    monkeypatch.setattr(tiers, "_BROWSER_LIFECYCLE_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(tiers, "_BROWSER_CLEANUP_GRACE_SECONDS", 0.01)
    monkeypatch.setattr(tiers, "schedule_container_recycle", lambda: None)
    started = asyncio.Event()
    finished = asyncio.Event()
    release_cleanup = asyncio.Event()

    async def cancellation_resistant(_url, _proxy):
        started.set()
        try:
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                await release_cleanup.wait()
                return None
        finally:
            finished.set()

    monkeypatch.setattr(tiers, "_playwright_fetch_unbounded", cancellation_resistant)
    with pytest.raises(tiers.BrowserUnavailableError, match="cleanup incomplete"):
        await tiers._playwright_fetch_with_proxy("https://example.test", None)

    assert started.is_set()
    assert "cleanup_incomplete" in tiers.browser_readiness()["reason"]
    assert tiers._browser_semaphore.locked()
    assert tiers._browser_admission.active("browser") == 1
    with pytest.raises(tiers.BrowserUnavailableError):
        await tiers._playwright_fetch_with_proxy("https://second.test", None)

    release_cleanup.set()
    await finished.wait()
    tiers._browser_active_gauge().dec()
    tiers._browser_semaphore.release()
    tiers._browser_admission.release("browser")
    tiers._browser_quarantined = False
    tiers._browser_quarantine_reason = None


@pytest.mark.asyncio
async def test_request_cancellation_waits_bounded_cleanup_or_quarantines(monkeypatch):
    import scraper.fetch_tiers as tiers

    _fresh_admission(monkeypatch, tiers)
    monkeypatch.setattr(tiers, "_BROWSER_CLEANUP_GRACE_SECONDS", 0.01)
    monkeypatch.setattr(tiers, "schedule_container_recycle", lambda: None)
    started = asyncio.Event()
    finished = asyncio.Event()
    release_cleanup = asyncio.Event()

    async def cancellation_resistant(_url, _proxy):
        started.set()
        try:
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                await release_cleanup.wait()
                return None
        finally:
            finished.set()

    monkeypatch.setattr(tiers, "_playwright_fetch_unbounded", cancellation_resistant)
    request = asyncio.create_task(
        tiers._playwright_fetch_with_proxy("https://example.test", None)
    )
    await started.wait()
    request.cancel()
    with pytest.raises(asyncio.CancelledError):
        await request

    assert tiers._browser_semaphore.locked()
    assert "cleanup_incomplete" in tiers.browser_readiness()["reason"]
    release_cleanup.set()
    await finished.wait()
    tiers._browser_active_gauge().dec()
    tiers._browser_semaphore.release()
    tiers._browser_admission.release("browser")
    tiers._browser_quarantined = False
    tiers._browser_quarantine_reason = None


@pytest.mark.asyncio
async def test_low_cgroup_headroom_rejects_browser_before_start(monkeypatch):
    import scraper.fetch_tiers as tiers

    _fresh_admission(monkeypatch, tiers)
    monkeypatch.setattr(
        tiers,
        "process_capacity",
        lambda: {
            "state": "degraded",
            "reason": "process_capacity_low",
            "available_tasks": 3,
        },
    )
    started = False

    async def should_not_run(_url, _proxy):
        nonlocal started
        started = True
        return None

    monkeypatch.setattr(tiers, "_playwright_fetch_unbounded", should_not_run)
    with pytest.raises(tiers.BrowserUnavailableError, match="process_capacity_low"):
        await tiers._playwright_fetch_with_proxy("https://example.test", None)
    assert started is False
    assert tiers._browser_admission.active("browser") == 0


@pytest.mark.asyncio
async def test_legacy_browser_close_failure_quarantines_its_owned_permit(monkeypatch):
    import playwright.async_api
    import scraper.browser_pool as browser_pool
    import scraper.fetch_tiers as tiers
    import scraper.stealth as stealth

    _fresh_admission(monkeypatch, tiers)
    monkeypatch.setattr(tiers, "schedule_container_recycle", lambda: None)

    class Browser:
        async def close(self):
            raise RuntimeError("synthetic close failure")

    class Manager:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *_args):
            return None

    async def create_browser(_playwright, _url):
        return Browser(), False

    async def fail_context(*_args, **_kwargs):
        raise RuntimeError("synthetic context failure")

    monkeypatch.setattr(
        browser_pool, "get_browser_pool", lambda: browser_pool.BrowserPool()
    )
    monkeypatch.setattr(playwright.async_api, "async_playwright", lambda: Manager())
    monkeypatch.setattr(stealth, "create_stealth_browser", create_browser)
    monkeypatch.setattr(stealth, "create_stealth_context", fail_context)

    with pytest.raises(tiers.BrowserCleanupIncompleteError):
        await tiers._playwright_fetch_with_proxy("https://example.test", None)

    assert tiers._browser_semaphore.locked()
    assert tiers._browser_admission.active("browser") == 1
    assert tiers.browser_readiness()["reason"] == "legacy_browser_close_failed"

    # Restore global observability and admission state after modeling a
    # quarantined process; production deliberately keeps these reservations.
    tiers._browser_active_gauge().dec()
    tiers._browser_semaphore.release()
    tiers._browser_admission.release("browser")
    tiers._browser_quarantined = False
    tiers._browser_quarantine_reason = None


def test_process_capacity_reads_bounded_cgroup_values(tmp_path):
    from scraper.process_health import process_capacity

    (tmp_path / "pids.current").write_text("40\n", encoding="ascii")
    (tmp_path / "pids.max").write_text("64\n", encoding="ascii")
    capacity = process_capacity((tmp_path,))

    assert capacity["state"] == "degraded"
    assert capacity["available_tasks"] == 24
    assert capacity["scope"] == "container_cgroup"


@pytest.mark.asyncio
async def test_health_reports_browser_degradation_without_hiding_service_liveness(
    monkeypatch,
):
    import scraper.fetch_tiers as tiers
    from scraper import app as app_module

    monkeypatch.setattr(app_module, "_browser_available", True)
    monkeypatch.setattr(
        app_module,
        "process_capacity",
        lambda: {"state": "degraded", "reason": "process_capacity_low"},
    )
    monkeypatch.setattr(
        tiers,
        "process_capacity",
        lambda: {"state": "degraded", "reason": "process_capacity_low"},
    )
    payload = await app_module.health()

    assert payload["status"] == "degraded"
    assert payload["checks"]["playwright"]["admission"] == "unavailable"
    assert payload["checks"]["playwright"]["reason"] == "process_capacity_low"
    ready_response = await app_module.browser_ready()
    assert ready_response.status_code == 503
    # The health route remains a normal response; lightweight scrape routes do
    # not share the browser-tier admission gate.


@pytest.mark.asyncio
async def test_unknown_process_capacity_is_not_reported_as_browser_ready(monkeypatch):
    import scraper.fetch_tiers as tiers
    from scraper import app as app_module

    monkeypatch.setattr(app_module, "_browser_available", True)
    unknown = {"state": "unknown", "reason": "process_budget_unavailable"}
    monkeypatch.setattr(app_module, "process_capacity", lambda: unknown)
    monkeypatch.setattr(tiers, "process_capacity", lambda: unknown)

    payload = await app_module.health()
    ready_response = await app_module.browser_ready()

    assert payload["status"] == "degraded"
    assert payload["checks"]["playwright"]["status"] == "unknown"
    assert ready_response.status_code == 503
