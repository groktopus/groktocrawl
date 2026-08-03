"""pytest configuration for GroktoCrawl unit tests.

Adds the project root and agent-svc to the Python path so that unit tests
can import the agent modules directly.
"""

from __future__ import annotations

import json
import os
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import pytest

# Add project root to path (for common.* imports)
_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

# Add agent-svc to path (for agent.* imports)
_agent_svc = _root / "agent-svc"
if _agent_svc.exists() and str(_agent_svc) not in sys.path:
    sys.path.insert(0, str(_agent_svc))

from tests.outcome_governance import (
    metadata_from_marker,
    metadata_from_reason,
    validate_metadata,
)

_GOVERNANCE_KEYS = {
    "owner",
    "issue",
    "classification",
    "review_date",
    "environment",
}


def _report_path() -> Path:
    return Path(os.environ.get("QA_OUTCOME_PATH", "test-outcomes.json"))


def _governance_metadata(item, report) -> dict[str, str]:
    metadata = getattr(item, "_qa_governance_metadata", None)
    if metadata:
        return metadata
    for marker_name in ("skip", "skipif", "xfail"):
        marker = item.get_closest_marker(marker_name)
        if marker:
            metadata = metadata_from_marker(marker)
            if metadata:
                return metadata
    return metadata_from_reason(getattr(report, "wasxfail", None))


def pytest_collection_modifyitems(config, items):
    violations = []
    markers_to_strip = {}
    for item in items:
        item_metadata = {}
        for marker_name in ("skip", "skipif", "xfail"):
            for marker in item.iter_markers(name=marker_name):
                kwargs = marker.kwargs
                try:
                    validate_metadata(
                        reason=str(kwargs.get("reason", "")),
                        owner=str(kwargs.get("owner", "")),
                        issue=str(kwargs.get("issue", "")),
                        classification=str(kwargs.get("classification", "")),
                        review_date=kwargs.get("review_date"),
                        environment=kwargs.get("environment"),
                    )
                except (TypeError, ValueError) as exc:
                    violations.append(f"{item.nodeid} @{marker_name}: {exc}")
                else:
                    item_metadata.update(metadata_from_marker(marker))
                    markers_to_strip[id(marker)] = marker
                if marker_name == "xfail" and kwargs.get("strict") is not True:
                    violations.append(f"{item.nodeid} @xfail: strict=True is required")
        if item_metadata:
            item._qa_governance_metadata = item_metadata
    if violations:
        raise pytest.UsageError(
            "Ungoverned skip/xfail markers:\n" + "\n".join(violations)
        )
    for marker in markers_to_strip.values():
        for key in _GOVERNANCE_KEYS:
            marker.kwargs.pop(key, None)


def _outcome_entry(nodeid, report, metadata=None) -> dict:
    report_text = str(getattr(report, "longrepr", ""))
    if report.failed and "XPASS(strict)" in report_text:
        status = "xpassed"
        reason = getattr(report, "wasxfail", None) or report_text
    elif report.skipped and hasattr(report, "wasxfail"):
        status = "xfailed"
        reason = report.wasxfail
    elif hasattr(report, "wasxfail"):
        status = "xpassed"
        reason = report.wasxfail
    elif report.passed:
        status = "passed"
        reason = None
    elif report.skipped:
        status = "skipped"
        reason = str(report.longrepr[2]) if isinstance(report.longrepr, tuple) else None
    else:
        status = "failed"
        reason = None
    return {
        "nodeid": nodeid,
        "status": status,
        "reason": reason,
        "metadata": metadata
        or metadata_from_reason(reason if isinstance(reason, str) else None)
        or metadata_from_reason(getattr(report, "wasxfail", None)),
    }


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if call.when != "call" and not (
        call.when in {"setup", "teardown"} and (report.skipped or report.failed)
    ):
        return
    entry = _outcome_entry(item.nodeid, report, _governance_metadata(item, report))
    _record_outcome(item.config, entry)


def _record_outcome(config, entry) -> None:
    by_nodeid = getattr(config, "_qa_outcome_by_nodeid", {})
    existing = by_nodeid.get(entry["nodeid"])
    if existing is None or _outcome_priority(entry["status"]) > _outcome_priority(
        existing["status"]
    ):
        by_nodeid[entry["nodeid"]] = entry
    config._qa_outcome_by_nodeid = by_nodeid
    config._qa_outcomes = list(by_nodeid.values())


def _outcome_priority(status: str) -> int:
    return {
        "passed": 1,
        "skipped": 2,
        "xfailed": 3,
        "xpassed": 4,
        "failed": 5,
    }.get(status, 0)


def _write_outcome_reports(config, entries=None, path=None) -> None:
    if entries is None:
        entries = getattr(config, "_qa_outcomes", [])
    if path is None:
        path = _report_path()
    entries = sorted(entries, key=lambda entry: entry["nodeid"])
    observed = Counter(entry["status"] for entry in entries)
    counts = {
        status: observed.get(status, 0)
        for status in ("passed", "failed", "skipped", "xfailed", "xpassed")
    }
    payload = {
        "schema_version": 1,
        "timestamp": datetime.now(UTC).isoformat(),
        "counts": counts,
        "tests": entries,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    markdown = [
        "# Pytest Outcome Report",
        "",
        f"Schema version: `{payload['schema_version']}`",
        "",
        "## Counts",
        "",
    ]
    markdown.extend(f"- {status}: {counts.get(status, 0)}" for status in sorted(counts))
    governed_entries = [entry for entry in entries if entry["status"] != "passed"]
    markdown.extend(["", "## Governed outcomes", ""])
    if not governed_entries:
        markdown.append("- none")
    for entry in governed_entries:
        metadata = ", ".join(
            f"{key}={value}" for key, value in sorted(entry["metadata"].items())
        )
        detail = f"; {entry['reason']}" if entry["reason"] else ""
        markdown.append(
            f"- `{entry['status']}` `{entry['nodeid']}`{detail} ({metadata})"
        )
    path.with_suffix(".md").write_text("\n".join(markdown) + "\n")


def pytest_configure(config):
    config._qa_outcomes = []
    config._qa_outcome_by_nodeid = {}


def _worker_report_path(path: Path, worker_id: str) -> Path:
    return path.with_name(f"{path.stem}.{worker_id}{path.suffix}")


@pytest.hookimpl(optionalhook=True)
def pytest_testnodedown(node, error):
    """Collect each xdist worker's report before the master finishes."""
    config = node.config
    if getattr(config, "workerinput", None) is not None:
        return
    worker_id = getattr(node, "workerid", None)
    if worker_id is None:
        worker_id = getattr(getattr(node, "gateway", None), "id", "worker")
    worker_path = _worker_report_path(_report_path(), worker_id)
    if not worker_path.exists():
        return
    entries = json.loads(worker_path.read_text()).get("tests", [])
    config._qa_worker_entries = getattr(config, "_qa_worker_entries", [])
    config._qa_worker_entries.extend(entries)
    worker_path.unlink()
    worker_path.with_suffix(".md").unlink(missing_ok=True)


def pytest_sessionfinish(session, exitstatus):
    _ = exitstatus
    config = session.config
    path = _report_path()
    if getattr(config, "workerinput", None) is not None:
        worker_id = config.workerinput.get("workerid", "worker")
        _write_outcome_reports(config, path=_worker_report_path(path, worker_id))
        return

    entries = getattr(config, "_qa_worker_entries", [])
    if entries:
        _write_outcome_reports(config, entries=entries)
    else:
        worker_reports = sorted(path.parent.glob(f"{path.stem}.gw*.json"))
        if worker_reports:
            for worker_report in worker_reports:
                entries.extend(json.loads(worker_report.read_text()).get("tests", []))
                worker_report.unlink()
            _write_outcome_reports(config, entries=entries)
        else:
            _write_outcome_reports(config)


def pytest_terminal_summary(terminalreporter):
    config = terminalreporter.config
    if getattr(config, "workerinput", None) is not None:
        return
    entries = getattr(config, "_qa_outcomes", [])
    report_path = _report_path()
    if report_path.exists():
        entries = json.loads(report_path.read_text()).get("tests", entries)
    counts = Counter(entry["status"] for entry in entries)
    terminalreporter.write_line(
        "QA outcomes: "
        + ", ".join(
            f"{status}={counts.get(status, 0)}"
            for status in ("passed", "failed", "skipped", "xfailed", "xpassed")
        )
    )
    for entry in entries:
        if entry["status"] in {"xpassed", "xfailed"}:
            metadata = ", ".join(
                f"{key}={value}" for key, value in sorted(entry["metadata"].items())
            )
            terminalreporter.write_line(
                f"{entry['status']}: {entry['nodeid']} [{metadata}] {entry['reason']}"
            )
