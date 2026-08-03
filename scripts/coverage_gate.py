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
import math
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


class CoverageGateError(RuntimeError):
    """A gate input could not be resolved safely."""


def _load_toml(path: Path) -> dict[str, Any]:
    with path.open("rb") as stream:
        return tomllib.load(stream)


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
    def passed(self) -> bool:
        return (
            self.coverage_percent is None
            or self.coverage_percent >= self.threshold
            or self.exception is not None
            or not self.high_risk
        )


def _required_string_tuple(
    raw: Mapping[str, Any], field: str, *, allow_empty: bool
) -> tuple[str, ...]:
    if field not in raw:
        raise ValueError(f"coverage policy field {field} is required")
    value = raw[field]
    if not isinstance(value, list):
        raise ValueError(f"coverage policy field {field} must be a string list")
    if not allow_empty and not value:
        raise ValueError(f"coverage policy field {field} must be non-empty")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(
            f"coverage policy field {field} must contain non-empty strings"
        )
    return tuple(value)


def _required_percentage(raw: Mapping[str, Any], field: str) -> float:
    if field not in raw:
        raise ValueError(f"coverage policy field {field} is required")
    value = raw[field]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"coverage policy field {field} must be numeric")
    number = float(value)
    if not math.isfinite(number) or not 0 <= number <= 100:
        raise ValueError(
            f"coverage policy field {field} must be finite and in the range 0..100"
        )
    return number


def load_policy(path: Path) -> CoveragePolicy:
    """Load the checked-in coverage policy from pyproject.toml."""

    document = _load_toml(path)
    tool = document.get("tool")
    groktocrawl = tool.get("groktocrawl") if isinstance(tool, Mapping) else None
    raw = groktocrawl.get("coverage") if isinstance(groktocrawl, Mapping) else None
    if not isinstance(raw, Mapping):
        raise ValueError(
            "coverage policy must define a [tool.groktocrawl.coverage] table"
        )
    source_roots = _required_string_tuple(raw, "source_roots", allow_empty=False)
    high_risk_paths = _required_string_tuple(raw, "high_risk_paths", allow_empty=False)
    outside_source_roots = [
        high_risk_path
        for high_risk_path in high_risk_paths
        if not any(
            high_risk_path == source_root.rstrip("/")
            or high_risk_path.startswith(source_root.rstrip("/") + "/")
            for source_root in source_roots
        )
    ]
    if outside_source_roots:
        raise ValueError(
            "coverage policy field high_risk_paths must fall under source_roots: "
            + ", ".join(outside_source_roots)
        )
    return CoveragePolicy(
        source_roots=source_roots,
        excluded_paths=_required_string_tuple(raw, "excluded_paths", allow_empty=True),
        high_risk_paths=high_risk_paths,
        changed_line_target=_required_percentage(raw, "changed_line_target"),
        high_risk_changed_line_fail_under=_required_percentage(
            raw, "high_risk_changed_line_fail_under"
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
    if len(parts) >= 3 and parts[1:3] == ("app", "agent"):
        return Path("agent-svc", *parts[2:]).as_posix()
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


def _coverage_line_numbers(value: object, *, field: str, name: str) -> set[int]:
    if not isinstance(value, list) or any(type(line) is not int for line in value):
        raise ValueError(f"coverage field {field} must contain integers: {name}")
    if any(line <= 0 for line in value):
        raise ValueError(f"coverage field {field} must contain positive lines: {name}")
    return set(value)


def load_coverage(paths: Iterable[Path], repo_root: Path) -> dict[str, CoverageLines]:
    """Load and union one or more coverage.py JSON reports."""

    merged: dict[str, tuple[set[int], set[int]]] = {}
    for path in paths:
        try:
            with path.open(encoding="utf-8") as stream:
                document = json.load(stream)
            files = document.get("files")
            if not isinstance(files, dict):
                raise ValueError(f"coverage report has no files mapping: {path}")
            for name, payload in files.items():
                if not isinstance(payload, dict):
                    raise ValueError(f"coverage entry is not an object: {name}")
                executed = _coverage_line_numbers(
                    payload.get("executed_lines", []), field="executed_lines", name=name
                )
                missing = _coverage_line_numbers(
                    payload.get("missing_lines", []), field="missing_lines", name=name
                )
                overlap = executed & missing
                if overlap:
                    lines = ", ".join(str(line) for line in sorted(overlap))
                    raise ValueError(
                        f"coverage entry marks lines as both executed and missing: "
                        f"{name}: {lines}"
                    )
                normalized = normalize_coverage_path(str(name), repo_root)
                current_executed, current_missing = merged.setdefault(
                    normalized, (set(), set())
                )
                current_executed.update(executed)
                current_missing.update(missing)
        except CoverageGateError:
            raise
        except (AttributeError, OSError, OverflowError, TypeError, ValueError) as exc:
            raise CoverageGateError(
                f"unable to load coverage report '{path}': {exc}"
            ) from exc

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
        if line.startswith("\\ No newline at end of file"):
            continue
        if line.startswith("+"):
            changed[current_path].add(current_line)
            current_line += 1
        elif line.startswith("-"):
            continue
        else:
            current_line += 1
    return {path: lines for path, lines in changed.items() if lines}


def parse_renamed_paths(diff_text: str) -> dict[str, str]:
    """Map destination paths to source paths for detected renames."""

    renamed: dict[str, str] = {}
    source_path: str | None = None
    for line in diff_text.splitlines():
        if line.startswith("rename from "):
            source_path = line[len("rename from ") :]
            continue
        if line.startswith("rename to "):
            if source_path is not None:
                renamed[line[len("rename to ") :]] = source_path
            source_path = None
            continue
        if line.startswith("--- a/"):
            source_path = line[6:]
            continue
        if line.startswith("--- "):
            source_path = None
            continue
        if line.startswith("+++ b/"):
            destination_path = line[6:]
            if source_path is not None and source_path != destination_path:
                renamed[destination_path] = source_path
            source_path = None
    return renamed


def parse_deleted_paths(diff_text: str) -> set[str]:
    """Return source paths deleted without a detected destination."""

    deleted: set[str] = set()
    source_path: str | None = None
    for line in diff_text.splitlines():
        if line.startswith("--- a/"):
            source_path = line[6:]
            continue
        if line.startswith("--- "):
            source_path = None
            continue
        if line == "+++ /dev/null" and source_path is not None:
            deleted.add(source_path)
        if line.startswith("+++ "):
            source_path = None
    return deleted


def git_diff(repo_root: Path, base_sha: str, head_sha: str) -> str:
    """Read the exact changed-line diff used by CI."""

    try:
        completed = subprocess.run(
            [
                "git",
                "diff",
                "--no-ext-diff",
                "--find-renames",
                "--unified=0",
                f"{base_sha}...{head_sha}",
                "--",
            ],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or "").strip()
        if not detail:
            detail = f"git diff exited with status {exc.returncode}"
        raise CoverageGateError(
            f"unable to resolve coverage diff base '{base_sha}' against head "
            f"'{head_sha}': {detail}"
        ) from exc
    return completed.stdout


def path_matches(path: str, patterns: Iterable[str]) -> bool:
    return any(
        fnmatch.fnmatch(path, pattern)
        or path == pattern.rstrip("/")
        or path.startswith(pattern.rstrip("/") + "/")
        for pattern in patterns
    )


def validate_high_risk_path_changes(
    renamed_from: Mapping[str, str],
    deleted_paths: Iterable[str],
    policy: CoveragePolicy,
) -> None:
    """Require policy updates when a configured high-risk path moves or disappears."""

    stale = [
        f"{source} -> {destination}"
        for destination, source in renamed_from.items()
        if path_matches(source, policy.high_risk_paths)
        and not path_matches(destination, policy.high_risk_paths)
    ]
    stale.extend(
        path for path in deleted_paths if path_matches(path, policy.high_risk_paths)
    )
    if stale:
        raise CoverageGateError(
            "high-risk paths moved or were deleted without a policy update: "
            + ", ".join(sorted(stale))
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
    document = _load_toml(path)
    raw = document.get("exceptions", {})
    if not isinstance(raw, dict):
        raise ValueError(f"coverage exceptions must be a table: {path}")
    invalid = [
        module for module, value in raw.items() if not isinstance(value, Mapping)
    ]
    if invalid:
        raise ValueError(
            f"coverage exception entries must be tables: {', '.join(map(str, invalid))}"
        )
    return {str(module): value for module, value in raw.items()}


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
            # coverage.py reports only modules imported by this lane. A missing
            # report means this lane did not measure the module, not that every
            # changed line was uncovered. The owning lane remains responsible.
            executable: set[int] = set()
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
    failures = [result for result in rows if not result.passed]
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
        if result.exception is not None:
            outcome = f"EXCEPTION ({result.exception['issue']})"
        elif result.coverage_percent is None:
            outcome = "INFO (no executable lines)"
        elif not result.passed:
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


def _fail_gate(args: argparse.Namespace, detail: str) -> int:
    message = f"Coverage gate error: {detail}"
    if args.summary_path:
        args.summary_path.parent.mkdir(parents=True, exist_ok=True)
        args.summary_path.write_text(
            f"# Changed-line coverage gate error\n\n{message}\n",
            encoding="utf-8",
        )
    print(message, file=sys.stderr)
    return 2


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo_root = args.repo_root.resolve()
    missing = [str(path) for path in args.coverage_json if not path.exists()]
    if missing:
        return _fail_gate(
            args,
            f"coverage report not found: {', '.join(missing)}",
        )
    try:
        policy = load_policy(args.policy)
        coverage = load_coverage(args.coverage_json, repo_root)
        diff_text = git_diff(repo_root, args.base_sha, args.head_sha)
        exceptions = load_exceptions(args.exceptions)
        changed = parse_changed_lines(diff_text)
        renamed_from = parse_renamed_paths(diff_text)
        deleted_paths = parse_deleted_paths(diff_text)
        validate_high_risk_path_changes(renamed_from, deleted_paths, policy)
    except (AttributeError, CoverageGateError, OSError, TypeError, ValueError) as exc:
        return _fail_gate(args, str(exc))
    results = evaluate(changed, coverage, policy, exceptions)
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
    return 1 if any(not result.passed for result in results) else 0


if __name__ == "__main__":
    sys.exit(main())
