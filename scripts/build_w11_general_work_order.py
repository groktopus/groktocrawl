#!/usr/bin/env python3
"""Build the W11 A0/A1 work order from the final W10 policy selection."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
from pathlib import Path
from typing import Any

ARMS = ("flat_http", "recorded_continuation")
W10_POLICIES = ["fixed", "unconstrained", "gap", "gated", "full"]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def frozen_result_limit(
    manifest: dict[str, Any],
    *,
    cases_sha256: str,
    case_count: int,
    repetitions: int,
) -> int:
    schema = manifest.get("schema_version")
    if schema == "enterprise-evaluation/w10-policy-run/1":
        expected = case_count * len(W10_POLICIES) * repetitions
        expected_policies = W10_POLICIES
    elif schema == "enterprise-evaluation/w11-fixed-control-handoff/1":
        expected = case_count * repetitions
        expected_policies = ["fixed"]
        if manifest.get("query_source") != "committed_frozen_case_query":
            raise ValueError("W11 handoff has an unsupported query source")
    else:
        raise ValueError("unsupported W10 run manifest schema")
    if (
        manifest.get("cases_sha256") != cases_sha256
        or manifest.get("records") != expected
        or manifest.get("completed") != expected
        or manifest.get("failed") != 0
        or manifest.get("failed_attempts") != 0
        or manifest.get("repetitions") != repetitions
        or manifest.get("policies") != expected_policies
    ):
        raise ValueError("W10 run manifest does not prove a complete matching case run")
    value = manifest.get("result_limit")
    if type(value) is not int or not 1 <= value <= 20:
        raise ValueError("W10 run manifest has an invalid result limit")
    return value


def build(
    selection: dict[str, Any],
    cases: dict[str, Any],
    *,
    seed: int,
    repetitions: int,
    result_limit: int,
) -> dict[str, Any]:
    if (
        selection.get("schema_version")
        != "enterprise-evaluation/w10-policy-selection/1"
    ):
        raise ValueError("unsupported W10 policy-selection schema")
    if selection.get("complete") is not True:
        raise ValueError("W10 selection must be complete before W11 is built")
    if selection.get("w11_measurement_authorized") is not True:
        raise ValueError("W10 did not authorize W11 measurement")
    if repetitions != 3:
        raise ValueError("the frozen W11 protocol requires exactly three repetitions")
    if type(result_limit) is not int or not 1 <= result_limit <= 20:
        raise ValueError("W10 result limit must be an integer from 1 to 20")
    selected = set(selection.get("selected_challenge_types", []))
    known_types = {str(case["challenge_type"]) for case in cases.get("cases", [])}
    if set(selection.get("known_challenge_types", [])) != known_types:
        raise ValueError("W10 selection does not match the W11 challenge types")
    if not selected <= known_types:
        raise ValueError("W10 selected an unknown challenge type")

    policy_by_type = {
        challenge_type: "full" if challenge_type in selected else "fixed"
        for challenge_type in sorted(known_types)
    }
    entries: list[dict[str, Any]] = []
    case_rows = sorted(cases["cases"], key=lambda item: str(item["case_id"]))
    for repetition in range(repetitions):
        ordered = list(case_rows)
        random.Random(seed + repetition).shuffle(ordered)
        for position, case in enumerate(ordered):
            first = (position + repetition) % 2
            arm_order = ARMS[first:] + ARMS[:first]
            for arm in arm_order:
                entries.append(
                    {
                        "position": len(entries) + 1,
                        "case_id": case["case_id"],
                        "challenge_type": case["challenge_type"],
                        "repetition": repetition,
                        "arm": arm,
                        "control_policy": policy_by_type[case["challenge_type"]],
                    }
                )
    return {
        "schema_version": "enterprise-evaluation/w11-general-work-order/1",
        "seed": seed,
        "repetitions": repetitions,
        "result_limit": result_limit,
        "arms": list(ARMS),
        "policy_by_challenge_type": policy_by_type,
        "entries": entries,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--w10-selection", type=Path, required=True)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--w10-run-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=11052026)
    parser.add_argument("--repetitions", type=int, default=3)
    args = parser.parse_args()
    selection = json.loads(args.w10_selection.read_text())
    cases = json.loads(args.cases.read_text())
    if (selection.get("inputs") or {}).get("challenge_cases") != digest(args.cases):
        raise ValueError("W10 selection is not bound to the supplied challenge cases")
    run_manifest = json.loads(args.w10_run_manifest.read_text())
    result_limit = frozen_result_limit(
        run_manifest,
        cases_sha256=digest(args.cases),
        case_count=len(cases.get("cases", [])),
        repetitions=args.repetitions,
    )
    result = build(
        selection,
        cases,
        seed=args.seed,
        repetitions=args.repetitions,
        result_limit=result_limit,
    )
    result["inputs"] = {
        "w10_selection_sha256": digest(args.w10_selection),
        "cases_sha256": digest(args.cases),
        "w10_run_manifest_sha256": digest(args.w10_run_manifest),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(
        json.dumps(
            {
                "entries": len(result["entries"]),
                "policy_by_challenge_type": result["policy_by_challenge_type"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
