from __future__ import annotations

import json
from datetime import date

import scripts.coverage_gate as coverage_gate
from scripts.coverage_gate import (
    CoverageGateError,
    CoverageLines,
    CoveragePolicy,
    evaluate,
    load_coverage,
    load_exceptions,
    load_policy,
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


def test_parse_changed_lines_ignores_no_newline_marker():
    diff = """\
diff --git a/common/url.py b/common/url.py
--- a/common/url.py
+++ b/common/url.py
@@ -9,1 +9,2 @@
-old
+new
\\ No newline at end of file
+newer
"""

    assert parse_changed_lines(diff) == {"common/url.py": {9, 10}}


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


def test_cli_reports_unresolvable_diff_base(tmp_path, monkeypatch, capsys):
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
    coverage_path.write_text(json.dumps({"files": {}}), encoding="utf-8")

    def fail_diff(*_args):
        raise CoverageGateError("unable to resolve coverage diff base 'missing'")

    monkeypatch.setattr(coverage_gate, "git_diff", fail_diff)
    exit_code = main(
        [
            "--coverage-json",
            str(coverage_path),
            "--base-sha",
            "missing",
            "--head-sha",
            "head",
            "--repo-root",
            str(tmp_path),
            "--policy",
            str(policy_path),
            "--summary-path",
            str(tmp_path / "summary.md"),
        ]
    )

    assert exit_code == 2
    assert (
        "Coverage gate error: unable to resolve coverage diff base"
        in capsys.readouterr().err
    )
    assert "Changed-line coverage gate error" in (tmp_path / "summary.md").read_text(
        encoding="utf-8"
    )


def test_cli_reports_missing_coverage_report(tmp_path, capsys):
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
    summary_path = tmp_path / "summary.md"
    exit_code = main(
        [
            "--coverage-json",
            str(tmp_path / "missing.json"),
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

    assert exit_code == 2
    assert "Coverage gate error: coverage report not found" in capsys.readouterr().err
    assert "Changed-line coverage gate error" in summary_path.read_text(
        encoding="utf-8"
    )


def test_cli_reports_malformed_coverage_report(tmp_path, capsys):
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
    coverage_path = tmp_path / "malformed.json"
    coverage_path.write_text('{"files":', encoding="utf-8")
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

    assert exit_code == 2
    assert (
        "Coverage gate error: unable to load coverage report" in capsys.readouterr().err
    )
    assert "Changed-line coverage gate error" in summary_path.read_text(
        encoding="utf-8"
    )


def test_toml_fallback_loads_policy_and_reviewed_exception(tmp_path, monkeypatch):
    policy_path = tmp_path / "pyproject.toml"
    policy_path.write_text(
        """\
[tool.groktocrawl.coverage]
source_roots = ["common"]
excluded_paths = ["tests"]
high_risk_paths = ["common/url.py"]
changed_line_target = 80.0
high_risk_changed_line_fail_under = 90.0
""",
        encoding="utf-8",
    )
    exception_path = tmp_path / "coverage-exceptions.toml"
    exception_path.write_text(
        """\
[exceptions]
"common/url.py" = { issue = "#503", reviewer = "@maintainer", expires = "2099-12-31", reason = "bounded", reviewed = true }
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(coverage_gate, "tomllib", None)

    policy = load_policy(policy_path)
    exceptions = load_exceptions(exception_path)

    assert policy.source_roots == ("common",)
    assert policy.high_risk_changed_line_fail_under == 90.0
    assert valid_exception(exceptions["common/url.py"], today=date(2026, 1, 1))


def test_toml_fallback_accepts_literal_string_values(tmp_path, monkeypatch):
    policy_path = tmp_path / "pyproject.toml"
    policy_path.write_text(
        """\
[tool.groktocrawl.coverage]
source_roots = ['common']
excluded_paths = ['tests']
high_risk_paths = ['common/url.py']
changed_line_target = 80.0
high_risk_changed_line_fail_under = 90.0
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(coverage_gate, "tomllib", None)

    policy = load_policy(policy_path)

    assert policy.source_roots == ("common",)
    assert policy.excluded_paths == ("tests",)
    assert policy.high_risk_paths == ("common/url.py",)


def test_changed_lines_without_executable_coverage_are_informational():
    results = evaluate(
        {"common/url.py": {10}},
        {"common/url.py": CoverageLines(frozenset({1}), frozenset({2}))},
        _policy(),
    )

    assert results[0].coverage_percent is None
    assert results[0].passed is True


def test_unmeasured_changed_module_is_informational():
    results = evaluate(
        {"common/url.py": {10}},
        {},
        _policy(),
    )

    assert results[0].changed_lines == 0
    assert results[0].coverage_percent is None
    assert results[0].passed is True
    assert "INFO (no executable lines)" in render_summary(
        results,
        {},
        base_sha="base",
        head_sha="head",
    )
