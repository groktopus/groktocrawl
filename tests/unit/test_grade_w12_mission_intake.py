import importlib.util
from pathlib import Path

from jsonschema import Draft202012Validator

SCRIPT = Path("scripts/grade_w12_mission_intake.py")
SPEC = importlib.util.spec_from_file_location("grade_w12_mission_intake", SCRIPT)
assert SPEC and SPEC.loader
grader = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(grader)


def test_intake_grade_schema_is_strict():
    schema = grader.intake_grade_schema()
    Draft202012Validator.check_schema(schema)
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])


def test_empty_intake_candidate_directory_loads_nothing(tmp_path):
    (tmp_path / "public/intake").mkdir(parents=True)
    assert grader.load_intake_candidates(tmp_path) == {}
