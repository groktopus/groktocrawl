import asyncio
import importlib.util
from pathlib import Path

import pytest
from agent.experimental.model_review import ModelReply

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "candidate_d_grading", ROOT / "scripts" / "run_candidate_d_grading.py"
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _row(arm: str, trial: int = 1):
    answer = (
        {"answer": "text", "citations": ["s1"]}
        if arm == "A"
        else {"text": "text", "citations": ["s1"]}
    )
    return {
        "case_id": "c1",
        "trial": trial,
        "arm": arm,
        "answer": answer,
        "status": "completed",
    }


def test_blind_schedule_is_deterministic_and_does_not_reveal_arm():
    first_schedule, first_map = MODULE.blind_schedule([_row("A"), _row("D")], 42)
    second_schedule, second_map = MODULE.blind_schedule([_row("A"), _row("D")], 42)
    assert first_schedule == second_schedule
    assert first_map == second_map
    assert len({row["blind_id"] for row in first_schedule}) == 2
    assert all(set(row) == {"blind_id", "input_sha256"} for row in first_schedule)


def test_normalization_gives_both_arms_the_same_shape():
    assert MODULE.normalized_answer(_row("A")) == MODULE.normalized_answer(_row("D"))


def test_grade_validation_enforces_exact_subquestion_denominator():
    value = {
        "blind_id": "blind",
        "dimensions": dict.fromkeys(MODULE.DIMENSIONS, "pass"),
        "overall_label": "ready_to_use",
        "required_subquestions": [{"id": "q1", "outcome": "addressed"}],
        "high_consequence_failure": False,
        "dangerous_unsupported_recommendation": False,
        "rationale": "Supported by the supplied evidence.",
    }
    assert MODULE.validate_grade(value, "blind", {"q1"}) == value
    with pytest.raises(ValueError, match="denominator"):
        MODULE.validate_grade(value, "blind", {"q1", "q2"})


def test_meter_stops_after_ceiling_and_tracks_transport_failure_streak(monkeypatch):
    class Transport:
        calls = 0

        async def __call__(self, request):
            self.calls += 1
            if self.calls == 1:
                raise ValueError("down")
            return ModelReply(b"{}", "local", 1, 1, "digest")

    request = MODULE.ReviewRequest("system", b"{}", "local", 10, None)
    meter = MODULE.MeteredTransport(Transport())
    with pytest.raises(ValueError):
        asyncio.run(meter.complete(request))
    assert meter.consecutive_transport_failures == 1
    asyncio.run(meter.complete(request))
    assert meter.consecutive_transport_failures == 0
    meter.calls = MODULE.MAX_CALLS
    with pytest.raises(ValueError, match="ceiling"):
        asyncio.run(meter.complete(request))
