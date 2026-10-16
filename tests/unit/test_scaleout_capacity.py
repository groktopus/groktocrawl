"""ADR-0089 scale-out sizing must match scraper and API admission."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from deploy import scaleout
from deploy.scaleout import browser_admission_units, compose_command


@pytest.mark.parametrize("replicas,units", [(1, 128), (2, 256), (3, 384), (4, 512)])
def test_browser_admission_budget_tracks_replica_count(replicas, units):
    assert browser_admission_units(replicas) == units
    assert compose_command(replicas)[-2:] == ["--scale", f"scraper-svc={replicas}"]


@pytest.mark.parametrize("replicas", [0, 5])
def test_unsupported_replica_count_fails_closed(replicas):
    with pytest.raises(ValueError):
        browser_admission_units(replicas)


def test_dry_run_exposes_matched_capacity_without_docker():
    root = Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    env.pop("ADMISSION_BROWSER_LIMIT", None)
    result = subprocess.run(
        [sys.executable, "deploy/scaleout.py", "--replicas", "4", "--dry-run"],
        cwd=root,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    plan = json.loads(result.stdout)
    assert plan["aggregate_browser_slots"] == 64
    assert plan["api_browser_admission_units"] == 512


def test_conflicting_explicit_api_budget_is_rejected():
    root = Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    env["ADMISSION_BROWSER_LIMIT"] = "128"
    result = subprocess.run(
        [sys.executable, "deploy/scaleout.py", "--replicas", "4", "--dry-run"],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "expected 512" in result.stderr


def test_live_scale_change_requires_api_job_drain(monkeypatch):
    monkeypatch.delenv("ADMISSION_BROWSER_LIMIT", raising=False)
    monkeypatch.setattr(scaleout, "running_replicas", lambda _env: 1)
    monkeypatch.setattr(sys, "argv", ["scaleout.py", "--replicas", "2"])
    with pytest.raises(SystemExit):
        scaleout.main()


def test_downscale_requires_backend_drain(monkeypatch):
    monkeypatch.delenv("ADMISSION_BROWSER_LIMIT", raising=False)
    monkeypatch.setattr(scaleout, "running_replicas", lambda _env: 4)
    monkeypatch.setattr(
        sys,
        "argv",
        ["scaleout.py", "--replicas", "2", "--allow-api-restart"],
    )
    with pytest.raises(SystemExit):
        scaleout.main()
