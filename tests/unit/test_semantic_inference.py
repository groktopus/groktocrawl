"""Deterministic tests for semantic-svc's bounded native inference runtime."""

from __future__ import annotations

import asyncio
import threading
import time

import pytest
from app import InferenceManager, InferenceOverloaded


async def _wait_for_thread_event(event: threading.Event) -> None:
    """Wait without blocking the event loop while a native stub starts."""
    for _ in range(100):
        if event.is_set():
            return
        await asyncio.sleep(0.001)
    raise AssertionError("native inference stub did not start")


async def _close(manager: InferenceManager) -> None:
    await manager.shutdown()


@pytest.mark.asyncio
async def test_blocking_rerank_does_not_stall_event_loop() -> None:
    manager = InferenceManager(max_workers=1, queue_size=1, admission_timeout=0.1)
    started = threading.Event()
    release = threading.Event()

    def delayed() -> str:
        started.set()
        assert release.wait(1.0)
        return "done"

    task = asyncio.create_task(manager.run("rerank", delayed))
    await _wait_for_thread_event(started)
    heartbeat_start = time.monotonic()
    await asyncio.sleep(0.01)
    heartbeat_elapsed = time.monotonic() - heartbeat_start
    assert heartbeat_elapsed < 0.08

    release.set()
    assert await task == "done"
    await _close(manager)


@pytest.mark.asyncio
async def test_cancellation_keeps_native_capacity_until_call_finishes() -> None:
    manager = InferenceManager(max_workers=1, queue_size=0, admission_timeout=0.01)
    started = threading.Event()
    release = threading.Event()

    def delayed() -> str:
        started.set()
        assert release.wait(1.0)
        return "native-result"

    canceled = asyncio.create_task(manager.run("rerank", delayed))
    await _wait_for_thread_event(started)
    canceled.cancel()
    with pytest.raises(asyncio.CancelledError):
        await canceled

    # The HTTP task is gone, but the native call still owns the only slot.
    with pytest.raises(InferenceOverloaded):
        await manager.run("embed", lambda: "should-wait")

    release.set()
    await asyncio.sleep(0.01)
    assert manager._in_flight == 0
    assert await manager.run("embed", lambda: "after-native") == "after-native"
    await _close(manager)


@pytest.mark.asyncio
async def test_admission_queue_is_bounded_and_interactive_work_is_prioritized() -> None:
    manager = InferenceManager(max_workers=1, queue_size=2, admission_timeout=0.01)
    first_started = threading.Event()
    first_release = threading.Event()
    order: list[str] = []

    def first_maintenance() -> str:
        first_started.set()
        assert first_release.wait(1.0)
        order.append("first-maintenance")
        return "first"

    first = asyncio.create_task(
        manager.run("migration_backfill", first_maintenance, priority="maintenance")
    )
    await _wait_for_thread_event(first_started)
    queued_maintenance = asyncio.create_task(
        manager.run(
            "migration_backfill",
            lambda: order.append("queued-maintenance") or "maintenance",
            priority="maintenance",
        )
    )
    for _ in range(100):
        if manager._queued == 1:
            break
        await asyncio.sleep(0.001)
    assert manager._queued == 1
    interactive = asyncio.create_task(
        manager.run("rerank", lambda: order.append("interactive") or "interactive")
    )
    for _ in range(100):
        if manager._queued == 2:
            break
        await asyncio.sleep(0.001)
    assert manager._queued == 2
    # Both the running call and one queued call consume the admission budget.
    with pytest.raises(InferenceOverloaded):
        await manager.run("embed", lambda: "overload")

    first_release.set()
    assert await first == "first"
    assert await interactive == "interactive"
    assert await queued_maintenance == "maintenance"
    assert order == ["first-maintenance", "interactive", "queued-maintenance"]
    await _close(manager)


@pytest.mark.asyncio
async def test_native_failure_releases_capacity_and_preserves_following_results() -> (
    None
):
    manager = InferenceManager(max_workers=1, queue_size=0, admission_timeout=0.1)

    def broken() -> None:
        raise RuntimeError("model failure")

    with pytest.raises(RuntimeError, match="model failure"):
        await manager.run("rerank", broken)
    assert manager._in_flight == 0
    assert await manager.run("rerank", lambda: [2, 1]) == [2, 1]
    await _close(manager)


@pytest.mark.asyncio
async def test_concurrent_work_never_exceeds_configured_native_capacity() -> None:
    manager = InferenceManager(max_workers=2, queue_size=2, admission_timeout=0.1)
    active = 0
    max_active = 0

    async def run_stub() -> int:
        nonlocal active, max_active

        def native() -> int:
            nonlocal active, max_active
            # This stub is intentionally short; the lock is only used to make
            # the observed concurrency deterministic across worker threads.
            active += 1
            max_active = max(max_active, active)
            time.sleep(0.01)
            active -= 1
            return max_active

        return await manager.run("embed", native)

    results = await asyncio.gather(*(run_stub() for _ in range(4)))
    assert len(results) == 4
    assert max_active == 2
    await _close(manager)


@pytest.mark.asyncio
async def test_worker_cancellation_drains_native_call_before_releasing_capacity() -> (
    None
):
    manager = InferenceManager(max_workers=1, queue_size=0, admission_timeout=0.01)
    started = threading.Event()
    release = threading.Event()

    def delayed() -> str:
        started.set()
        assert release.wait(1.0)
        return "drained"

    caller = asyncio.create_task(manager.run("rerank", delayed))
    await _wait_for_thread_event(started)
    worker = manager._workers[0]
    worker.cancel()
    await asyncio.sleep(0.01)
    assert manager._in_flight == 1
    with pytest.raises(InferenceOverloaded):
        await manager.run("embed", lambda: "blocked")

    release.set()
    with pytest.raises(asyncio.CancelledError):
        await worker
    assert await caller == "drained"
    assert manager._in_flight == 0
    await _close(manager)
