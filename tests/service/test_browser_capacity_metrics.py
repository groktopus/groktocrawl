"""Browser metrics describe reservations and unknown cgroups without spawning work."""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "browser-svc"))
from browser_svc import app as service
from browser_svc.capacity_metrics import CapacityMetrics
from browser_svc.process_health import process_capacity

from common.browser_metrics import parse_samples


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setattr(service, "_sessions", {})
    monkeypatch.setattr(service, "_owners", {})
    monkeypatch.setattr(service, "_shutting_down", False)
    monkeypatch.setattr(service, "_capacity_metrics", CapacityMetrics())
    monkeypatch.setattr(service, "process_capacity", lambda: {"state": "unknown"})
    monkeypatch.setattr(service, "async_playwright", lambda: None)


async def snapshot():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=service.app), base_url="http://test"
    ) as client:
        response = await client.get("/metrics")
    assert response.status_code == 200
    assert response.text.count("# EOF") == 1
    return parse_samples(response.text)


def budget(root, current="7", limit="256", events="max 12\n"):
    root.mkdir(parents=True, exist_ok=True)
    for name, value in (("current", current), ("max", limit), ("events", events)):
        if value is not None:
            (root / f"pids.{name}").write_text(value)
    return (root,)


@pytest.mark.asyncio
async def test_metrics_cgroup_raw_counter_and_unlimited_transition(
    monkeypatch, tmp_path
):
    roots = budget(tmp_path)
    monkeypatch.setattr(service, "process_capacity", lambda: process_capacity(roots))
    first = await snapshot()
    assert first[("cgroup_tasks", "")] == 7
    assert first[("cgroup_task_limit", "")] == 256
    assert first[("cgroup_limit_events_total", "")] == 12
    assert (await snapshot())[("cgroup_limit_events_total", "")] == 12
    # Cgroup recreation resets the source counter; never accumulate file values.
    (tmp_path / "pids.events").write_text("max 0\n")
    (tmp_path / "pids.max").write_text("max")
    next_sample = await snapshot()
    assert next_sample[("cgroup_limit_events_total", "")] == 0
    assert ("cgroup_task_limit", "") not in next_sample
    assert next_sample[("cgroup_collection_success", "")] == 1
    (tmp_path / "pids.current").write_text("broken")
    unknown = await snapshot()
    assert ("cgroup_tasks", "") not in unknown
    assert ("cgroup_limit_events_total", "") not in unknown
    assert unknown[("cgroup_collection_success", "")] == 0


@pytest.mark.parametrize(
    "events",
    [
        None,
        "",
        "bad",
        "max -1",
        "max no",
        "max 1\nmax 2",
        "max 2 extra",
        "max " + "9" * 1024,
    ],
)
def test_missing_or_malformed_events_do_not_change_admission(tmp_path, events):
    result = process_capacity(budget(tmp_path, events=events))
    assert result["state"] == "ok"
    assert result["limit_events"] is None


def test_v1_events_fallback_and_unknown_other_fields(tmp_path):
    roots = budget(tmp_path / "pids", events="other 2\nmax 9\n")
    result = process_capacity((tmp_path / "missing", *roots))
    assert result["limit_events"] == 9


@pytest.mark.asyncio
async def test_phases_admission_and_zero_series(monkeypatch):
    monkeypatch.setattr(
        service,
        "_owners",
        {
            str(i): SimpleNamespace(phase=phase)
            for i, phase in enumerate(("active", "closing", "quarantined"))
        },
    )
    monkeypatch.setattr(service, "_sessions", {"active": object()})
    values = await snapshot()
    assert values[("active_sessions", "")] == 1
    assert values[("reserved_sessions", "")] == 3
    assert values[("owners", "creating")] == 0
    assert values[("owners", "closing")] == 1
    assert values[("owners", "quarantined")] == 1
    assert values[("admission_available", "")] == 0
    with pytest.raises(service.HTTPException):
        await service.create_browser(service.BrowserCreateRequest())
    values = await snapshot()
    assert values[("admission_rejections_total", "cleanup_incomplete")] == 1
    assert values[("admission_rejections_total", "creation_in_progress")] == 0


@pytest.mark.asyncio
async def test_incomplete_cleanup_counts_owner_once():
    owner = service.BrowserOwner()
    owner.close = AsyncMock(return_value=False)
    service._owners["one"] = owner
    first = service._begin_cleanup("one", owner)
    second = service._begin_cleanup("one", owner)
    assert first is second
    await first
    values = await snapshot()
    assert values[("cleanup_incomplete_total", "")] == 1
    assert values[("owners", "quarantined")] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["timeout", "error", "cancelled"])
async def test_creation_failure_classification(monkeypatch, reason):
    entered = asyncio.Event()

    async def initialize(owner):
        entered.set()
        if reason == "error":
            raise RuntimeError("secret must not become a label")
        await asyncio.Event().wait()

    monkeypatch.setattr(service, "_initialize_browser", initialize)
    monkeypatch.setattr(service.lifecycle, "CREATE_TIMEOUT", 0.01)
    task = asyncio.create_task(service.create_browser(service.BrowserCreateRequest()))
    await entered.wait()
    if reason == "cancelled":
        task.cancel()
    with pytest.raises((service.HTTPException, asyncio.CancelledError)):
        await task
    values = await snapshot()
    assert values[("creation_failures_total", reason)] == 1
    assert (
        sum(
            value
            for (name, _), value in values.items()
            if name == "creation_failures_total"
        )
        == 1
    )
    assert values[("reserved_sessions", "")] == 0


def test_unknown_event_rejected():
    with pytest.raises(ValueError):
        CapacityMetrics().increment("private_url", "https://secret.example")
