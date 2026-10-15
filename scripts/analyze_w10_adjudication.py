#!/usr/bin/env python3
"""Apply blinded W10 adjudication as a declared decision sensitivity."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

try:
    from scripts.build_w10_adjudication_packet import digest
    from scripts.summarize_w10_adaptive_policy import (
        aggregate,
        challenge_decision,
        gate,
        read_cases,
        read_policy_positions,
        read_records,
        summarize_stratum,
    )
except ModuleNotFoundError:  # Direct execution places scripts/ on sys.path.
    from build_w10_adjudication_packet import digest
    from summarize_w10_adaptive_policy import (
        aggregate,
        challenge_decision,
        gate,
        read_cases,
        read_policy_positions,
        read_records,
        summarize_stratum,
    )


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_observation_id(record: dict[str, Any], candidate_id: str) -> str:
    return digest(
        ":".join(
            (
                record["case_id"],
                record["policy"],
                str(record["repetition"]),
                candidate_id,
            )
        )
    )[:20]


def claim_observation_id(record: dict[str, Any], gap_id: str) -> str:
    return digest(
        ":".join(
            (
                "gap",
                record["case_id"],
                record["policy"],
                str(record["repetition"]),
                gap_id,
            )
        )
    )[:20]


def _ratio(matches: int, reviewed: int) -> float | None:
    return matches / reviewed if reviewed else None


def apply_adjudication(
    challenge_records: list[dict[str, Any]],
    anchor_records: list[dict[str, Any]],
    adjudication: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    if (
        adjudication.get("schema_version")
        != "enterprise-evaluation/w10-adjudication-public/1"
    ):
        raise ValueError("unsupported public adjudication schema")
    if adjudication.get("reviewer_kind") != "agent":
        raise ValueError("public adjudication must disclose agent review")
    if adjudication.get("blind_to_policy_and_repetition") is not True:
        raise ValueError("public adjudication must attest blinded review")
    items = adjudication.get("items")
    if not isinstance(items, list):
        raise ValueError("public adjudication must contain an item list")
    indexed: dict[str, dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("adjudication items must be objects")
        observation_id = item.get("observation_id")
        if not isinstance(observation_id, str) or not observation_id:
            raise ValueError("adjudication item lacks an observation ID")
        if observation_id in indexed:
            raise ValueError("adjudication contains duplicate observation IDs")
        indexed[observation_id] = item

    adjusted = copy.deepcopy(challenge_records + anchor_records)
    seen: set[str] = set()
    source_reviewed = source_useful_matches = source_quality_fields = (
        source_quality_matches
    ) = 0
    claim_reviewed = claim_status_matches = 0
    by_reason: dict[str, dict[str, int]] = defaultdict(
        lambda: {"reviewed": 0, "model_agent_matches": 0}
    )
    for record in adjusted:
        if record.get("status") != "completed":
            continue
        for candidate in record.get("candidates", []):
            candidate_id = candidate.get("candidate_id")
            if not isinstance(candidate_id, str):
                continue
            observation_id = source_observation_id(record, candidate_id)
            item = indexed.get(observation_id)
            if item is None:
                continue
            if item.get("item_type") != "source_grade":
                raise ValueError(f"item type mismatch for {observation_id}")
            verdict = item.get("verdict")
            assessment = candidate.get("operational_assessment")
            if not isinstance(verdict, dict) or not isinstance(assessment, dict):
                raise ValueError(f"source adjudication cannot map to {observation_id}")
            agent_useful = verdict.get("useful")
            if type(agent_useful) is not bool:
                raise ValueError(f"source usefulness missing for {observation_id}")
            model_useful = bool(assessment.get("supports_or_challenges"))
            source_reviewed += 1
            source_useful_matches += model_useful == agent_useful
            for field in (
                "currency",
                "relevance",
                "authority",
                "accuracy",
                "purpose",
            ):
                model_value = (assessment.get("quality") or {}).get(field)
                agent_value = verdict.get(field)
                if type(model_value) is int and type(agent_value) is int:
                    source_quality_fields += 1
                    source_quality_matches += model_value == agent_value
            assessment["supports_or_challenges"] = agent_useful
            for reason in item.get("selection_reasons", []):
                by_reason[str(reason)]["reviewed"] += 1
                by_reason[str(reason)]["model_agent_matches"] += (
                    model_useful == agent_useful
                )
            seen.add(observation_id)
        for gap in record.get("gap_results", []):
            gap_id = gap.get("gap_id")
            if not isinstance(gap_id, str):
                continue
            observation_id = claim_observation_id(record, gap_id)
            item = indexed.get(observation_id)
            if item is None:
                continue
            if item.get("item_type") != "claim_closure":
                raise ValueError(f"item type mismatch for {observation_id}")
            verdict = item.get("verdict")
            if not isinstance(verdict, dict) or verdict.get("claim_status") not in {
                "closed",
                "partial",
                "open",
                "ambiguous",
            }:
                raise ValueError(f"claim verdict missing for {observation_id}")
            model_status = gap.get("status")
            agent_status = verdict["claim_status"]
            claim_reviewed += 1
            claim_status_matches += model_status == agent_status
            gap["status"] = agent_status
            for reason in item.get("selection_reasons", []):
                by_reason[str(reason)]["reviewed"] += 1
                by_reason[str(reason)]["model_agent_matches"] += (
                    model_status == agent_status
                )
            seen.add(observation_id)
    missing = sorted(set(indexed) - seen)
    if missing:
        raise ValueError(
            f"adjudication observations did not map to retained records: {missing}"
        )
    if len(seen) != len(indexed):
        raise ValueError("adjudication observation mapping is not one-to-one")

    agreement = {
        "source_usefulness": {
            "reviewed": source_reviewed,
            "matches": source_useful_matches,
            "agreement": _ratio(source_useful_matches, source_reviewed),
        },
        "source_quality_components": {
            "reviewed": source_quality_fields,
            "matches": source_quality_matches,
            "agreement": _ratio(source_quality_matches, source_quality_fields),
        },
        "claim_status": {
            "reviewed": claim_reviewed,
            "matches": claim_status_matches,
            "agreement": _ratio(claim_status_matches, claim_reviewed),
        },
        "by_selection_reason": {
            reason: {
                **counts,
                "agreement": _ratio(counts["model_agent_matches"], counts["reviewed"]),
            }
            for reason, counts in sorted(by_reason.items())
        },
    }
    split = len(challenge_records)
    return adjusted[:split], adjusted[split:], agreement


def sensitivity_summary(
    challenge_records: list[dict[str, Any]],
    anchor_records: list[dict[str, Any]],
    challenge_cases: dict[str, dict[str, Any]],
    anchor_cases: dict[str, dict[str, Any]],
    challenge_positions: dict[tuple[str, int, str], int],
    anchor_positions: dict[tuple[str, int, str], int],
) -> dict[str, Any]:
    for records, cases in (
        (challenge_records, challenge_cases),
        (anchor_records, anchor_cases),
    ):
        for record in records:
            if record.get("status") != "completed":
                continue
            case = cases[record["case_id"]]
            gap_status = {
                item["gap_id"]: item["status"] for item in record["gap_results"]
            }
            record["metrics"]["closed_weight"] = sum(
                claim["importance"]
                for claim in case["claims"]
                if gap_status.get(claim["claim_id"]) == "closed"
            )
            record["metrics"]["total_weight"] = sum(
                claim["importance"] for claim in case["claims"]
            )
    challenge_rows, challenge_summaries = summarize_stratum(
        challenge_records, challenge_cases, challenge_positions
    )
    anchor_rows, anchor_summaries = summarize_stratum(
        anchor_records, anchor_cases, anchor_positions
    )
    decision = challenge_decision(challenge_rows)
    fixed_anchor = aggregate(row for row in anchor_rows if row["policy"] == "fixed")
    full_anchor = aggregate(row for row in anchor_rows if row["policy"] == "full")
    anchor_gate = gate(full_anchor, fixed_anchor)
    # The anchor is a non-inferiority gate with a 2pp closure margin rather
    # than the challenge's 10pp improvement threshold.
    closure_delta = anchor_gate["closure_gain"]
    precision_delta = anchor_gate["precision_delta"]
    anchor_passed = (
        closure_delta is not None
        and closure_delta >= -0.02
        and precision_delta is not None
        and precision_delta >= -0.02
    )
    selected = decision["selected_types_before_anchor"] if anchor_passed else []
    return {
        "challenge": {"summaries": challenge_summaries, "decision": decision},
        "anchor": {
            "summaries": anchor_summaries,
            "fixed": fixed_anchor,
            "full": full_anchor,
            "closure_delta": closure_delta,
            "precision_delta": precision_delta,
            "passed": anchor_passed,
        },
        "selected_challenge_types": selected,
        "decision": (
            "retain_fixed_default"
            if not selected
            else "allow_bounded_adaptation_for_selected_types"
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--challenge", type=Path, required=True)
    parser.add_argument("--challenge-cases", type=Path, required=True)
    parser.add_argument("--anchor", type=Path, required=True)
    parser.add_argument("--anchor-cases", type=Path, required=True)
    parser.add_argument("--primary-summary", type=Path, required=True)
    parser.add_argument("--adjudication", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    primary = json.loads(args.primary_summary.read_text())
    if primary.get(
        "schema_version"
    ) != "enterprise-evaluation/w10-summary/1" or not primary.get("complete"):
        raise ValueError("primary W10 summary must be complete")
    challenge_records = read_records(args.challenge)
    anchor_records = read_records(args.anchor)
    adjusted_challenge, adjusted_anchor, agreement = apply_adjudication(
        challenge_records,
        anchor_records,
        json.loads(args.adjudication.read_text()),
    )
    sensitivity = sensitivity_summary(
        adjusted_challenge,
        adjusted_anchor,
        read_cases(args.challenge_cases),
        read_cases(args.anchor_cases),
        read_policy_positions(args.challenge),
        read_policy_positions(args.anchor),
    )
    result = {
        "schema_version": "enterprise-evaluation/w10-adjudication-analysis/1",
        "analysis_role": "declared sensitivity; frozen model-graded primary is unchanged",
        "primary_summary_sha256": file_digest(args.primary_summary),
        "adjudication_sha256": file_digest(args.adjudication),
        "agreement": agreement,
        "adjudication_sensitivity": sensitivity,
        "decision_changed": sensitivity["decision"] != primary.get("decision"),
        "primary_decision": primary.get("decision"),
        "adjudicated_sensitivity_decision": sensitivity["decision"],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(
        json.dumps(
            {
                "decision_changed": result["decision_changed"],
                "primary_decision": result["primary_decision"],
                "adjudicated_sensitivity_decision": result[
                    "adjudicated_sensitivity_decision"
                ],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
