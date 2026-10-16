import importlib.util
from pathlib import Path

SCRIPT = Path("scripts/adjudicate_w12_mission_experiment.py")
SPEC = importlib.util.spec_from_file_location("adjudicate_w12", SCRIPT)
assert SPEC and SPEC.loader
adjudicator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(adjudicator)


class Obligation:
    def __init__(self, obligation_id, weight):
        self.obligation_id = obligation_id
        self.weight = weight


class Mission:
    obligations = (Obligation("o1", 5),)


class Case:
    reference_mission = Mission()


def test_selection_includes_seeded_sample_and_high_weight_nonclosure():
    records = {
        f"candidate-{index}": {
            "case_id": "case",
            "grade": {
                "hard_boundary_failure": False,
                "obligation_grades": {
                    "o1": {"status": "open" if index == 0 else "closed"}
                },
            },
        }
        for index in range(10)
    }
    selected = adjudicator.select_adjudications(
        grade_records=records, cases={"case": Case()}, seed=20260919
    )
    assert len(selected) >= 2
    first = next(item for item in selected if item["candidate_id"] == "candidate-0")
    assert "high_weight_obligation_not_closed" in first["reasons"]
    assert sum(
        "seeded_20_percent_of_remaining" in item["reasons"] for item in selected
    ) == 2
    assert [item["position"] for item in selected] == list(
        range(1, len(selected) + 1)
    )


def test_canonical_digest_is_stable_across_mapping_order():
    assert adjudicator.canonical_digest({"a": 1, "b": 2}) == (
        adjudicator.canonical_digest({"b": 2, "a": 1})
    )
