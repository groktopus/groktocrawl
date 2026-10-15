#!/usr/bin/env python3
"""Record the final W10 policy selection for downstream experiments."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

OUTCOMES = (
    "keep_fixed_retrieval",
    "bounded_recovery",
    "full_bounded_policy",
    "run_followup_experiment",
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_selection(
    primary: dict[str, Any],
    adjudication: dict[str, Any],
    accounting: dict[str, Any],
    cases: dict[str, Any],
    *,
    outcome: str,
    selected_types: list[str],
    rationale: str,
    reversal_condition: str,
    input_digests: dict[str, str],
) -> dict[str, Any]:
    if primary.get("schema_version") != "enterprise-evaluation/w10-summary/1":
        raise ValueError("unsupported primary W10 summary schema")
    if primary.get("complete") is not True:
        raise ValueError("primary W10 summary must be complete")
    if (
        adjudication.get("schema_version")
        != "enterprise-evaluation/w10-adjudication-analysis/1"
    ):
        raise ValueError("unsupported W10 adjudication-analysis schema")
    if (
        accounting.get("schema_version")
        != "enterprise-evaluation/w10-public-accounting/1"
    ):
        raise ValueError("unsupported W10 accounting schema")
    if accounting.get("complete") is not True:
        raise ValueError("W10 accounting must be complete")
    if outcome not in OUTCOMES:
        raise ValueError("unsupported W10 policy-selection outcome")
    if not rationale.strip() or not reversal_condition.strip():
        raise ValueError("rationale and reversal condition are required")

    known_types = sorted({str(case["challenge_type"]) for case in cases["cases"]})
    chosen = sorted(set(selected_types))
    if len(chosen) != len(selected_types) or not set(chosen) <= set(known_types):
        raise ValueError("selected challenge types must be unique and known")
    primary_selected = set(primary.get("selected_challenge_types", []))
    if not primary_selected <= set(known_types):
        raise ValueError("primary W10 summary selected an unknown challenge type")

    if adjudication.get("primary_summary_sha256") != input_digests["primary_summary"]:
        raise ValueError("adjudication analysis is not bound to the primary summary")
    if (accounting.get("inputs") or {}).get("summary_sha256") != input_digests[
        "primary_summary"
    ]:
        raise ValueError("accounting is not bound to the primary summary")

    changed = adjudication.get("decision_changed")
    primary_decision = adjudication.get("primary_decision")
    sensitivity_decision = adjudication.get("adjudicated_sensitivity_decision")
    if (
        type(changed) is not bool
        or primary_decision != primary.get("decision")
        or changed != (primary_decision != sensitivity_decision)
    ):
        raise ValueError("adjudication decision relationship is inconsistent")
    if changed and outcome != "run_followup_experiment":
        raise ValueError("a decision-changing sensitivity requires a follow-up experiment")

    if outcome in {"keep_fixed_retrieval", "run_followup_experiment"} and chosen:
        raise ValueError(f"{outcome} cannot select adaptive challenge types")
    if outcome == "bounded_recovery":
        if not chosen or set(chosen) >= set(known_types):
            raise ValueError("bounded recovery requires a nonempty proper type subset")
        if not set(chosen) <= primary_selected:
            raise ValueError("bounded recovery exceeds the primary W10 selection")
    if outcome == "full_bounded_policy":
        if set(chosen) != set(known_types) or set(chosen) != primary_selected:
            raise ValueError("full policy requires every type to pass the primary gates")

    return {
        "schema_version": "enterprise-evaluation/w10-policy-selection/1",
        "complete": True,
        "outcome": outcome,
        "w11_measurement_authorized": outcome != "run_followup_experiment",
        "selected_challenge_types": chosen,
        "known_challenge_types": known_types,
        "rationale": rationale.strip(),
        "reversal_condition": reversal_condition.strip(),
        "adr": "ADR-0081",
        "inputs": input_digests,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--primary-summary", type=Path, required=True)
    parser.add_argument("--adjudication-analysis", type=Path, required=True)
    parser.add_argument("--accounting", type=Path, required=True)
    parser.add_argument("--challenge-cases", type=Path, required=True)
    parser.add_argument("--outcome", choices=OUTCOMES, required=True)
    parser.add_argument("--selected-challenge-type", action="append", default=[])
    parser.add_argument("--rationale", required=True)
    parser.add_argument("--reversal-condition", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    paths = {
        "primary_summary": args.primary_summary,
        "adjudication_analysis": args.adjudication_analysis,
        "accounting": args.accounting,
        "challenge_cases": args.challenge_cases,
    }
    result = build_selection(
        json.loads(args.primary_summary.read_text()),
        json.loads(args.adjudication_analysis.read_text()),
        json.loads(args.accounting.read_text()),
        json.loads(args.challenge_cases.read_text()),
        outcome=args.outcome,
        selected_types=args.selected_challenge_type,
        rationale=args.rationale,
        reversal_condition=args.reversal_condition,
        input_digests={name: digest(path) for name, path in paths.items()},
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({key: result[key] for key in ("outcome", "w11_measurement_authorized", "selected_challenge_types")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
