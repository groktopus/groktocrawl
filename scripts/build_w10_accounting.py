#!/usr/bin/env python3
"""Build a redacted W10 accounting and variation dossier from retained records."""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

try:
    from scripts.summarize_w10_adaptive_policy import POLICIES, read_cases, read_records
except ModuleNotFoundError:  # Direct execution places scripts/ on sys.path.
    from summarize_w10_adaptive_policy import POLICIES, read_cases, read_records


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


def _distribution(values: list[float | int]) -> dict[str, float | int | None]:
    if not values:
        return {"count": 0, "min": None, "median": None, "max": None}
    return {
        "count": len(values),
        "min": min(values),
        "median": statistics.median(values),
        "max": max(values),
    }


def _trial_row(record: dict[str, Any], case: dict[str, Any]) -> dict[str, Any]:
    completed = record.get("status") == "completed"
    admitted = [item for item in record.get("candidates", []) if item.get("admitted")]
    useful = [
        item
        for item in admitted
        if (item.get("operational_assessment") or {}).get("supports_or_challenges")
    ]
    metrics = record.get("metrics") or {}
    total_weight = metrics.get("total_weight")
    closed_weight = metrics.get("closed_weight")
    return {
        "case_id": record.get("case_id"),
        "challenge_type": case["challenge_type"],
        "policy": record.get("policy"),
        "repetition": record.get("repetition"),
        "completed": completed,
        "searches": len(record.get("attempts", [])),
        "model_calls": metrics.get("model_calls") if completed else None,
        "elapsed_ms": metrics.get("elapsed_ms") if completed else None,
        "admitted": len(admitted),
        "weighted_closure": (
            closed_weight / total_weight
            if completed
            and type(closed_weight) in {int, float}
            and type(total_weight) in {int, float}
            and total_weight
            else None
        ),
        "precision": len(useful) / len(admitted) if completed and admitted else None,
    }


def _group_distributions(
    rows: list[dict[str, Any]], key: str
) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row[key])].append(row)
    result = {}
    for value, items in sorted(grouped.items()):
        completed = [item for item in items if item["completed"]]
        result[value] = {
            "trials": len(items),
            "completed": len(completed),
            "failed": len(items) - len(completed),
            **{
                metric: _distribution(
                    [item[metric] for item in completed if item[metric] is not None]
                )
                for metric in (
                    "searches",
                    "model_calls",
                    "elapsed_ms",
                    "admitted",
                    "weighted_closure",
                    "precision",
                )
            },
        }
    return result


def _private_manifest(run_dirs: list[Path]) -> tuple[int, str]:
    items = []
    for run_index, run_dir in enumerate(run_dirs):
        for path in sorted((run_dir / "private-acquisitions").glob("*.json")):
            items.append(
                {
                    "run_index": run_index,
                    "name_sha256": hashlib.sha256(path.name.encode()).hexdigest(),
                    "bytes": path.stat().st_size,
                    "sha256": file_digest(path),
                }
            )
    return len(items), canonical_digest(items)


def build_accounting(
    run_dirs: list[Path],
    case_paths: list[Path],
    summary: dict[str, Any],
    *,
    summary_sha256: str,
) -> dict[str, Any]:
    cases: dict[str, dict[str, Any]] = {}
    for path in case_paths:
        cases.update(read_cases(path))
    records = [record for run_dir in run_dirs for record in read_records(run_dir)]
    rows: list[dict[str, Any]] = []
    acquisition_status: Counter[str] = Counter()
    admission_reason: Counter[str] = Counter()
    terminal_reasons: Counter[str] = Counter()
    proposal_reasons: Counter[str] = Counter()
    missing: Counter[str] = Counter()
    queries = candidates = sightings = acquired = admitted = source_links = 0
    for record in records:
        case_id = record.get("case_id")
        if case_id not in cases:
            missing["unknown_case"] += 1
            continue
        rows.append(_trial_row(record, cases[case_id]))
        attempts = record.get("attempts")
        if not isinstance(attempts, list):
            missing["attempt_list"] += 1
            attempts = []
        queries += len(attempts)
        for attempt in attempts:
            if not isinstance(attempt, dict) or not isinstance(
                attempt.get("query"), str
            ):
                missing["query_identity"] += 1
        if record.get("status") == "completed":
            reason = record.get("stop_reason")
            if not isinstance(reason, str) or not reason:
                missing["terminal_stop_reason"] += 1
            else:
                terminal_reasons[reason] += 1
        for proposal in record.get("proposals", []):
            reason = proposal.get("reason")
            proposal_reasons[str(reason) if reason is not None else "missing"] += 1
            if reason is None:
                missing["proposal_disposition"] += 1
        for candidate in record.get("candidates", []):
            candidates += 1
            origins = candidate.get("search_origins")
            if not isinstance(origins, list):
                missing["candidate_search_origins"] += 1
                origins = []
            sightings += len(origins)
            status = candidate.get("acquisition_status")
            if not isinstance(status, str) or not status:
                missing["candidate_acquisition_status"] += 1
                status = "missing"
            acquisition_status[status] += 1
            is_admitted = candidate.get("admitted")
            if type(is_admitted) is not bool:
                missing["candidate_admission_boolean"] += 1
            elif is_admitted:
                admitted += 1
            reason = candidate.get("admission_reason")
            if not isinstance(reason, str) or not reason:
                missing["candidate_admission_reason"] += 1
                reason = "missing"
            admission_reason[reason] += 1
            if status == "acquired":
                acquired += 1
                assessment = candidate.get("operational_assessment")
                if not isinstance(assessment, dict):
                    missing["acquired_candidate_assessment"] += 1
                else:
                    links = assessment.get("relevant_gap_ids")
                    if not isinstance(links, list):
                        missing["source_to_claim_links"] += 1
                    else:
                        source_links += len(links)

    expected = len(cases) * len(POLICIES) * 3
    private_count, private_digest = _private_manifest(run_dirs)
    observed_completed = sum(record.get("status") == "completed" for record in records)
    summary_complete = summary.get("complete") is True
    summary_expected = summary.get("expected_records") or {}
    summary_observed = summary.get("observed_records") or {}
    summary_expected_total = sum(
        value for value in summary_expected.values() if type(value) is int
    )
    summary_observed_total = sum(
        value for value in summary_observed.values() if type(value) is int
    )
    gates = {
        "expected_record_count": len(records) == expected,
        "all_records_completed": observed_completed == len(records),
        "one_private_acquisition_file_per_record": private_count == len(records),
        "no_missing_accounting_fields": not missing,
        "frozen_summary_complete": summary_complete,
        "frozen_summary_schema": (
            summary.get("schema_version") == "enterprise-evaluation/w10-summary/1"
        ),
        "summary_counts_match_records": (
            summary_expected_total == expected
            and summary_observed_total == len(records)
        ),
    }
    return {
        "schema_version": "enterprise-evaluation/w10-public-accounting/1",
        "redaction": (
            "No query text, URL, title, excerpt, model response, private path, or "
            "credential is included."
        ),
        "inputs": {
            "summary_sha256": summary_sha256,
            "case_file_sha256": [file_digest(path) for path in case_paths],
            "private_acquisition_manifest": {
                "files": private_count,
                "canonical_manifest_sha256": private_digest,
            },
        },
        "totals": {
            "expected_trials": expected,
            "observed_trials": len(records),
            "completed_trials": observed_completed,
            "failed_trials": len(records) - observed_completed,
            "executed_queries": queries,
            "candidate_records": candidates,
            "search_result_sightings": sightings,
            "acquired_candidates": acquired,
            "admitted_candidates": admitted,
            "excluded_candidates": candidates - admitted,
            "source_to_claim_links": source_links,
        },
        "dispositions": {
            "acquisition_status": dict(sorted(acquisition_status.items())),
            "admission_reason": dict(sorted(admission_reason.items())),
            "proposal_reason": dict(sorted(proposal_reasons.items())),
            "terminal_stop_reason": dict(sorted(terminal_reasons.items())),
        },
        "variation": {
            "by_policy": _group_distributions(rows, "policy"),
            "by_challenge_type": _group_distributions(rows, "challenge_type"),
        },
        "missing_accounting": dict(sorted(missing.items())),
        "completion_gates": gates,
        "complete": all(gates.values()),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, action="append", required=True)
    parser.add_argument("--cases", type=Path, action="append", required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = build_accounting(
        args.run_dir,
        args.cases,
        json.loads(args.summary.read_text()),
        summary_sha256=file_digest(args.summary),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"complete": result["complete"], **result["totals"]}, indent=2))
    return 0 if result["complete"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
