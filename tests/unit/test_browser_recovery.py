"""Container recycling never re-admits uncertain browser ownership."""

import asyncio
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "browser-svc"))
from browser_svc import recovery


@pytest.mark.asyncio
async def test_disabled_never_exits(monkeypatch):
    monkeypatch.delenv("BROWSER_CONTAINER_RECYCLE_ON_QUARANTINE", raising=False)
    exit_process = Mock()
    recycler = recovery.ContainerRecycler(exit_process=exit_process, grace_seconds=0)
    recycler.schedule()
    await asyncio.sleep(0)
    assert recycler.task is None
    exit_process.assert_not_called()


@pytest.mark.asyncio
async def test_enabled_single_flight_grace(monkeypatch):
    monkeypatch.setenv("BROWSER_CONTAINER_RECYCLE_ON_QUARANTINE", "1")
    monkeypatch.setattr(recovery, "dedicated_container", lambda: True)
    exit_process = Mock()
    recycler = recovery.ContainerRecycler(exit_process=exit_process, grace_seconds=0.02)
    recycler.schedule()
    task = recycler.task
    recycler.schedule()
    assert recycler.task is task
    exit_process.assert_not_called()
    await task
    exit_process.assert_called_once_with(75)


@pytest.mark.asyncio
async def test_shutdown_cancels_pending_recycle(monkeypatch):
    monkeypatch.setenv("BROWSER_CONTAINER_RECYCLE_ON_QUARANTINE", "1")
    monkeypatch.setattr(recovery, "dedicated_container", lambda: True)
    exit_process = Mock()
    recycler = recovery.ContainerRecycler(exit_process=exit_process, grace_seconds=60)
    recycler.schedule()
    await recycler.stop()
    exit_process.assert_not_called()
    assert recycler.task.done()
    recycler.schedule()
    assert recycler.task.done()


@pytest.mark.asyncio
async def test_host_execution_cannot_recycle(monkeypatch):
    monkeypatch.setenv("BROWSER_CONTAINER_RECYCLE_ON_QUARANTINE", "1")
    monkeypatch.setattr(recovery, "dedicated_container", lambda: False)
    recycler = recovery.ContainerRecycler(exit_process=Mock(), grace_seconds=0)
    with pytest.raises(RuntimeError, match="dedicated container"):
        recycler.schedule()


def test_process_exit_is_nonzero_and_bounded():
    import subprocess

    script = """
import asyncio, os
from browser_svc import recovery
os.environ["BROWSER_CONTAINER_RECYCLE_ON_QUARANTINE"] = "1"
recovery.dedicated_container = lambda: True
async def main():
    recycler = recovery.ContainerRecycler(grace_seconds=0.01)
    recycler.schedule()
    await recycler.task
asyncio.run(main())
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        env={
            **__import__("os").environ,
            "PYTHONPATH": str(Path(recovery.__file__).parents[1]),
        },
        capture_output=True,
        timeout=5,
    )
    assert result.returncode == 75


@pytest.mark.asyncio
async def test_quarantine_schedules_only_outside_shutdown(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from browser_svc import app as service

    recycler = Mock()
    monkeypatch.setattr(service, "_recycler", recycler)
    monkeypatch.setattr(service, "_shutting_down", False)
    owner = SimpleNamespace(close=AsyncMock(return_value=False), phase="closing")
    await service._retire_owner("test", owner)
    assert owner.phase == "quarantined"
    recycler.schedule.assert_called_once()
    monkeypatch.setattr(service, "_shutting_down", True)
    await service._retire_owner("test", owner)
    recycler.schedule.assert_called_once()
