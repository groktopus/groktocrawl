import importlib.util
from pathlib import Path

SCRIPT = Path("scripts/analyze_w12_mission_experiment.py")
SPEC = importlib.util.spec_from_file_location("analyze_w12_mission_experiment", SCRIPT)
assert SPEC and SPEC.loader
analysis = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(analysis)


def test_percentile_uses_nearest_rank():
    assert analysis.percentile([1, 2, 3, 4, 5], 0.95) == 5
    assert analysis.percentile([], 0.95) is None


def test_scope_reduction_does_not_claim_improvement_from_two_zeroes():
    assert analysis.relative_reduction(10, 5) == 0.5
    assert analysis.relative_reduction(0, 0) == 0.0
    assert analysis.relative_reduction(0, 1) == -float("inf")


def test_partial_credit_and_conservative_sensitivity_are_distinct():
    grade = {
        "obligation_grades": {
            "o1": {"status": "closed"},
            "o2": {"status": "partial"},
            "o3": {"status": "open"},
        }
    }
    weights = {"o1": 5, "o2": 3, "o3": 1}
    assert analysis.coverage_for(grade, weights) == (6.5, 9.0)
    assert analysis.coverage_for(grade, weights, equal_weights=True) == (1.5, 3.0)
    assert analysis.coverage_for(grade, weights, partial_as_open=True) == (5.0, 9.0)


def test_repetition_gate_fails_closed_when_an_arm_is_missing():
    rows = [
        {
            "repetition": repetition,
            "stratum": "ambiguous",
            "arm": "control",
            "weighted_coverage": 1.0,
            "scope_violations": 0,
        }
        for repetition in (1, 2, 3)
    ]
    gates = analysis.difficult_repetition_gates(rows)
    assert all(item["coverage_gain"] is None for item in gates)
    assert not any(item["passes"] for item in gates)


def test_markdown_renders_the_mechanical_decision():
    rendered = analysis.render_markdown(
        {
            "disposition": "reject",
            "gates": {
                "target_effect": False,
                "anchor_regression": False,
                "operational": True,
                "hard_boundary": True,
            },
            "intake": {
                "action_accuracy": 1.0,
                "mean_required_field_recall": 90,
                "hard_boundary_failures": 0,
                "total_correction_actions": 1,
            },
            "repetition_gates": [
                {
                    "repetition": 1,
                    "coverage_gain": 0.05,
                    "scope_violation_relative_reduction": 0.5,
                    "passes": False,
                }
            ],
        }
    )
    assert "Decision: **reject**" in rendered
    assert "5.0%" in rendered
