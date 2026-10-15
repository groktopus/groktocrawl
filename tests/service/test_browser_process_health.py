"""Health reports resource pressure without launching browser subprocesses."""

import sys
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "browser-svc"))

import browser_svc.app as browser_app
from browser_svc.process_health import MIN_TASK_HEADROOM, process_capacity


def _budget(root, current="7", limit="256"):
    root.mkdir(parents=True, exist_ok=True)
    (root / "pids.current").write_text(current)
    (root / "pids.max").write_text(limit)
    return (root,)


@pytest.mark.parametrize(
    "current,limit", [("256", "256"), ("257", "256"), ("250", "256"), ("1", "0")]
)
@pytest.mark.asyncio
async def test_exhausted_budget_degrades_transport_without_launch(
    monkeypatch, tmp_path, current, limit
):
    roots = _budget(tmp_path, current, limit)
    monkeypatch.setattr(
        browser_app, "process_capacity", lambda: process_capacity(roots)
    )
    launch = AsyncMock(side_effect=AssertionError("health must not start Playwright"))
    monkeypatch.setattr(browser_app, "async_playwright", launch)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=browser_app.app), base_url="http://test"
    ) as client:
        response = await client.get("/health")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["active_sessions"] == len(browser_app._sessions)
    assert body["process_capacity"]["reason"] == "process_capacity_low"
    assert str(tmp_path) not in response.text
    launch.assert_not_called()


@pytest.mark.parametrize(
    "current,limit", [("7", "256"), ("240", "256"), ("1000", "max")]
)
def test_ordinary_and_unlimited_budget(tmp_path, current, limit):
    result = process_capacity(_budget(tmp_path, current, limit))
    assert result["state"] == "ok"
    assert result["minimum_task_headroom"] == MIN_TASK_HEADROOM
    assert result["task_limit"] == (None if limit == "max" else int(limit))


@pytest.mark.parametrize(
    "current,limit",
    [
        ("invalid", "256"),
        ("-1", "256"),
        ("1", "-2"),
        ("1", "broken"),
        ("1", "9" * 200),
    ],
)
def test_invalid_budget_does_not_claim_capacity(tmp_path, current, limit):
    roots = _budget(tmp_path, current, limit)
    assert process_capacity(roots) == {
        "state": "unknown",
        "reason": "process_budget_unavailable",
    }


def test_missing_controller_and_v1_fallback(tmp_path):
    missing = tmp_path / "missing"
    assert process_capacity((missing,))["state"] == "unknown"
    v1 = tmp_path / "pids"
    _budget(v1)
    assert process_capacity((missing, v1))["state"] == "ok"


@pytest.mark.asyncio
async def test_unknown_capacity_preserves_health_without_false_readiness(monkeypatch):
    monkeypatch.setattr(browser_app, "process_capacity", lambda: process_capacity(()))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=browser_app.app), base_url="http://test"
    ) as client:
        response = await client.get("/health")
    assert response.status_code == 200
    assert response.json()["process_capacity"]["state"] == "unknown"
