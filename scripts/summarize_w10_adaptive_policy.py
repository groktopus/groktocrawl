#!/usr/bin/env python3
"""Summarize W10 policy trials and apply the frozen replacement gates."""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import Any

POLICIES = ("fixed", "unconstrained", "gap", "gated", "full")
_QUERY_WORDS = re.compile(r"[a-z0-9]+")


def read_cases(path: Path) -> dict[str, dict[str, Any]]:
    payload = json.loads(path.read_text())
    return {case["case_id"]: case for case in payload["cases"]}


def read_records(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(item.read_text())
        for item in sorted((path / "records").glob("*.json"))
    ]


def trial_metrics(
    record: dict[str, Any], case: dict[str, Any], *, policy_position: int | None = None
) -> dict[str, Any]:
    if record["status"] != "completed":
        return {
            "case_id": record["case_id"],
            "policy": record["policy"],
            "repetition": record["repetition"],
            "challenge_type": case["challenge_type"],
            "policy_position": policy_position,
            "status": "failed",
        }
    candidates = record["candidates"]
    proposals = record.get("proposals", [])
    admitted = [item for item in candidates if item["admitted"]]
    useful = [
        item
        for item in admitted
        if item.get("operational_assessment", {}).get("supports_or_challenges")
    ]
    acquired = [item for item in candidates if item["acquisition_status"] == "acquired"]
    useful_acquired = [
        item
        for item in acquired
        if item.get("operational_assessment", {}).get("supports_or_challenges")
    ]
    followups = len(record["attempts"]) - 1
    gainful_attempts: set[int] = set()
    for attempt in range(1, followups + 1):
        gained = False
        for item in acquired:
            origins = item.get("search_origins", [])
            selected_on_attempt = item.get("selected_on_attempt")
            if selected_on_attempt is None and "selected_on_attempt" not in item:
                selected_on_attempt = (
                    min(origin["attempt"] for origin in origins) if origins else None
                )
            if selected_on_attempt != attempt:
                continue
            assessment = item.get("operational_assessment", {})
            gained = gained or (
                (
                    assessment.get("supports_or_challenges", False)
                    and assessment.get("marginal_value", False)
                )
                or assessment.get("improves_currency", False)
                or assessment.get("improves_authority", False)
                or assessment.get("resolves_contradiction", False)
            )
        if gained:
            gainful_attempts.add(attempt)
    gainful_followups = len(gainful_attempts)
    last_gain = max(gainful_attempts, default=0)
    unnecessary = sum(attempt > last_gain for attempt in range(1, followups + 1))
    gap_status = {item["gap_id"]: item["status"] for item in record["gap_results"]}
    closed_claims = sum(
        gap_status.get(claim["claim_id"]) == "closed" for claim in case["claims"]
    )
    unsupported_high = sum(
        claim["importance"] == 3 and gap_status.get(claim["claim_id"]) != "closed"
        for claim in case["claims"]
    )
    metrics = record["metrics"]
    interim_gaps = (record.get("interim_assessment") or {}).get("gaps", [])
    interim_all_closed = bool(interim_gaps) and all(
        item.get("status") == "closed" for item in interim_gaps
    )
    final_all_closed = bool(record["gap_results"]) and all(
        item.get("status") == "closed" for item in record["gap_results"]
    )
    return {
        "case_id": record["case_id"],
        "policy": record["policy"],
        "repetition": record["repetition"],
        "challenge_type": case["challenge_type"],
        "policy_position": policy_position,
        "status": "completed",
        "closed_weight": metrics["closed_weight"],
        "total_weight": metrics["total_weight"],
        "closed_claims": closed_claims,
        "total_claims": len(case["claims"]),
        "admitted": len(admitted),
        "useful": len(useful),
        "acquired": len(acquired),
        "useful_acquired": len(useful_acquired),
        "proposals_made": len(proposals),
        "proposals_evaluated": sum(
            item.get("reason") != "stopped_after_prior_round" for item in proposals
        ),
        "proposals_accepted": sum(item.get("admitted", False) for item in proposals),
        "followup_queries": followups,
        "gainful_followup_queries": gainful_followups,
        "unnecessary_followup_queries": unnecessary,
        "unsupported_high_importance": unsupported_high,
        "within_bounds": (
            metrics["searches"] <= 3
            and metrics["model_calls"] <= 3
            and metrics["admitted_count"] <= 8
            and metrics["elapsed_ms"] <= 180_000
        ),
        "elapsed_ms": metrics["elapsed_ms"],
        "model_calls": metrics["model_calls"],
        "round_decisions": record.get("round_decisions", []),
        "interim_all_closed": interim_all_closed,
        "final_all_closed": final_all_closed,
        "premature_stop_disagreement": interim_all_closed and not final_all_closed,
    }


def aggregate(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    rows = list(rows)
    completed = [row for row in rows if row["status"] == "completed"]
    closed_weight = sum(row["closed_weight"] for row in completed)
    total_weight = sum(row["total_weight"] for row in completed)
    closed_claims = sum(row["closed_claims"] for row in completed)
    total_claims = sum(row["total_claims"] for row in completed)
    admitted = sum(row["admitted"] for row in completed)
    useful = sum(row["useful"] for row in completed)
    acquired = sum(row.get("acquired", 0) for row in completed)
    useful_acquired = sum(row.get("useful_acquired", 0) for row in completed)
    proposals = sum(row.get("proposals_made", 0) for row in completed)
    evaluated_proposals = sum(row.get("proposals_evaluated", 0) for row in completed)
    accepted_proposals = sum(row.get("proposals_accepted", 0) for row in completed)
    followups = sum(row["followup_queries"] for row in completed)
    gainful_followups = sum(row.get("gainful_followup_queries", 0) for row in completed)
    unnecessary = sum(row["unnecessary_followup_queries"] for row in completed)
    interim_stop_trials = sum(row.get("interim_all_closed", False) for row in completed)
    premature_stop_disagreements = sum(
        row.get("premature_stop_disagreement", False) for row in completed
    )
    return {
        "trials": len(rows),
        "completed": len(completed),
        "failed": len(rows) - len(completed),
        "weighted_closure": closed_weight / total_weight if total_weight else None,
        "equal_weight_closure": closed_claims / total_claims if total_claims else None,
        "precision": useful / admitted if admitted else None,
        "pre_admission_precision": (useful_acquired / acquired if acquired else None),
        "proposal_yield": (
            accepted_proposals / evaluated_proposals if evaluated_proposals else None
        ),
        "useful_query_yield": gainful_followups / followups if followups else None,
        "proposals_made": proposals,
        "proposals_evaluated": evaluated_proposals,
        "proposals_accepted": accepted_proposals,
        "gainful_followup_queries": gainful_followups,
        "unsupported_high_importance": sum(
            row["unsupported_high_importance"] for row in completed
        ),
        "unnecessary_query_rate": unnecessary / followups if followups else 0.0,
        "within_bounds": bool(completed)
        and all(row["within_bounds"] for row in completed),
        "elapsed_ms": [row["elapsed_ms"] for row in completed],
        "model_calls": [row.get("model_calls", 0) for row in completed],
        "interim_all_closed_trials": interim_stop_trials,
        "premature_stop_disagreements": premature_stop_disagreements,
        "premature_stop_disagreement_rate": (
            premature_stop_disagreements / interim_stop_trials
            if interim_stop_trials
            else None
        ),
    }


def gate(
    full: dict[str, Any],
    fixed: dict[str, Any],
    *,
    closure_key: str = "weighted_closure",
) -> dict[str, Any]:
    closure_gain = (
        full[closure_key] - fixed[closure_key]
        if full[closure_key] is not None and fixed[closure_key] is not None
        else None
    )
    precision_delta = (
        full["precision"] - fixed["precision"]
        if full["precision"] is not None and fixed["precision"] is not None
        else None
    )
    checks = {
        "closure_gain_at_least_10pp": closure_gain is not None and closure_gain >= 0.10,
        "no_more_unsupported_high_importance": (
            full["unsupported_high_importance"] <= fixed["unsupported_high_importance"]
        ),
        "precision_within_5pp": precision_delta is not None
        and precision_delta >= -0.05,
        "unnecessary_queries_at_most_10pct": full["unnecessary_query_rate"] <= 0.10,
        "within_bounds": full["within_bounds"],
        "no_higher_failure_rate": full["failed"] <= fixed["failed"],
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "closure_gain": closure_gain,
        "precision_delta": precision_delta,
    }


def summarize_stratum(
    records: list[dict[str, Any]],
    cases: dict[str, dict[str, Any]],
    positions: dict[tuple[str, int, str], int],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows = [
        trial_metrics(
            record,
            cases[record["case_id"]],
            policy_position=positions.get(
                (record["case_id"], record["repetition"], record["policy"])
            ),
        )
        for record in records
    ]
    grouped: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(row["policy"], row["repetition"])].append(row)
    summaries = {
        f"{policy}:{repetition}": aggregate(items)
        for (policy, repetition), items in sorted(grouped.items())
    }
    return rows, summaries


def read_policy_positions(path: Path) -> dict[tuple[str, int, str], int]:
    entries = json.loads((path / "work-order.json").read_text())["entries"]
    return {
        (item["case_id"], item["repetition"], item["policy"]): (
            (item["position"] - 1) % len(POLICIES)
        )
        + 1
        for item in entries
    }


def by_policy_position(rows: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row["policy_position"] is not None:
            grouped[(row["policy"], row["policy_position"])].append(row)
    return {
        f"{policy}:{position}": aggregate(items)
        for (policy, position), items in sorted(grouped.items())
    }


def _normalized_query(value: str) -> str:
    return " ".join(_QUERY_WORDS.findall(value.casefold()))


def _attempt_gained(record: dict[str, Any], attempt: int) -> bool:
    for item in record.get("candidates", []):
        if item.get("acquisition_status") != "acquired":
            continue
        origins = item.get("search_origins", [])
        selected_on_attempt = item.get("selected_on_attempt")
        if selected_on_attempt is None and "selected_on_attempt" not in item:
            selected_on_attempt = (
                min(origin["attempt"] for origin in origins) if origins else None
            )
        if selected_on_attempt != attempt:
            continue
        assessment = item.get("operational_assessment", {})
        if (
            (
                assessment.get("supports_or_challenges", False)
                and assessment.get("marginal_value", False)
            )
            or assessment.get("improves_currency", False)
            or assessment.get("improves_authority", False)
            or assessment.get("resolves_contradiction", False)
        ):
            return True
    return False


def gate_rejection_audit(records: list[dict[str, Any]]) -> dict[str, Any]:
    executed: dict[tuple[str, int, str], list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        if record.get("status") != "completed":
            continue
        for attempt, item in enumerate(record.get("attempts", [])[1:], 1):
            executed[
                (
                    record["case_id"],
                    record["repetition"],
                    _normalized_query(item["query"]),
                )
            ].append(
                {
                    "policy": record["policy"],
                    "evidence_gain": _attempt_gained(record, attempt),
                }
            )
    audited = []
    for record in records:
        if record.get("status") != "completed":
            continue
        for proposal in record.get("proposals", []):
            if proposal.get("admitted", False) or proposal.get("reason") in {
                "policy_has_no_proposal_gate",
                "stopped_after_prior_round",
            }:
                continue
            matches = executed.get(
                (
                    record["case_id"],
                    record["repetition"],
                    _normalized_query(proposal["query"]),
                ),
                [],
            )
            verdict = (
                "observed_gain_elsewhere"
                if any(item["evidence_gain"] for item in matches)
                else "observed_no_gain_elsewhere"
                if matches
                else "unknown_not_executed_equivalently"
            )
            audited.append(
                {
                    "case_id": record["case_id"],
                    "repetition": record["repetition"],
                    "policy": record["policy"],
                    "query": proposal["query"],
                    "rejection_reason": proposal["reason"],
                    "verdict": verdict,
                    "equivalent_executions": matches,
                }
            )
    return {
        "exact_normalized_query_matches_only": True,
        "rejections": len(audited),
        "observed_gain_elsewhere": sum(
            item["verdict"] == "observed_gain_elsewhere" for item in audited
        ),
        "observed_no_gain_elsewhere": sum(
            item["verdict"] == "observed_no_gain_elsewhere" for item in audited
        ),
        "unknown": sum(
            item["verdict"] == "unknown_not_executed_equivalently" for item in audited
        ),
        "items": audited,
    }


def challenge_decision(
    rows: list[dict[str, Any]], *, closure_key: str = "weighted_closure"
) -> dict[str, Any]:
    types = sorted({row["challenge_type"] for row in rows})
    by_type: dict[str, Any] = {}
    selected = []
    for challenge_type in types:
        repetitions = []
        for repetition in range(3):
            selected_rows = [
                row
                for row in rows
                if row["challenge_type"] == challenge_type
                and row["repetition"] == repetition
            ]
            result = gate(
                aggregate(row for row in selected_rows if row["policy"] == "full"),
                aggregate(row for row in selected_rows if row["policy"] == "fixed"),
                closure_key=closure_key,
            )
            result["repetition"] = repetition
            repetitions.append(result)
        passes = sum(item["passed"] for item in repetitions)
        by_type[challenge_type] = {
            "passed_repetitions": passes,
            "required_repetitions": 2,
            "repetitions": repetitions,
        }
        if passes >= 2:
            selected.append(challenge_type)
    return {"by_challenge_type": by_type, "selected_types_before_anchor": selected}


def paired_effects(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    indexed = {
        (row["case_id"], row["repetition"], row["policy"]): row
        for row in rows
        if row["status"] == "completed"
    }
    pairs = []
    keys = sorted({(row["case_id"], row["repetition"]) for row in rows})
    for case_id, repetition in keys:
        fixed = indexed.get((case_id, repetition, "fixed"))
        full = indexed.get((case_id, repetition, "full"))
        if fixed is None or full is None:
            continue
        fixed_precision = (
            fixed["useful"] / fixed["admitted"] if fixed["admitted"] else None
        )
        full_precision = full["useful"] / full["admitted"] if full["admitted"] else None
        pairs.append(
            {
                "case_id": case_id,
                "challenge_type": full["challenge_type"],
                "repetition": repetition,
                "weighted_closure_gain": (
                    full["closed_weight"] / full["total_weight"]
                    - fixed["closed_weight"] / fixed["total_weight"]
                ),
                "precision_delta": (
                    full_precision - fixed_precision
                    if full_precision is not None and fixed_precision is not None
                    else None
                ),
                "additional_followup_queries": full["followup_queries"],
                "elapsed_ms_delta": full["elapsed_ms"] - fixed["elapsed_ms"],
            }
        )
    return pairs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--challenge", type=Path, required=True)
    parser.add_argument("--challenge-cases", type=Path, required=True)
    parser.add_argument("--anchor", type=Path, required=True)
    parser.add_argument("--anchor-cases", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    challenge_cases = read_cases(args.challenge_cases)
    anchor_cases = read_cases(args.anchor_cases)
    challenge_records = read_records(args.challenge)
    anchor_records = read_records(args.anchor)
    challenge_rows, challenge_summaries = summarize_stratum(
        challenge_records, challenge_cases, read_policy_positions(args.challenge)
    )
    anchor_rows, anchor_summaries = summarize_stratum(
        anchor_records, anchor_cases, read_policy_positions(args.anchor)
    )
    decision = challenge_decision(challenge_rows)
    equal_weight_decision = challenge_decision(
        challenge_rows, closure_key="equal_weight_closure"
    )
    full_anchor = aggregate(row for row in anchor_rows if row["policy"] == "full")
    fixed_anchor = aggregate(row for row in anchor_rows if row["policy"] == "fixed")
    anchor_closure_delta = (
        full_anchor["weighted_closure"] - fixed_anchor["weighted_closure"]
        if full_anchor["weighted_closure"] is not None
        and fixed_anchor["weighted_closure"] is not None
        else None
    )
    anchor_precision_delta = (
        full_anchor["precision"] - fixed_anchor["precision"]
        if full_anchor["precision"] is not None
        and fixed_anchor["precision"] is not None
        else None
    )
    expected_challenge = len(challenge_cases) * len(POLICIES) * 3
    expected_anchor = len(anchor_cases) * len(POLICIES) * 3
    complete = (
        len(challenge_records) == expected_challenge
        and len(anchor_records) == expected_anchor
        and all(row["status"] == "completed" for row in challenge_rows + anchor_rows)
    )
    anchor_gate = {
        "closure_degradation_at_most_2pp": (
            anchor_closure_delta is not None and anchor_closure_delta >= -0.02
        ),
        "precision_degradation_at_most_2pp": (
            anchor_precision_delta is not None and anchor_precision_delta >= -0.02
        ),
        "closure_delta": anchor_closure_delta,
        "precision_delta": anchor_precision_delta,
    }
    anchor_gate["passed"] = all(
        value for key, value in anchor_gate.items() if key.endswith("2pp")
    )
    selected = (
        decision["selected_types_before_anchor"]
        if complete and anchor_gate["passed"]
        else []
    )
    equal_anchor_delta = (
        full_anchor["equal_weight_closure"] - fixed_anchor["equal_weight_closure"]
        if full_anchor["equal_weight_closure"] is not None
        and fixed_anchor["equal_weight_closure"] is not None
        else None
    )
    equal_anchor_passed = (
        equal_anchor_delta is not None
        and equal_anchor_delta >= -0.02
        and anchor_gate["precision_degradation_at_most_2pp"]
    )
    leave_one_case_out = {
        case_id: challenge_decision(
            [row for row in challenge_rows if row["case_id"] != case_id]
        )["selected_types_before_anchor"]
        for case_id in sorted(challenge_cases)
    }
    payload = {
        "schema_version": "enterprise-evaluation/w10-summary/1",
        "complete": complete,
        "expected_records": {
            "challenge": expected_challenge,
            "anchor": expected_anchor,
        },
        "observed_records": {
            "challenge": len(challenge_records),
            "anchor": len(anchor_records),
        },
        "challenge": {
            "summaries": challenge_summaries,
            "by_policy_position": by_policy_position(challenge_rows),
            "gate_rejection_audit": gate_rejection_audit(challenge_records),
            "decision": decision,
            "paired_full_vs_fixed_effects": paired_effects(challenge_rows),
        },
        "anchor": {
            "summaries": anchor_summaries,
            "by_policy_position": by_policy_position(anchor_rows),
            "gate_rejection_audit": gate_rejection_audit(anchor_records),
            "fixed": fixed_anchor,
            "full": full_anchor,
            "gate": anchor_gate,
            "paired_full_vs_fixed_effects": paired_effects(anchor_rows),
        },
        "selected_challenge_types": selected,
        "sensitivity": {
            "equal_claim_weights": {
                "challenge": equal_weight_decision,
                "anchor_closure_delta": equal_anchor_delta,
                "anchor_passed": equal_anchor_passed,
                "selected_types": (
                    equal_weight_decision["selected_types_before_anchor"]
                    if complete and equal_anchor_passed
                    else []
                ),
            },
            "leave_one_challenge_case_out": leave_one_case_out,
            "unavailable_sources_removed": (
                "Invariant: unavailable sources are never admitted or used to close claims."
            ),
            "ambiguous_closure_as_open": (
                "Applied in every primary and sensitivity calculation."
            ),
        },
        "decision": (
            "retain_fixed_default"
            if not selected
            else "allow_bounded_adaptation_for_selected_types"
        ),
        "interpretation_limit": (
            "Repeated runs reuse the same cases and are not independent samples; "
            "report effect sizes and case distributions, not population p-values."
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(
        json.dumps(
            {key: payload[key] for key in ("complete", "observed_records", "decision")},
            indent=2,
        )
    )
    return 0 if complete else 2


if __name__ == "__main__":
    raise SystemExit(main())
