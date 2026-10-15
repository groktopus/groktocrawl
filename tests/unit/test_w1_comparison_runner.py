"""Fail-closed tests for the private W1 comparison runner."""

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[2] / "scripts/run_w1_comparison.py"
SPEC = importlib.util.spec_from_file_location("w1_comparison", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_schedule_is_complete_reproducible_and_paired():
    cases = [{"case_id": f"case-{i}"} for i in range(30)]
    first = MODULE.schedule(cases, seed=20260909, trials=5)
    second = MODULE.schedule(cases, seed=20260909, trials=5)
    assert first == second
    assert len(first) == 300
    assert {(r["case_id"], r["trial"], r["arm"]) for r in first} == {
        (case["case_id"], trial, arm)
        for case in cases
        for trial in range(1, 6)
        for arm in MODULE.ARMS
    }


def test_schedule_rejects_changed_trial_count():
    with pytest.raises(ValueError, match="exactly five"):
        MODULE.schedule([{"case_id": "case-1"}], seed=1, trials=4)


def test_private_source_url_is_contract_valid_and_non_routable():
    assert MODULE.private_source_url("source:one") == "https://w1.invalid/source/source-one"


def test_packet_refuses_when_public_authorization_is_false(tmp_path):
    preflight = tmp_path / "preflight.json"
    preflight.write_text(json.dumps({"comparison_authorized": False}))
    with pytest.raises(ValueError, match="not authorized"):
        MODULE.load_authorized_packet(tmp_path, preflight)


def test_packet_refuses_wrong_authorized_scope(tmp_path):
    preflight = tmp_path / "preflight.json"
    preflight.write_text(json.dumps({"comparison_authorized": True, "authorized_scope": ["A"]}))
    with pytest.raises(ValueError, match="scope"):
        MODULE.load_authorized_packet(tmp_path, preflight)


def test_packet_refuses_unapproved_clean_rerun(tmp_path):
    preflight = tmp_path / "preflight.json"
    preflight.write_text(
        json.dumps(
            {
                "comparison_authorized": True,
                "authorized_scope": ["A", "B"],
                "comparison_execution": {"clean_rerun_authorized": False},
            }
        )
    )
    with pytest.raises(ValueError, match="rerun"):
        MODULE.load_authorized_packet(tmp_path, preflight)
