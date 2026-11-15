"""Container recovery guard tests for uncertain scraper browser cleanup."""

import asyncio

import pytest


@pytest.mark.asyncio
async def test_recycler_is_disabled_unless_compose_opt_in(monkeypatch):
    from scraper import browser_recovery as recovery

    monkeypatch.delenv("SCRAPER_CONTAINER_RECYCLE_ON_QUARANTINE", raising=False)
    recycler = recovery.ContainerRecycler(
        exit_process=lambda _code: None, grace_seconds=0
    )

    recycler.schedule()

    assert recycler.task is None


@pytest.mark.asyncio
async def test_recycler_checks_dedicated_container_guard(monkeypatch):
    from scraper import browser_recovery as recovery

    monkeypatch.setenv("SCRAPER_CONTAINER_RECYCLE_ON_QUARANTINE", "1")
    monkeypatch.setattr(recovery, "dedicated_container", lambda: False)
    recycler = recovery.ContainerRecycler(
        exit_process=lambda _code: None, grace_seconds=0
    )

    with pytest.raises(RuntimeError, match="dedicated container"):
        recycler.schedule()
    assert recycler.task is None


@pytest.mark.asyncio
async def test_recycler_is_single_flight_and_exits_after_fixed_grace(monkeypatch):
    from scraper import browser_recovery as recovery

    exits = []
    monkeypatch.setenv("SCRAPER_CONTAINER_RECYCLE_ON_QUARANTINE", "1")
    monkeypatch.setattr(recovery, "dedicated_container", lambda: True)
    recycler = recovery.ContainerRecycler(exit_process=exits.append, grace_seconds=0.01)

    recycler.schedule()
    first = recycler.task
    recycler.schedule()
    assert recycler.task is first
    await first

    assert exits == [75]


@pytest.mark.asyncio
async def test_normal_shutdown_cancels_pending_recycle(monkeypatch):
    from scraper import browser_recovery as recovery

    exits = []
    monkeypatch.setenv("SCRAPER_CONTAINER_RECYCLE_ON_QUARANTINE", "1")
    monkeypatch.setattr(recovery, "dedicated_container", lambda: True)
    recycler = recovery.ContainerRecycler(exit_process=exits.append, grace_seconds=60)

    recycler.schedule()
    await asyncio.sleep(0)
    await recycler.stop()

    assert exits == []
