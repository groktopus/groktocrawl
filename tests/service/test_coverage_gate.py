from __future__ import annotations

import json
from datetime import date

import pytest

import scripts.coverage_gate as coverage_gate
from scripts.coverage_gate import (
    CoverageGateError,
    CoverageLines,
    CoveragePolicy,
    evaluate,
    load_coverage,
    load_exceptions,
    main,
    parse_changed_lines,
    parse_deleted_paths,
    parse_renamed_paths,
    render_summary,
    valid_exception,
    validate_high_risk_path_changes,
)


def _policy(*, high_risk: tuple[str, ...] = ("common/url.py",)) -> CoveragePolicy:
    return CoveragePolicy(
        source_roots=("agent-svc/agent", "common"),
        excluded_paths=("tests", "agent-svc/agent/tests"),
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


def test_parse_renamed_paths_maps_destination_to_source():
    diff = """\
diff --git a/common/url.py b/common/url_validation.py
similarity index 91%
rename from common/url.py
rename to common/url_validation.py
--- a/common/url.py
+++ b/common/url_validation.py
@@ -10 +10 @@
-old
+new
"""

    assert parse_renamed_paths(diff) == {"common/url_validation.py": "common/url.py"}


def test_detected_high_risk_rename_requires_policy_update():
    with pytest.raises(
        CoverageGateError, match=r"common/url\.py -> common/url_validation\.py"
    ):
        validate_high_risk_path_changes(
            {"common/url_validation.py": "common/url.py"}, set(), _policy()
        )

    validate_high_risk_path_changes(
        {"common/url_validation.py": "common/url.py"},
        set(),
        _policy(high_risk=("common/url_validation.py",)),
    )


def test_low_similarity_high_risk_rename_fails_closed_as_deletion():
    diff = """\
diff --git a/common/url.py b/common/url.py
deleted file mode 100644
--- a/common/url.py
+++ /dev/null
@@ -1 +0,0 @@
-old
diff --git a/common/url_validation.py b/common/url_validation.py
new file mode 100644
--- /dev/null
+++ b/common/url_validation.py
@@ -0,0 +1 @@
+new
"""

    deleted = parse_deleted_paths(diff)

    assert deleted == {"common/url.py"}
    with pytest.raises(CoverageGateError, match=r"common/url\.py"):
        validate_high_risk_path_changes({}, deleted, _policy())


def test_evaluate_filters_non_source_changes():
    policy = _policy()
    changed = {
        "common/url.py": {10},
        "tests/test_url.py": {1},
        "agent-svc/agent/tests/test_stack.py": {1},
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
                    },
                    "/app/agent/crawler.py": {
                        "executed_lines": [20],
                        "missing_lines": [21],
                    },
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
    assert loaded["agent-svc/agent/crawler.py"] == CoverageLines(
        executed=frozenset({20}), missing=frozenset({21})
    )


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
excluded_paths = []
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
excluded_paths = []
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
excluded_paths = []
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
excluded_paths = []
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


@pytest.mark.parametrize(
    ("coverage_payload", "expected_detail"),
    [
        (
            '{"files":{"common/url.py":{"executed_lines":[1e999],"missing_lines":[]}}}',
            "must contain integers",
        ),
        (
            '{"files":{"common/url.py":{"executed_lines":[0],"missing_lines":[]}}}',
            "must contain positive lines",
        ),
        (
            '{"files":{"common/url.py":{"executed_lines":[10],"missing_lines":[10]}}}',
            "both executed and missing",
        ),
    ],
)
def test_cli_rejects_invalid_coverage_lines(
    tmp_path, capsys, coverage_payload, expected_detail
):
    policy_path = tmp_path / "pyproject.toml"
    policy_path.write_text(
        """\
[tool.groktocrawl.coverage]
source_roots = ["common"]
excluded_paths = []
high_risk_paths = ["common/url.py"]
changed_line_target = 80.0
high_risk_changed_line_fail_under = 90.0
""",
        encoding="utf-8",
    )
    coverage_path = tmp_path / "invalid-lines.json"
    coverage_path.write_text(coverage_payload, encoding="utf-8")
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
    error = capsys.readouterr().err
    assert "Coverage gate error: unable to load coverage report" in error
    assert expected_detail in error
    assert "Changed-line coverage gate error" in summary_path.read_text(
        encoding="utf-8"
    )


def test_cli_reports_missing_policy_file(tmp_path, capsys):
    coverage_path = tmp_path / "coverage.json"
    coverage_path.write_text(json.dumps({"files": {}}), encoding="utf-8")
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
            str(tmp_path / "missing-pyproject.toml"),
            "--exceptions",
            str(tmp_path / "exceptions.toml"),
            "--summary-path",
            str(summary_path),
        ]
    )

    assert exit_code == 2
    assert "Coverage gate error:" in capsys.readouterr().err
    assert "Changed-line coverage gate error" in summary_path.read_text(
        encoding="utf-8"
    )


def test_cli_reports_malformed_exceptions_file(tmp_path, monkeypatch, capsys):
    policy_path = tmp_path / "pyproject.toml"
    policy_path.write_text(
        """\
[tool.groktocrawl.coverage]
source_roots = ["common"]
excluded_paths = []
high_risk_paths = ["common/url.py"]
changed_line_target = 80.0
high_risk_changed_line_fail_under = 90.0
""",
        encoding="utf-8",
    )
    coverage_path = tmp_path / "coverage.json"
    coverage_path.write_text(json.dumps({"files": {}}), encoding="utf-8")
    exceptions_path = tmp_path / "exceptions.toml"
    exceptions_path.write_text("[exceptions\n", encoding="utf-8")
    summary_path = tmp_path / "summary.md"
    monkeypatch.setattr(coverage_gate, "git_diff", lambda *_args: "")

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
            "--exceptions",
            str(exceptions_path),
            "--summary-path",
            str(summary_path),
        ]
    )

    assert exit_code == 2
    assert "Coverage gate error:" in capsys.readouterr().err
    assert "Changed-line coverage gate error" in summary_path.read_text(
        encoding="utf-8"
    )


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("source_roots", "[]", "source_roots must be non-empty"),
        ("source_roots", None, "source_roots is required"),
        ("high_risk_paths", '"common/url.py"', "high_risk_paths must be a string list"),
        ("high_risk_paths", '[""]', "high_risk_paths must contain non-empty strings"),
        (
            "high_risk_paths",
            '["outside/url.py"]',
            "high_risk_paths must fall under source_roots",
        ),
        ("changed_line_target", "nan", "changed_line_target must be finite"),
        ("changed_line_target", "-1", "changed_line_target must be finite"),
        (
            "high_risk_changed_line_fail_under",
            "101",
            "high_risk_changed_line_fail_under must be finite",
        ),
    ],
)
def test_cli_rejects_invalid_policy_with_error_summary(
    tmp_path, monkeypatch, capsys, field, value, expected
):
    policy_path = tmp_path / "pyproject.toml"
    fields = {
        "source_roots": '["common"]',
        "excluded_paths": "[]",
        "high_risk_paths": '["common/url.py"]',
        "changed_line_target": "80.0",
        "high_risk_changed_line_fail_under": "90.0",
    }
    if value is None:
        fields.pop(field)
    else:
        fields[field] = value
    policy_path.write_text(
        "[tool.groktocrawl.coverage]\n"
        + "\n".join(f"{name} = {item}" for name, item in fields.items())
        + "\n",
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
        coverage_gate,
        "git_diff",
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

    assert exit_code == 2
    assert (
        f"Coverage gate error: coverage policy field {expected}"
        in capsys.readouterr().err
    )
    assert "Changed-line coverage gate error" in summary_path.read_text(
        encoding="utf-8"
    )


def test_exceptions_accept_multiline_reasons_supported_by_tomllib(tmp_path):
    exception_path = tmp_path / "coverage-exceptions.toml"
    exception_path.write_text(
        '''\
[exceptions."common/url.py"]
issue = "#503"
reviewer = "@maintainer"
expires = "2099-12-31"
reason = """multi
line reason""" # trailing comment
reviewed = true

[exceptions]
"common/inline.py" = { issue = "#504", reviewer = "@maintainer", expires = "2099-12-31", reason = """inline
multiline reason""", reviewed = true }
''',
        encoding="utf-8",
    )

    exceptions = load_exceptions(exception_path)

    assert exceptions["common/url.py"]["reason"] == "multi\nline reason"
    assert exceptions["common/inline.py"]["reason"] == "inline\nmultiline reason"


def test_changed_lines_without_executable_coverage_are_informational():
    results = evaluate(
        {"common/url.py": {10}},
        {"common/url.py": CoverageLines(frozenset({1}), frozenset({2}))},
        _policy(),
    )

    assert results[0].coverage_percent is None
    assert results[0].passed is True


def test_exception_is_visible_when_changed_lines_are_not_executable():
    exception = {
        "issue": "#503",
        "reviewer": "@maintainers",
        "expires": "2099-12-31",
        "reason": "A reviewed bounded exception.",
        "reviewed": True,
    }
    coverage = {"common/url.py": CoverageLines(frozenset({1}), frozenset({2}))}
    results = evaluate(
        {"common/url.py": {10}},
        coverage,
        _policy(),
        {"common/url.py": exception},
    )

    assert "EXCEPTION (#503)" in render_summary(
        results,
        coverage,
        base_sha="base",
        head_sha="head",
    )


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
