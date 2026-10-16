from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).parents[2] / "scripts/evaluate_jev_shadow_policy.py"
SPEC = importlib.util.spec_from_file_location("jev_shadow_policy", SCRIPT)
policy = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(policy)


def test_threshold_sweep_uses_search_probability_not_confidence() -> None:
    cases = [
        {
            "id": "met",
            "study": "continuation",
            "split": "validation",
            "stratum": "met",
            "evaluation": {"reference": {"status": "met", "search_needed": "no"}},
        },
        {
            "id": "unmet",
            "study": "continuation",
            "split": "validation",
            "stratum": "unmet",
            "evaluation": {"reference": {"status": "unmet", "search_needed": "yes"}},
        },
    ]
    packet = {"cases": cases}
    receipts = {
        "records": [
            {
                "id": "met",
                "status": "completed",
                "answer": {
                    "choice": "search",
                    "probabilities": {"search": 0.87},
                    "confidence": 0.1,
                },
            },
            {
                "id": "unmet",
                "status": "completed",
                "answer": {
                    "choice": "search",
                    "probabilities": {"search": 0.97},
                    "confidence": 0.99,
                },
            },
        ]
    }
    result = policy.analyze(packet, receipts)
    at_90 = result["threshold_sweep"][2]
    assert at_90["threshold_triggered_ids"] == ["unmet"]
    assert at_90["reference_yes_triggered_ids"] == ["unmet"]
    assert at_90["reference_no_triggered_ids"] == []
    assert at_90["reference_yes_missed_ids"] == []
    rows = result["validation_rows"]
    assert rows[0]["confidence_separate_field"] == 0.1
    assert rows[0]["search_probability"] == 0.87


def test_uncertain_or_low_probability_does_not_force_stop_and_fail_falls_back() -> None:
    assert (
        policy.continuation_control(
            call_status="completed",
            probability=0.6,
            threshold=0.9,
            addressable_gap=True,
        )
        == "defer_to_existing_caller_flow"
    )
    assert (
        policy.continuation_control(
            call_status="proxy_error",
            probability=None,
            threshold=0.9,
            addressable_gap=True,
        )
        == "fallback_to_existing_caller_flow"
    )
    assert (
        policy.continuation_control(
            call_status="completed",
            probability=0.99,
            threshold=0.9,
            addressable_gap=False,
        )
        == "leave_unresolved_without_repeated_search"
    )


def test_monitoring_failure_never_suppresses_deterministic_change() -> None:
    assert (
        policy.monitoring_control(
            confirmed_change=True, fetch_ok=True, call_status="proxy_error"
        )
        == "deliver_deterministic_change_without_jev_annotation"
    )
    assert (
        policy.monitoring_control(
            confirmed_change=False, fetch_ok=False, call_status="not_called"
        )
        == "report_fetch_unavailable_without_semantic_judgment"
    )
