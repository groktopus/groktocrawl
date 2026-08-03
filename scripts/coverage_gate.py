#!/usr/bin/env python3
"""Report and enforce changed-line coverage for risk-sensitive source files.

The repository's aggregate pytest-cov threshold remains a coarse smoke signal. This
script adds a narrower signal: it intersects executable lines from a git diff with
coverage.py JSON data and enforces the high-risk policy only where changed code has
observable coverage obligations.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import re
import subprocess
import sys
import tomllib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

DEFAULT_POLICY_PATH = Path("pyproject.toml")
DEFAULT_EXCEPTION_PATH = Path("qa/coverage-exceptions.toml")
HUNK_RE = re.compile(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
ISSUE_RE = re.compile(r"#\d+")


@dataclass(frozen=True)
class CoverageLines:
    executed: frozenset[int]
    missing: frozenset[int]

    @property
    def executable(self) -> frozenset[int]:
        return self.executed | self.missing


@dataclass(frozen=True)
class CoveragePolicy:
    source_roots: tuple[str, ...]
    excluded_paths: tuple[str, ...]
    high_risk_paths: tuple[str, ...]
    changed_line_target: float
    high_risk_changed_line_fail_under: float


@dataclass(frozen=True)
class FileResult:
    path: str
    changed_lines: int
    covered_lines: int
    coverage_percent: float | None
    high_risk: bool
    threshold: float
    exception: Mapping[str, Any] | None = None

    @property
    def enforced(self) -> bool:
        return self.high_risk and self.coverage_percent is not None

    @property
    def passed(self) -> bool:
        return (
            self.coverage_percent is None
            or self.coverage_percent >= self.threshold
            or self.exception is not None
            or not self.high_risk
        )


def _as_string_tuple(
    value: object, *, default: tuple[str, ...] = ()
) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        return default
    return tuple(value)


def load_policy(path: Path) -> CoveragePolicy:
    """Load the checked-in coverage policy from pyproject.toml."""

    with path.open("rb") as stream:
        document = tomllib.load(stream)
    raw = document.get("tool", {}).get("groktocrawl", {}).get("coverage", {})
    if not isinstance(raw, dict):
        raw = {}
    return CoveragePolicy(
        source_roots=_as_string_tuple(raw.get("source_roots")),
        excluded_paths=_as_string_tuple(raw.get("excluded_paths")),
        high_risk_paths=_as_string_tuple(raw.get("high_risk_paths")),
        changed_line_target=float(raw.get("changed_line_target", 80.0)),
        high_risk_changed_line_fail_under=float(
            raw.get("high_risk_changed_line_fail_under", 90.0)
        ),
    )


def normalize_coverage_path(name: str, repo_root: Path) -> str:
    """Normalize local and container coverage paths to repository-relative paths."""

    path = Path(name)
    if not path.is_absolute():
        return path.as_posix().lstrip("./")

    try:
        return path.relative_to(repo_root).as_posix()
    except ValueError:
        pass

    # Docker reports commonly contain /app/<repository path>. Keep the first
    # repository-owned source directory and discard the container prefix.
    parts = path.parts
    source_markers = (
        "agent-svc",
        "scraper-svc",
        "browser-svc",
        "parse-svc",
        "portal-svc",
        "semantic-svc",
        "common",
    )
    for marker in source_markers:
        if marker in parts:
            return Path(*parts[parts.index(marker) :]).as_posix()
    return path.as_posix().lstrip("/")


def load_coverage(paths: Iterable[Path], repo_root: Path) -> dict[str, CoverageLines]:
    """Load and union one or more coverage.py JSON reports."""

    merged: dict[str, tuple[set[int], set[int]]] = {}
    for path in paths:
        with path.open(encoding="utf-8") as stream:
            document = json.load(stream)
        files = document.get("files")
        if not isinstance(files, dict):
            raise ValueError(f"coverage report has no files mapping: {path}")
        for name, payload in files.items():
            if not isinstance(payload, dict):
                raise ValueError(f"coverage entry is not an object: {name}")
            executed = {int(line) for line in payload.get("executed_lines", [])}
            missing = {int(line) for line in payload.get("missing_lines", [])}
            normalized = normalize_coverage_path(str(name), repo_root)
            current_executed, current_missing = merged.setdefault(
                normalized, (set(), set())
            )
            current_executed.update(executed)
            current_missing.update(missing)

    return {
        path: CoverageLines(
            executed=frozenset(executed),
            missing=frozenset(missing - executed),
        )
        for path, (executed, missing) in merged.items()
    }


def parse_changed_lines(diff_text: str) -> dict[str, set[int]]:
    """Return added/modified destination line numbers from a zero-context diff."""

    changed: dict[str, set[int]] = {}
    current_path: str | None = None
    current_line = 0
    for line in diff_text.splitlines():
        if line.startswith("+++ b/"):
            current_path = line[6:]
            changed.setdefault(current_path, set())
            continue
        if line.startswith("+++ "):
            current_path = None
            continue
        if line.startswith("@@"):
            match = HUNK_RE.search(line)
            if match:
                current_line = int(match.group(1))
            continue
        if current_path is None or not line:
            continue
        if line.startswith("+"):
            changed[current_path].add(current_line)
            current_line += 1
        elif line.startswith("-"):
            continue
        else:
            current_line += 1
    return {path: lines for path, lines in changed.items() if lines}


def git_diff(repo_root: Path, base_sha: str, head_sha: str) -> str:
    """Read the exact changed-line diff used by CI."""

    completed = subprocess.run(
        [
            "git",
            "diff",
            "--no-ext-diff",
            "--unified=0",
            f"{base_sha}...{head_sha}",
            "--",
        ],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout


def path_matches(path: str, patterns: Iterable[str]) -> bool:
    return any(
        fnmatch.fnmatch(path, pattern)
        or path == pattern.rstrip("/")
        or path.startswith(pattern.rstrip("/") + "/")
        for pattern in patterns
    )


def source_changes(
    changed: Mapping[str, set[int]], policy: CoveragePolicy
) -> dict[str, set[int]]:
    return {
        path: lines
        for path, lines in changed.items()
        if path.endswith(".py")
        and path_matches(path, policy.source_roots)
        and not path_matches(path, policy.excluded_paths)
    }


def load_exceptions(path: Path) -> dict[str, Mapping[str, Any]]:
    if not path.exists():
        return {}
    with path.open("rb") as stream:
        document = tomllib.load(stream)
    raw = document.get("exceptions", {})
    if not isinstance(raw, dict):
        raise ValueError(f"coverage exceptions must be a table: {path}")
    return {
        str(module): value for module, value in raw.items() if isinstance(value, dict)
    }


def valid_exception(entry: Mapping[str, Any], *, today: date | None = None) -> bool:
    """Require an explicit, reviewed, non-expired exception record."""

    required = ("issue", "reviewer", "expires", "reason")
    if entry.get("reviewed") is not True or any(not entry.get(key) for key in required):
        return False
    if not ISSUE_RE.fullmatch(str(entry["issue"])):
        return False
    try:
        expires = date.fromisoformat(str(entry["expires"]))
    except ValueError:
        return False
    return expires >= (today or date.today())


def evaluate(
    changed: Mapping[str, set[int]],
    coverage: Mapping[str, CoverageLines],
    policy: CoveragePolicy,
    exceptions: Mapping[str, Mapping[str, Any]] | None = None,
) -> list[FileResult]:
    """Calculate changed executable-line coverage for each changed source file."""

    results: list[FileResult] = []
    exceptions = exceptions or {}
    for path in sorted(source_changes(changed, policy)):
        lines = changed[path]
        report = coverage.get(path)
        if report is None:
            executable = set(lines)
            covered: set[int] = set()
        else:
            executable = set(lines) & set(report.executable)
            covered = executable & set(report.executed)
        if not executable:
            percent = None
        else:
            percent = 100.0 * len(covered) / len(executable)
        high_risk = path_matches(path, policy.high_risk_paths)
        exception = exceptions.get(path) if high_risk else None
        results.append(
            FileResult(
                path=path,
                changed_lines=len(executable),
                covered_lines=len(covered),
                coverage_percent=percent,
                high_risk=high_risk,
                threshold=(
                    policy.high_risk_changed_line_fail_under
                    if high_risk
                    else policy.changed_line_target
                ),
                exception=exception
                if exception and valid_exception(exception)
                else None,
            )
        )
    return results


def _percent(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1f}%"


def render_summary(
    results: Iterable[FileResult],
    coverage: Mapping[str, CoverageLines],
    *,
    base_sha: str,
    head_sha: str,
) -> str:
    rows = list(results)
    total_executable = sum(result.changed_lines for result in rows)
    total_covered = sum(result.covered_lines for result in rows)
    total_percent = (
        100.0 * total_covered / total_executable if total_executable else None
    )
    failures = [
        result
        for result in rows
        if result.high_risk
        and result.coverage_percent is not None
        and result.coverage_percent < result.threshold
        and result.exception is None
    ]
    lines = [
        "# Changed-line coverage",
        "",
        f"- Compared `{base_sha}` to `{head_sha}`.",
        f"- Coverage reports loaded: {len(coverage)} measured modules.",
        f"- Changed executable lines: {total_covered}/{total_executable} ({_percent(total_percent)}).",
        "- High-risk policy: changed lines must meet the configured threshold unless a reviewed, non-expired exception is recorded.",
        "",
        "| Module | Risk | Covered | Changed executable | Coverage | Policy | Result |",
        "| --- | --- | ---: | ---: | ---: | ---: | --- |",
    ]
    if not rows:
        lines.append(
            "| No measured service source changed | — | — | — | — | — | PASS |"
        )
    for result in rows:
        if result.coverage_percent is None:
            outcome = "INFO (no executable lines)"
        elif result.exception is not None:
            outcome = f"EXCEPTION ({result.exception['issue']})"
        elif result.high_risk and result.coverage_percent < result.threshold:
            outcome = "FAIL"
        elif not result.high_risk and result.coverage_percent < result.threshold:
            outcome = "INFO (below target)"
        else:
            outcome = "PASS"
        risk = "high" if result.high_risk else "standard"
        lines.append(
            f"| `{result.path}` | {risk} | {result.covered_lines} | "
            f"{result.changed_lines} | {_percent(result.coverage_percent)} | "
            f"{result.threshold:.0f}% | {outcome} |"
        )
    if failures:
        lines.extend(
            [
                "",
                "## Required action",
                "",
                "The high-risk modules above are below policy. Add focused tests or add a reviewed, non-expired exception to `qa/coverage-exceptions.toml`.",
            ]
        )
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--coverage-json", action="append", required=True, type=Path)
    parser.add_argument("--base-sha", required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY_PATH)
    parser.add_argument("--exceptions", type=Path, default=DEFAULT_EXCEPTION_PATH)
    parser.add_argument("--summary-path", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo_root = args.repo_root.resolve()
    policy = load_policy(args.policy)
    coverage = load_coverage(args.coverage_json, repo_root)
    changed = parse_changed_lines(git_diff(repo_root, args.base_sha, args.head_sha))
    results = evaluate(changed, coverage, policy, load_exceptions(args.exceptions))
    summary = render_summary(
        results,
        coverage,
        base_sha=args.base_sha,
        head_sha=args.head_sha,
    )
    if args.summary_path:
        args.summary_path.parent.mkdir(parents=True, exist_ok=True)
        args.summary_path.write_text(summary, encoding="utf-8")
    print(summary, end="")
    return (
        1
        if any(
            result.high_risk
            and result.coverage_percent is not None
            and result.coverage_percent < result.threshold
            and result.exception is None
            for result in results
        )
        else 0
    )


if __name__ == "__main__":
    sys.exit(main())
