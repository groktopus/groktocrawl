import copy
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).parents[2]
MANIFEST = (
    ROOT
    / "docs"
    / "experiments"
    / "slopsearx-substrate"
    / "w11-contract-cases.json"
)
SPEC = importlib.util.spec_from_file_location(
    "validate_w11_contract_cases",
    ROOT / "scripts" / "validate_w11_contract_cases.py",
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def payload() -> dict:
    return json.loads(MANIFEST.read_text())


def test_committed_contract_cases_are_valid() -> None:
    assert MODULE.validate_cases(payload()) == []


def test_duplicate_case_identity_fails_closed() -> None:
    value = payload()
    value["cases"][1]["case_id"] = value["cases"][0]["case_id"]
    assert "case IDs must be unique" in MODULE.validate_cases(value)


def test_family_loss_fails_closed() -> None:
    value = payload()
    value["cases"] = [
        case for case in value["cases"] if case["family"] != "staged_search"
    ]
    assert any("family counts" in issue for issue in MODULE.validate_cases(value))


def test_case_without_observable_outcome_fails_closed() -> None:
    value = payload()
    value["cases"][0]["expected"] = []
    assert any(
        "expected outcomes" in issue for issue in MODULE.validate_cases(value)
    )


def test_malformed_reference_fails_closed() -> None:
    value = copy.deepcopy(payload())
    value["cases"][0]["reference_tests"] = ["a prose assertion"]
    assert any("reference_tests" in issue for issue in MODULE.validate_cases(value))


def test_missing_proof_revision_fails_closed() -> None:
    value = payload()
    del value["slopsearx_reference"]["proof_revision"]
    assert "SlopSearX proof_revision must be an exact commit" in MODULE.validate_cases(value)


def test_class_scoped_pytest_reference_is_valid() -> None:
    value = payload()
    value["cases"][0]["reference_tests"] = [
        "tests/test_example.py::TestWorkflow::test_contract"
    ]
    assert MODULE.validate_cases(value) == []
