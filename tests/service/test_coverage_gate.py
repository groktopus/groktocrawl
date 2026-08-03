from __future__ import annotations

import json
from datetime import date

from scripts.coverage_gate import (
    CoverageLines,
    CoveragePolicy,
    evaluate,
    load_coverage,
    main,
    parse_changed_lines,
    render_summary,
    valid_exception,
)


def _policy(*, high_risk: tuple[str, ...] = ("common/url.py",)) -> CoveragePolicy:
    return CoveragePolicy(
        source_roots=("agent-svc/agent", "common"),
        excluded_paths=("tests",),
        high_risk_paths=high_risk,
        changed_line_target=80.0,
        high_risk_changed_line_fail_under=90.0,
    )


def test_parse_changed_lines_tracks_added_and_modified_destination_lines():
    diff = """\
diff --git a/common/url.py b/common/url.py
--- a/common/url.py
+++ b/common/url.py
@@ -10,2 +10,3 @@
-old
+new
+newer
diff --git a/tests/test_url.py b/tests/test_url.py
--- a/tests/test_url.py
+++ b/tests/test_url.py
@@ -1 +1 @@
-old test
+new test
"""

    assert parse_changed_lines(diff) == {
        "common/url.py": {10, 11},
        "tests/test_url.py": {1},
    }


def test_evaluate_filters_non_source_changes():
    policy = _policy()
    changed = {
        "common/url.py": {10},
        "tests/test_url.py": {1},
        "docs/testing-coverage.md": {2},
    }
    coverage = {
        "common/url.py": CoverageLines(frozenset({10}), frozenset()),
    }

    results = evaluate(changed, coverage, policy)

    assert [result.path for result in results] == ["common/url.py"]


def test_load_coverage_unions_reports_and_normalizes_docker_paths(tmp_path):
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    first.write_text(
        json.dumps(
            {
                "files": {
                    "/app/common/url.py": {
                        "executed_lines": [10],
                        "missing_lines": [11],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    second.write_text(
        json.dumps(
            {
                "files": {
                    "common/url.py": {
                        "executed_lines": [11],
                        "missing_lines": [12],
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    loaded = load_coverage([first, second], tmp_path)

    assert loaded["common/url.py"].executed == frozenset({10, 11})
    assert loaded["common/url.py"].missing == frozenset({12})


def test_high_risk_changed_line_failure_is_reported():
    policy = _policy()
    results = evaluate(
        {"common/url.py": {10, 11}},
        {"common/url.py": CoverageLines(frozenset({10}), frozenset({11}))},
        policy,
    )

    assert len(results) == 1
    assert results[0].coverage_percent == 50.0
    assert results[0].passed is False
    assert "FAIL" in render_summary(
        results,
        {"common/url.py": CoverageLines(frozenset({10}), frozenset({11}))},
        base_sha="base",
        head_sha="head",
    )


def test_standard_module_below_target_is_informational():
    results = evaluate(
        {"agent-svc/agent/worker.py": {10, 11}},
        {"agent-svc/agent/worker.py": CoverageLines(frozenset(), frozenset({10, 11}))},
        _policy(),
    )

    assert results[0].high_risk is False
    assert results[0].passed is True
    assert "INFO (below target)" in render_summary(
        results,
        {"agent-svc/agent/worker.py": CoverageLines(frozenset(), frozenset({10, 11}))},
        base_sha="base",
        head_sha="head",
    )


def test_reviewed_nonexpired_exception_allows_high_risk_failure():
    exception = {
        "issue": "#503",
        "reviewer": "@maintainers",
        "expires": "2026-12-31",
        "reason": "The change is covered by the runtime probe.",
        "reviewed": True,
    }
    assert valid_exception(exception, today=date(2026, 8, 3))

    results = evaluate(
        {"common/url.py": {10}},
        {"common/url.py": CoverageLines(frozenset(), frozenset({10}))},
        _policy(),
        {"common/url.py": exception},
    )

    assert results[0].exception == exception
    assert results[0].passed is True


def test_invalid_exception_does_not_bypass_high_risk_gate():
    exception = {
        "issue": "#503",
        "reviewer": "@maintainers",
        "expires": "2026-12-31",
        "reason": "Missing explicit review marker.",
        "reviewed": False,
    }

    results = evaluate(
        {"common/url.py": {10}},
        {"common/url.py": CoverageLines(frozenset(), frozenset({10}))},
        _policy(),
        {"common/url.py": exception},
    )

    assert results[0].exception is None
    assert results[0].passed is False


def test_cli_returns_failure_for_uncovered_high_risk_change(tmp_path, monkeypatch):
    policy_path = tmp_path / "pyproject.toml"
    policy_path.write_text(
        """\
[tool.groktocrawl.coverage]
source_roots = ["common"]
high_risk_paths = ["common/url.py"]
changed_line_target = 80.0
high_risk_changed_line_fail_under = 90.0
""",
        encoding="utf-8",
    )
    coverage_path = tmp_path / "coverage.json"
    coverage_path.write_text(
        json.dumps(
            {
                "files": {
                    "common/url.py": {
                        "executed_lines": [],
                        "missing_lines": [10],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "scripts.coverage_gate.git_diff",
        lambda *_args: (
            """\
diff --git a/common/url.py b/common/url.py
--- a/common/url.py
+++ b/common/url.py
@@ -9,0 +10 @@
+return False
"""
        ),
    )

    summary_path = tmp_path / "summary.md"
    exit_code = main(
        [
            "--coverage-json",
            str(coverage_path),
            "--base-sha",
            "base",
            "--head-sha",
            "head",
            "--repo-root",
            str(tmp_path),
            "--policy",
            str(policy_path),
            "--summary-path",
            str(summary_path),
        ]
    )

    assert exit_code == 1
    assert "FAIL" in summary_path.read_text(encoding="utf-8")


def test_changed_lines_without_executable_coverage_are_informational():
    results = evaluate(
        {"common/url.py": {10}},
        {"common/url.py": CoverageLines(frozenset({1}), frozenset({2}))},
        _policy(),
    )

    assert results[0].coverage_percent is None
    assert results[0].passed is True
