from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[2] / "scripts/run_jev_continuation_monitoring.py"
SPEC = importlib.util.spec_from_file_location("jev_continuation_monitoring", SCRIPT)
runner = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(runner)


def continuation_case(case_id: str = "c-01") -> dict:
    return {
        "id": case_id,
        "study": "continuation",
        "split": "validation",
        "stratum": "unmet",
        "state": {
            "research_question": "Question",
            "obligation": "One claim",
            "first_pass_evidence": [],
        },
        "evaluation": {
            "reference": {"status": "unmet", "search_needed": "yes"},
            "follow_up_pool": [{"content": "private evaluator-only text"}],
        },
    }


def test_accepts_bounded_frozen_packet() -> None:
    packet = {
        "schema_version": "jev-continuation-monitoring-packet/1",
        "cases": [continuation_case()],
    }
    assert runner.validate_packet(packet)[0]["id"] == "c-01"


def test_rejects_over_budget_and_duplicate_ids() -> None:
    duplicate = continuation_case()
    packet = {
        "schema_version": "jev-continuation-monitoring-packet/1",
        "cases": [duplicate, duplicate],
    }
    with pytest.raises(ValueError, match="unique"):
        runner.validate_packet(packet)

    cases = []
    for index in range(21):
        case = continuation_case(f"c-{index:02}")
        case["split"] = "validation"
        cases.append(case)
    with pytest.raises(ValueError, match="20-call validation"):
        runner.validate_packet(
            {"schema_version": "jev-continuation-monitoring-packet/1", "cases": cases}
        )


def test_choice_requires_frozen_distribution_and_model() -> None:
    response = {
        "model": runner.MODEL,
        "answers": {
            "continuation_decision": {
                "choice": "search",
                "probabilities": {"search": 0.8, "no_search": 0.1, "uncertain": 0.1},
                "confidence": 0.8,
            }
        },
        "usage": {"input_tokens": 100, "output_tokens": 5},
    }
    assert runner._choice_answer(response, "continuation")["choice"] == "search"
    response["model"] = "jev-latest"
    with pytest.raises(ValueError, match="returned model"):
        runner._choice_answer(response, "continuation")

    response["model"] = runner.MODEL
    response["answers"]["continuation_decision"]["probabilities"]["uncertain"] = 0.3
    with pytest.raises(ValueError, match="sum"):
        runner._choice_answer(response, "continuation")


def test_request_excludes_labels_and_follow_up_evidence() -> None:
    case = continuation_case()
    request = runner._request_for_case(case)
    encoded = str(request)
    assert "private evaluator-only text" not in encoded
    assert "search_needed" not in encoded
    assert request["state"] == case["state"]
