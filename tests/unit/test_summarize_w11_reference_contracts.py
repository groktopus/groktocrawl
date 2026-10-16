import json
from pathlib import Path

from scripts.summarize_w11_reference_contracts import summarize


def _junit(path: Path, cases: list[tuple[str, str, bool]]) -> Path:
    body = "".join(
        f'<testcase classname="{classname}" name="{name}">{"<skipped />" if skipped else ""}</testcase>'
        for classname, name, skipped in cases
    )
    path.write_text(f'<testsuite tests="{len(cases)}">{body}</testsuite>')
    return path


def test_skipped_reference_can_be_proved_by_valkey_suite(tmp_path: Path) -> None:
    local = _junit(tmp_path / "local.xml", [("tests.test_x", "test_a", True)])
    valkey = _junit(tmp_path / "valkey.xml", [("tests.test_x", "test_a", False)])
    manifest = {
        "slopsearx_reference": {"version": "0.5.0", "source_revision": "a" * 40},
        "cases": [{"case_id": "w11-x-01", "family": "x", "reference_tests": ["tests/test_x.py::test_a"]}],
    }
    result = summarize(manifest, local, valkey)
    assert result["all_cases_passed"] is True
    assert result["cases"][0]["references"][0]["proof"] == "isolated_valkey"
    json.dumps(result)


def test_class_scoped_reference_is_matched(tmp_path: Path) -> None:
    local = _junit(tmp_path / "local.xml", [("tests.test_x.TestThing", "test_a", False)])
    valkey = _junit(tmp_path / "valkey.xml", [])
    manifest = {
        "slopsearx_reference": {"version": "0.5.0", "source_revision": "a" * 40},
        "cases": [{"case_id": "w11-x-01", "family": "x", "reference_tests": ["tests/test_x.py::TestThing::test_a"]}],
    }
    assert summarize(manifest, local, valkey)["all_cases_passed"] is True
