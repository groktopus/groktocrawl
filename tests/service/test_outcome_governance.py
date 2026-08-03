"""Contract tests for skip/xfail metadata and outcome reporting."""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import tests.conftest as outcome_conftest
from tests.conftest import (
    _outcome_entry,
    _write_outcome_reports,
)
from tests.outcome_governance import CLASSIFICATIONS, governed_skip, validate_metadata


def _attribute_path(node: ast.AST) -> str | None:
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def _governance_violations(root: Path) -> list[str]:
    violations = []
    excluded = {"outcome_governance.py", "test_outcome_governance.py"}
    for path in sorted(root.rglob("*.py")):
        if path.name in excluded:
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                function = _attribute_path(node.func)
                if function in {"pytest.skip", "pytest.xfail", "pytest.importorskip"}:
                    violations.append(f"{path}:{node.lineno}: direct {function}")
                if function == "governed_skip":
                    keywords = {keyword.arg for keyword in node.keywords}
                    required = {"owner", "issue", "classification"}
                    if missing := required - keywords:
                        violations.append(
                            f"{path}:{node.lineno}: missing {sorted(missing)}"
                        )
                    if "review_date" not in keywords and "environment" not in keywords:
                        violations.append(
                            f"{path}:{node.lineno}: missing review_date/environment"
                        )
                if function in {
                    "pytest.mark.skip",
                    "pytest.mark.skipif",
                    "pytest.mark.xfail",
                }:
                    keywords = {keyword.arg for keyword in node.keywords}
                    required = {
                        "reason",
                        "owner",
                        "issue",
                        "classification",
                    }
                    if missing := required - keywords:
                        violations.append(
                            f"{path}:{node.lineno}: missing {sorted(missing)}"
                        )
                    if "review_date" not in keywords and "environment" not in keywords:
                        violations.append(
                            f"{path}:{node.lineno}: missing review_date/environment"
                        )
                    if function == "pytest.mark.xfail":
                        strict = next(
                            (
                                keyword.value
                                for keyword in node.keywords
                                if keyword.arg == "strict"
                            ),
                            None,
                        )
                        if (
                            not isinstance(strict, ast.Constant)
                            or strict.value is not True
                        ):
                            violations.append(
                                f"{path}:{node.lineno}: xfail is not strict"
                            )
    return violations


def test_all_root_test_outcomes_have_governance_metadata():
    violations = _governance_violations(Path(__file__).parents[1])
    assert not violations, "\n".join(violations)


def test_metadata_validation_requires_review_or_environment():
    with pytest.raises(ValueError, match="review_date or environment"):
        validate_metadata(
            reason="needs a service",
            owner="repository-maintainer",
            issue="#502",
            classification="retained",
        )
    with pytest.raises(ValueError, match="unknown governance classification"):
        validate_metadata(
            reason="needs a service",
            owner="repository-maintainer",
            issue="#502",
            classification="maybe",
            environment="service is unavailable",
        )
    assert {
        "retained",
        "fixed/re-enabled",
        "quarantined",
        "deleted",
    } == CLASSIFICATIONS


def test_ast_rejects_non_strict_xfail(tmp_path):
    test_file = tmp_path / "test_bad.py"
    test_file.write_text(
        "import pytest\n\n"
        "@pytest.mark.xfail(strict="
        "False, reason='known', owner='o', issue='#502', "
        "classification='quarantined', environment='ci')\n"
        "def test_bad(): pass\n"
    )
    violations = _governance_violations(tmp_path)
    assert any("xfail is not strict" in violation for violation in violations)


def test_ast_rejects_ungoverned_runtime_skip(tmp_path):
    test_file = tmp_path / "test_bad_runtime.py"
    test_file.write_text(
        "from tests.outcome_governance import governed_skip\n\n"
        "def test_bad():\n"
        "    governed_skip('known', owner='o', issue='#502', "
        "environment='ci')\n"
    )
    violations = _governance_violations(tmp_path)
    assert any("missing ['classification']" in violation for violation in violations)


def test_governed_skip_preserves_reason_and_metadata(monkeypatch):
    with pytest.raises(pytest.skip.Exception, match="Docker is unavailable") as caught:
        governed_skip(
            "Docker is unavailable",
            owner="repository-maintainer",
            issue="#502",
            classification="retained",
            environment="Docker daemon is unavailable",
        )
    assert "classification=retained" in str(caught.value)
    assert "environment=Docker daemon is unavailable" in str(caught.value)


def test_outcome_report_contains_counts_and_entries(tmp_path, monkeypatch):
    path = tmp_path / "qa-outcomes.json"
    monkeypatch.setenv("QA_OUTCOME_PATH", str(path))
    config = type("Config", (), {})()
    config._qa_outcomes = [
        {
            "nodeid": "tests/unit/test_example.py::test_ok",
            "status": "passed",
            "reason": None,
            "metadata": {},
        },
        {
            "nodeid": "tests/integration/test_example.py::test_skip",
            "status": "skipped",
            "reason": "service unavailable",
            "metadata": {"issue": "#502"},
        },
    ]
    _write_outcome_reports(config)
    payload = json.loads(path.read_text())
    assert payload["schema_version"] == 1
    assert payload["counts"] == {
        "passed": 1,
        "failed": 0,
        "skipped": 1,
        "xfailed": 0,
        "xpassed": 0,
    }
    assert path.with_suffix(".md").exists()


def test_passed_outcome_drops_governance_metadata():
    report = SimpleNamespace(passed=True, skipped=False, failed=False, longrepr=None)
    entry = _outcome_entry(
        "tests/unit/test_example.py::test_ok",
        report,
        {"owner": "repository-maintainer", "issue": "#502"},
    )
    assert entry["status"] == "passed"
    assert entry["metadata"] == {}


def test_failed_outcome_preserves_reason():
    report = SimpleNamespace(
        passed=False,
        skipped=False,
        failed=True,
        longrepr="assertion failed: expected 1, got 2",
    )
    entry = _outcome_entry("tests/unit/test_example.py::test_broken", report)
    assert entry["status"] == "failed"
    assert entry["reason"] == "assertion failed: expected 1, got 2"


def test_sessionfinish_fallback_removes_worker_markdown(tmp_path, monkeypatch):
    report_path = tmp_path / "outcomes.json"
    worker_json = tmp_path / "outcomes.gw0.json"
    worker_markdown = tmp_path / "outcomes.gw0.md"
    worker_json.write_text(
        json.dumps(
            {
                "tests": [
                    {
                        "nodeid": "tests/test_worker.py::test_ok",
                        "status": "passed",
                        "reason": None,
                        "metadata": {},
                    }
                ]
            }
        )
    )
    worker_markdown.write_text("partial worker report\n")
    monkeypatch.setenv("QA_OUTCOME_PATH", str(report_path))
    live_collection_outcomes = outcome_conftest._collection_outcomes
    original_collection_outcomes = list(live_collection_outcomes)
    sentinel = object()
    live_collection_outcomes.append(sentinel)
    try:
        with monkeypatch.context() as isolated:
            isolated.setattr(outcome_conftest, "_collection_outcomes", [])
            config = SimpleNamespace(workerinput=None)
            outcome_conftest.pytest_sessionfinish(SimpleNamespace(config=config), 0)
        assert live_collection_outcomes == [*original_collection_outcomes, sentinel]
    finally:
        live_collection_outcomes[:] = original_collection_outcomes
    assert outcome_conftest._collection_outcomes is live_collection_outcomes
    assert report_path.exists()
    assert not worker_json.exists()
    assert not worker_markdown.exists()


def _run_report_fixture(tmp_path, source: str, *, xdist: bool = False):
    test_file = tmp_path / "test_report_fixture.py"
    report_file = tmp_path / "outcomes.json"
    test_file.write_text(source)
    env = os.environ.copy()
    env["QA_OUTCOME_PATH"] = str(report_file)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(Path(__file__).parents[2]), env.get("PYTHONPATH", "")]
    )
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-p",
        "tests.conftest",
    ]
    if xdist:
        command.extend(["-n", "2"])
    command.extend(
        [
            "-o",
            "addopts=",
            "-q",
            str(test_file),
        ]
    )
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        env=env,
    )
    return result, json.loads(report_file.read_text())


def test_combined_skipif_and_xfail_use_skipif_metadata(tmp_path):
    result, payload = _run_report_fixture(
        tmp_path,
        """\
import pytest

@pytest.mark.skipif(
    True,
    reason="skip marker",
    owner="skip-owner",
    issue="#skip",
    classification="retained",
    environment="skip environment",
)
@pytest.mark.xfail(
    strict=True,
    reason="xfail marker",
    owner="xfail-owner",
    issue="#xfail",
    classification="quarantined",
    environment="xfail environment",
)
def test_combined_markers():
    assert False
""",
    )
    assert result.returncode == 0, result.stderr
    entry = next(item for item in payload["tests"] if item["status"] == "skipped")
    assert entry["metadata"] == {
        "owner": "skip-owner",
        "issue": "#skip",
        "classification": "retained",
        "environment": "skip environment",
    }


def test_module_level_governed_skip_is_reported(tmp_path):
    result, payload = _run_report_fixture(
        tmp_path,
        """\
from tests.outcome_governance import governed_skip

governed_skip(
    "module unavailable",
    owner="module-owner",
    issue="#module",
    classification="retained",
    environment="module environment",
    allow_module_level=True,
)
""",
        xdist=True,
    )
    assert result.returncode in {0, 5}, result.stderr
    assert payload["counts"]["skipped"] == 1
    entry = next(item for item in payload["tests"] if item["status"] == "skipped")
    assert entry["metadata"]["issue"] == "#module"
    assert entry["metadata"]["classification"] == "retained"


def test_collection_failure_is_reported(tmp_path):
    result, payload = _run_report_fixture(
        tmp_path,
        "raise RuntimeError('collection boom')\n",
        xdist=True,
    )
    assert result.returncode != 0
    entry = next(item for item in payload["tests"] if item["status"] == "failed")
    assert entry["nodeid"].endswith("test_report_fixture.py")
    assert payload["counts"]["failed"] == 1
