#!/usr/bin/env python3
"""Summarize paired W11 flat-HTTP and recorded-continuation retrieval."""

from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

ARMS = {"flat_http", "recorded_continuation"}


def _jaccard(left: list[str], right: list[str]) -> float:
    a, b = set(left), set(right)
    return len(a & b) / len(a | b) if a or b else 1.0


def _bounded(workflow: dict[str, Any]) -> bool:
    budgets = workflow.get("budgets")
    if not isinstance(budgets, dict):
        return False
    limits, used = budgets.get("limits"), budgets.get("used")
    if not isinstance(limits, dict) or not isinstance(used, dict):
        return False
    return all(
        isinstance(limit, int)
        and isinstance(used.get(key), int)
        and 0 <= used[key] <= limit
        for key, limit in limits.items()
        if key != "engines_per_query"
    )


def summarize(records: list[dict[str, Any]], *, expected_pairs: int) -> dict[str, Any]:
    grouped: dict[tuple[str, int], dict[str, dict[str, Any]]] = defaultdict(dict)
    failures = []
    for record in records:
        if record.get("status") != "completed":
            failures.append(
                {
                    "case_id": record.get("case_id"),
                    "repetition": record.get("repetition"),
                    "arm": record.get("arm"),
                    "error_type": record.get("error_type"),
                }
            )
            continue
        key = (str(record["case_id"]), int(record["repetition"]))
        arm = str(record["arm"])
        if arm in grouped[key]:
            raise ValueError(f"duplicate record for {key} {arm}")
        grouped[key][arm] = record

    pairs = []
    incomplete = []
    for key in sorted(grouped):
        arms = grouped[key]
        if set(arms) != ARMS:
            incomplete.append({"case_id": key[0], "repetition": key[1], "arms": sorted(arms)})
            continue
        flat = arms["flat_http"]
        research = arms["recorded_continuation"]
        workflow = research.get("workflow")
        workflow_accounted = (
            isinstance(workflow, dict)
            and workflow.get("caller_completed") is True
            and workflow.get("stop_reason") == "caller_completed"
            and _bounded(workflow)
        )
        plan_equal = flat["query_plan_sha256"] == research["query_plan_sha256"]
        scope_equal = flat["engine_scope_sha256"] == research["engine_scope_sha256"]
        if len(flat["attempts"]) != len(research["attempts"]):
            raise ValueError(f"attempt count differs for pair {key}")
        attempts = []
        for index, (left, right) in enumerate(zip(flat["attempts"], research["attempts"], strict=True)):
            attempts.append(
                {
                    "index": index,
                    "query_equal": left["query_sha256"] == right["query_sha256"],
                    "result_set_jaccard": _jaccard(
                        left["result_url_sha256"], right["result_url_sha256"]
                    ),
                    "returned_result_delta": right["returned_results"] - left["returned_results"],
                    "reported_result_count_delta": right["result_count"] - left["result_count"],
                }
            )
        pairs.append(
            {
                "case_id": key[0],
                "repetition": key[1],
                "challenge_type": flat["challenge_type"],
                "control_policy": flat["control_policy"],
                "plan_equal": plan_equal,
                "scope_equal": scope_equal,
                "all_query_hashes_equal": all(item["query_equal"] for item in attempts),
                "workflow_accounted_and_bounded": workflow_accounted,
                "mean_result_set_jaccard": statistics.fmean(
                    item["result_set_jaccard"] for item in attempts
                ),
                "elapsed_ms_delta": research["elapsed_ms"] - flat["elapsed_ms"],
                "attempts": attempts,
            }
        )
    complete = len(pairs) == expected_pairs and not failures and not incomplete
    integrity = {
        "all_pairs_present": len(pairs) == expected_pairs and not incomplete,
        "no_failed_trials": not failures,
        "all_query_plans_equal": bool(pairs) and all(pair["plan_equal"] for pair in pairs),
        "all_engine_scopes_equal": bool(pairs) and all(pair["scope_equal"] for pair in pairs),
        "all_query_hashes_equal": bool(pairs)
        and all(pair["all_query_hashes_equal"] for pair in pairs),
        "all_workflows_accounted_and_bounded": bool(pairs)
        and all(pair["workflow_accounted_and_bounded"] for pair in pairs),
    }
    return {
        "schema_version": "enterprise-evaluation/w11-general-retrieval-summary/1",
        "complete": complete,
        "expected_pairs": expected_pairs,
        "observed_pairs": len(pairs),
        "integrity": integrity,
        "hard_gate_passed": complete and all(integrity.values()),
        "failures": failures,
        "incomplete_pairs": incomplete,
        "mean_result_set_jaccard": (
            statistics.fmean(pair["mean_result_set_jaccard"] for pair in pairs)
            if pairs
            else None
        ),
        "median_elapsed_ms_delta": (
            statistics.median(pair["elapsed_ms_delta"] for pair in pairs)
            if pairs
            else None
        ),
        "pairs": pairs,
        "interpretation": (
            "Result-set overlap is a transport-equivalence diagnostic. It is not a research-quality score."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("records", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--expected-pairs", type=int, required=True)
    args = parser.parse_args()
    records = [json.loads(path.read_text()) for path in sorted(args.records.glob("*.json"))]
    result = summarize(records, expected_pairs=args.expected_pairs)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({key: result[key] for key in ("complete", "observed_pairs", "hard_gate_passed")}, indent=2))
    return 0 if result["hard_gate_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
