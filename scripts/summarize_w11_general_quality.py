#!/usr/bin/env python3
"""Analyze paired W11 research quality with case-level uncertainty."""

from __future__ import annotations

import argparse
import json
import random
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

ARMS = {"flat_http", "recorded_continuation"}
CLOSURE_MARGIN = -0.02
PRECISION_MARGIN = -0.05


def _counts(records: list[dict[str, Any]]) -> dict[str, float | int | None]:
    closed = sum(record["metrics"]["closed_weight"] for record in records)
    total = sum(record["metrics"]["total_weight"] for record in records)
    candidates = [candidate for record in records for candidate in record["candidates"]]
    admitted = [candidate for candidate in candidates if candidate.get("admitted")]
    useful = [
        candidate
        for candidate in admitted
        if candidate.get("operational_assessment", {}).get("supports_or_challenges")
    ]
    return {
        "trials": len(records),
        "weighted_closure": closed / total if total else None,
        "admitted": len(admitted),
        "useful": len(useful),
        "precision": len(useful) / len(admitted) if admitted else None,
    }


def _delta(left: float | None, right: float | None) -> float | None:
    return right - left if left is not None and right is not None else None


def _percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * quantile)))
    return ordered[index]


def _bootstrap(
    by_case: dict[str, dict[str, list[dict[str, Any]]]], *, seed: int, samples: int
) -> dict[str, Any]:
    identities = sorted(by_case)
    if not identities:
        return {"samples": 0, "closure_delta_95ci": None, "precision_delta_95ci": None}
    rng = random.Random(seed)
    closure: list[float] = []
    precision: list[float] = []
    for _ in range(samples):
        chosen = [rng.choice(identities) for _ in identities]
        arms = {
            arm: [record for identity in chosen for record in by_case[identity][arm]]
            for arm in ARMS
        }
        flat, research = _counts(arms["flat_http"]), _counts(arms["recorded_continuation"])
        closure_delta = _delta(flat["weighted_closure"], research["weighted_closure"])
        precision_delta = _delta(flat["precision"], research["precision"])
        if closure_delta is not None:
            closure.append(closure_delta)
        if precision_delta is not None:
            precision.append(precision_delta)
    return {
        "samples": samples,
        "unit": "case",
        "closure_delta_95ci": (
            [_percentile(closure, 0.025), _percentile(closure, 0.975)] if closure else None
        ),
        "precision_delta_95ci": (
            [_percentile(precision, 0.025), _percentile(precision, 0.975)] if precision else None
        ),
    }


def summarize(
    records: list[dict[str, Any]], *, expected_pairs: int, seed: int = 11052026, samples: int = 10_000
) -> dict[str, Any]:
    failures = [record for record in records if record.get("status") != "completed"]
    grouped: dict[tuple[str, int], dict[str, dict[str, Any]]] = defaultdict(dict)
    by_case: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(
        lambda: {arm: [] for arm in ARMS}
    )
    for record in records:
        if record.get("status") != "completed":
            continue
        key = (str(record["case_id"]), int(record["repetition"]))
        arm = str(record["arm"])
        if arm in grouped[key]:
            raise ValueError(f"duplicate grade for {key} {arm}")
        grouped[key][arm] = record
        by_case[key[0]][arm].append(record)
    complete_pairs = [arms for arms in grouped.values() if set(arms) == ARMS]
    incomplete = [
        {"case_id": key[0], "repetition": key[1], "arms": sorted(arms)}
        for key, arms in grouped.items()
        if set(arms) != ARMS
    ]
    arm_records = {
        arm: [record for pair in complete_pairs for name, record in pair.items() if name == arm]
        for arm in ARMS
    }
    flat = _counts(arm_records["flat_http"])
    research = _counts(arm_records["recorded_continuation"])
    closure_delta = _delta(flat["weighted_closure"], research["weighted_closure"])
    precision_delta = _delta(flat["precision"], research["precision"])
    complete_by_case: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(
        lambda: {arm: [] for arm in ARMS}
    )
    for pair in complete_pairs:
        for arm, record in pair.items():
            complete_by_case[str(record["case_id"])][arm].append(record)
    uncertainty = _bootstrap(complete_by_case, seed=seed, samples=samples)
    closure_ci = uncertainty["closure_delta_95ci"]
    precision_ci = uncertainty["precision_delta_95ci"]
    complete = len(complete_pairs) == expected_pairs and not failures and not incomplete
    gates = {
        "complete_paired_evidence": complete,
        "closure_noninferior_2pp": bool(closure_ci) and closure_ci[0] >= CLOSURE_MARGIN,
        "precision_noninferior_5pp": bool(precision_ci) and precision_ci[0] >= PRECISION_MARGIN,
    }
    return {
        "schema_version": "enterprise-evaluation/w11-general-quality-summary/1",
        "complete": complete,
        "expected_pairs": expected_pairs,
        "observed_pairs": len(complete_pairs),
        "failure_count": len(failures),
        "incomplete_pairs": incomplete,
        "arms": {"flat_http": flat, "recorded_continuation": research},
        "paired_effects": {
            "weighted_closure_delta": closure_delta,
            "precision_delta": precision_delta,
            "case_mean_closure_deltas": [
                statistics.fmean(
                    pair["recorded_continuation"]["metrics"]["weighted_closure"]
                    - pair["flat_http"]["metrics"]["weighted_closure"]
                    for pair in complete_pairs
                    if pair["flat_http"]["case_id"] == case_id
                )
                for case_id in sorted(complete_by_case)
            ],
        },
        "uncertainty": uncertainty,
        "gates": gates,
        "quality_gate_passed": all(gates.values()),
        "decision_boundary": (
            "Passing quality gates establishes noninferiority only. Adoption also requires "
            "the W11 compatibility, provenance, recovery, and operator gates."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("records", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--expected-pairs", type=int, required=True)
    parser.add_argument("--seed", type=int, default=11052026)
    parser.add_argument("--samples", type=int, default=10_000)
    args = parser.parse_args()
    records = [json.loads(path.read_text()) for path in sorted(args.records.glob("*.json"))]
    result = summarize(
        records, expected_pairs=args.expected_pairs, seed=args.seed, samples=args.samples
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({key: result[key] for key in ("complete", "observed_pairs", "quality_gate_passed")}, indent=2))
    return 0 if result["quality_gate_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
