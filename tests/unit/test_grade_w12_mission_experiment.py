import importlib.util
import json
from pathlib import Path

from jsonschema import Draft202012Validator

SCRIPT = Path("scripts/grade_w12_mission_experiment.py")
SPEC = importlib.util.spec_from_file_location("grade_w12_mission_experiment", SCRIPT)
assert SPEC and SPEC.loader
grader = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(grader)


def cases():
    from agent.experimental.research_mission import MissionExperimentCorpus

    return MissionExperimentCorpus.model_validate(
        json.loads(
            Path("docs/experiments/research-mission/w12.1-cases.json").read_bytes()
        )
    ).cases


def test_grade_schema_is_strict_and_closes_every_obligation():
    for case in cases():
        schema = grader.grade_schema(case)
        Draft202012Validator.check_schema(schema)
        assert schema["additionalProperties"] is False
        obligations = schema["properties"]["obligation_grades"]
        assert obligations["additionalProperties"] is False
        assert set(obligations["required"]) == {
            item.obligation_id for item in case.reference_mission.obligations
        }


def test_candidate_loader_rejects_duplicate_identity(tmp_path):
    trials = tmp_path / "public/trials"
    trials.mkdir(parents=True)
    record = {
        "status": "completed",
        "sealed_candidate": {
            "candidate_id": "candidate-1",
            "answer": "x",
            "citations": [],
            "claims": [],
        },
    }
    (trials / "a.json").write_text(json.dumps(record))
    (trials / "b.json").write_text(json.dumps(record))
    try:
        grader.load_candidates(tmp_path)
    except ValueError as error:
        assert "duplicate" in str(error)
    else:
        raise AssertionError("duplicate candidate identity was accepted")
