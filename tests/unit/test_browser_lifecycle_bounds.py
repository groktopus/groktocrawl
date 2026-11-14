"""Regressions for unregistered browser work and cancellation-safe teardown."""

import asyncio
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "browser-svc"))

from browser_svc import app as service
from browser_svc import lifecycle


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setattr(service, "_sessions", {})
    monkeypatch.setattr(service, "_owners", {})
    monkeypatch.setattr(service, "_shutting_down", False)
    monkeypatch.setattr(service, "_cleanup_task", None)
    monkeypatch.setattr(service, "process_capacity", lambda: {"state": "unknown"})
    monkeypatch.setattr(lifecycle, "CREATE_TIMEOUT", 0.03)
    monkeypatch.setattr(lifecycle, "CLOSE_TIMEOUT", 0.02)


@pytest.fixture
def resources(monkeypatch):
    page = SimpleNamespace(close=AsyncMock(), add_init_script=AsyncMock())
    context = SimpleNamespace(close=AsyncMock(), new_page=AsyncMock(return_value=page))
    browser = SimpleNamespace(
        close=AsyncMock(), new_context=AsyncMock(return_value=context)
    )
    controller = SimpleNamespace(
        stop=AsyncMock(),
        chromium=SimpleNamespace(launch=AsyncMock(return_value=browser)),
    )
    manager = MagicMock(start=AsyncMock(return_value=controller))
    monkeypatch.setattr(service, "async_playwright", lambda: manager)
    return SimpleNamespace(
        page=page,
        context=context,
        browser=browser,
        controller=controller,
        manager=manager,
    )


def step(resources, name):
    return {
        "start": resources.manager.start,
        "launch": resources.controller.chromium.launch,
        "context": resources.browser.new_context,
        "page": resources.context.new_page,
        "script": resources.page.add_init_script,
    }[name]


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["launch", "context", "page", "script"])
async def test_deadline_reclaims_partial_initialization(resources, boundary):
    async def hang(*args, **kwargs):
        await asyncio.Event().wait()

    step(resources, boundary).side_effect = hang
    started = time.monotonic()
    with pytest.raises(service.HTTPException) as error:
        await service.create_browser(service.BrowserCreateRequest())
    assert error.value.status_code == 504
    assert time.monotonic() - started < 0.3
    assert not service._sessions
    assert not service._owners
    resources.controller.stop.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["launch", "context", "page", "script"])
async def test_request_cancellation_reclaims_partial_initialization(
    resources, boundary
):
    entered = asyncio.Event()

    async def hang(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    step(resources, boundary).side_effect = hang
    task = asyncio.create_task(service.create_browser(service.BrowserCreateRequest()))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    resources.controller.stop.assert_awaited_once()
    assert not service._sessions and not service._owners


@pytest.mark.asyncio
async def test_late_driver_start_is_retained_and_closed(resources):
    release = asyncio.Event()
    entered = asyncio.Event()

    async def start():
        entered.set()
        await release.wait()
        return resources.controller

    resources.manager.start.side_effect = start
    task = asyncio.create_task(service.create_browser(service.BrowserCreateRequest()))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    owner = next(iter(service._owners.values()))
    assert owner.phase == "quarantined"
    resources.manager.__aexit__.assert_not_called()
    assert (await service.health()).status_code == 503
    release.set()
    await owner.startup
    await asyncio.sleep(0)
    await owner.late_cleanup
    resources.controller.stop.assert_awaited_once()
    assert not service._sessions
    assert owner.phase == "quarantined"  # Never silently reclaim uncertain capacity.


@pytest.mark.asyncio
async def test_hung_close_continues_and_quarantines(resources):
    release = asyncio.Event()

    async def stubborn_close():
        try:
            await release.wait()
        except asyncio.CancelledError:
            await release.wait()

    resources.page.close.side_effect = stubborn_close
    response = await service.create_browser(service.BrowserCreateRequest())
    await service._destroy_session(response.id)
    owner = service._owners[response.id]
    assert owner.phase == "quarantined" and owner.pending
    resources.context.close.assert_awaited_once()
    resources.browser.close.assert_awaited_once()
    resources.controller.stop.assert_awaited_once()
    with pytest.raises(service.HTTPException, match="cleanup_incomplete"):
        await service.create_browser(service.BrowserCreateRequest())
    release.set()
    await asyncio.gather(*owner.pending)


@pytest.mark.asyncio
async def test_repeated_cancellation_cannot_abandon_cleanup(resources):
    entered = asyncio.Event()
    close_entered = asyncio.Event()
    release = asyncio.Event()

    async def hang():
        entered.set()
        await asyncio.Event().wait()

    async def stop():
        close_entered.set()
        await release.wait()

    resources.context.new_page.side_effect = hang
    resources.controller.stop.side_effect = stop
    task = asyncio.create_task(service.create_browser(service.BrowserCreateRequest()))
    await entered.wait()
    task.cancel()
    await close_entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    owner = next(iter(service._owners.values()))
    assert owner.phase == "closing"
    release.set()
    await owner.cleanup
    assert not service._owners


@pytest.mark.asyncio
async def test_simultaneous_admission_and_health(resources):
    entered, release = asyncio.Event(), asyncio.Event()

    async def launch(**kwargs):
        entered.set()
        await release.wait()
        return resources.browser

    resources.controller.chromium.launch.side_effect = launch
    first = asyncio.create_task(service.create_browser(service.BrowserCreateRequest()))
    await entered.wait()
    with pytest.raises(service.HTTPException, match="creation_in_progress"):
        await service.create_browser(service.BrowserCreateRequest())
    health = await service.health()
    assert health.status_code == 503
    assert json.loads(health.body)["admission"]["creating"] == 1
    resources.manager.start.assert_awaited_once()
    release.set()
    first_id = (await first).id
    second_id = (await service.create_browser(service.BrowserCreateRequest())).id
    with pytest.raises(service.HTTPException, match="session_capacity_reached"):
        await service.create_browser(service.BrowserCreateRequest())
    await service._destroy_session(first_id)
    await service._destroy_session(second_id)
    assert (await service.health()).status_code == 200


@pytest.mark.asyncio
async def test_shutdown_closes_active_and_stops_admission(resources, monkeypatch):
    monkeypatch.setattr(service.app.state, "redis", None, raising=False)
    await service.create_browser(service.BrowserCreateRequest())
    await service.shutdown()
    resources.controller.stop.assert_awaited_once()
    assert not service._sessions and not service._owners
    with pytest.raises(service.HTTPException, match="shutting_down"):
        await service.create_browser(service.BrowserCreateRequest())


@pytest.mark.asyncio
async def test_start_timeout_tracks_late_controller(resources):
    release = asyncio.Event()

    async def delayed():
        await release.wait()
        return resources.controller

    resources.manager.start.side_effect = delayed
    with pytest.raises(service.HTTPException) as error:
        await service.create_browser(service.BrowserCreateRequest())
    assert error.value.status_code == 504
    owner = next(iter(service._owners.values()))
    assert owner.phase == "quarantined"
    release.set()
    await owner.startup
    await asyncio.sleep(0)
    await owner.late_cleanup
    resources.controller.stop.assert_awaited_once()


@pytest.mark.asyncio
async def test_start_failure_attempts_partial_manager_cleanup(resources):
    resources.manager.start.side_effect = RuntimeError("driver failed")
    with pytest.raises(service.HTTPException, match="driver failed"):
        await service.create_browser(service.BrowserCreateRequest())
    resources.manager.__aexit__.assert_awaited_once()
    assert not service._owners


@pytest.mark.asyncio
async def test_close_failure_attempts_all_resources(resources):
    resources.page.close.side_effect = RuntimeError("disconnected")
    response = await service.create_browser(service.BrowserCreateRequest())
    await service._destroy_session(response.id)
    resources.context.close.assert_awaited_once()
    resources.browser.close.assert_awaited_once()
    resources.controller.stop.assert_awaited_once()
    assert service._owners[response.id].phase == "quarantined"


@pytest.mark.asyncio
async def test_closing_reservation_is_not_released_early(resources):
    entered, release = asyncio.Event(), asyncio.Event()
    first = await service.create_browser(service.BrowserCreateRequest())
    second = await service.create_browser(service.BrowserCreateRequest())

    async def close():
        entered.set()
        await release.wait()

    resources.page.close.side_effect = close
    deletion = asyncio.create_task(service._destroy_session(first.id))
    await entered.wait()
    assert service._owners[first.id].phase == "closing"
    with pytest.raises(service.HTTPException, match="session_capacity_reached"):
        await service.create_browser(service.BrowserCreateRequest())
    release.set()
    await deletion
    await service._destroy_session(second.id)
    assert not service._owners


@pytest.mark.asyncio
async def test_shutdown_during_creation_cannot_publish_session(resources, monkeypatch):
    monkeypatch.setattr(service.app.state, "redis", None, raising=False)
    entered = asyncio.Event()

    async def launch(**kwargs):
        entered.set()
        await asyncio.Event().wait()

    resources.controller.chromium.launch.side_effect = launch
    creation = asyncio.create_task(
        service.create_browser(service.BrowserCreateRequest())
    )
    await entered.wait()
    await service.shutdown()
    with pytest.raises(asyncio.CancelledError):
        await creation
    assert not service._sessions and not service._owners
    resources.controller.stop.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["start", "launch"])
async def test_shutdown_retains_late_start_or_cancellation_resistant_launch(
    resources, monkeypatch, boundary
):
    monkeypatch.setattr(service.app.state, "redis", None, raising=False)
    entered, release = asyncio.Event(), asyncio.Event()

    async def delayed(*args, **kwargs):
        entered.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            await release.wait()
        return resources.controller if boundary == "start" else resources.browser

    step(resources, boundary).side_effect = delayed
    creation = asyncio.create_task(
        service.create_browser(service.BrowserCreateRequest())
    )
    await entered.wait()
    started = time.monotonic()
    await service.shutdown()
    assert time.monotonic() - started < 0.3
    owner = next(iter(service._owners.values()))
    assert owner.phase == "quarantined"
    release.set()
    with pytest.raises((asyncio.CancelledError, service.HTTPException)):
        await creation
    await asyncio.gather(owner.startup, owner.creation, return_exceptions=True)
    await asyncio.sleep(0)
    if owner.late_cleanup is not None:
        await owner.late_cleanup
    assert not service._sessions
    assert resources.controller.stop.await_count >= 1
    assert (await service.health()).status_code == 503


@pytest.mark.asyncio
async def test_cleanup_exception_reports_stage_without_secret(resources, caplog):
    resources.page.close.side_effect = RuntimeError(
        "https://secret.test/?token=private"
    )
    response = await service.create_browser(service.BrowserCreateRequest())
    await service._destroy_session(response.id)
    assert service._owners[response.id].phase == "quarantined"
    assert "stage=page outcome=exception error_type=RuntimeError" in caplog.text
    assert "secret.test" not in caplog.text
    assert "token=private" not in caplog.text
    resources.controller.stop.assert_awaited_once()


@pytest.mark.asyncio
async def test_cleanup_timeout_reports_stage(resources, caplog):
    async def slow_close():
        await asyncio.sleep(60)

    resources.page.close.side_effect = slow_close
    response = await service.create_browser(service.BrowserCreateRequest())
    await service._destroy_session(response.id)
    assert "stage=page outcome=timeout" in caplog.text
