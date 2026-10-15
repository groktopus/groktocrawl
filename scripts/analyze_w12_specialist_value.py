#!/usr/bin/env python3
"""Apply the preregistered W12.5 specialist-value gate."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Any


def coverage(record: dict[str, Any], arm: str) -> float:
    artifact = record[f"{arm}_artifact"]
    required = set(record["control_artifact"]["covered_obligation_ids"])
    required.update(record["treatment_artifact"]["covered_obligation_ids"])
    # The complete expected set is represented by the treatment on separable cases.
    if not required:
        return 1.0
    return len(set(artifact["covered_obligation_ids"]) & required) / len(required)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    records = [
        json.loads(path.read_bytes())
        for path in sorted((args.run_dir / "public/reviews").glob("*.json"))
    ]
    failed = [item for item in records if item["status"] != "completed"]
    complete = [item for item in records if item["status"] == "completed"]
    groups: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for item in complete:
        groups[item["repetition"]].append(item)
    repetitions = []
    passed_count = 0
    for repetition in sorted(groups):
        items = groups[repetition]
        separable = [item for item in items if item["separable"]]
        simple = [item for item in items if item["stratum"] == "simple"]
        coverage_delta = mean(
            coverage(item, "treatment") - coverage(item, "control")
            for item in separable
        )
        usefulness_delta = mean(
            item["scores"]["treatment"]["usefulness"]
            - item["scores"]["control"]["usefulness"]
            for item in separable
        )
        simple_delta = mean(
            item["scores"]["treatment"]["usefulness"]
            - item["scores"]["control"]["usefulness"]
            for item in simple
        )
        contradiction_loss = sum(
            item["stratum"] == "contradiction"
            and not item["treatment_artifact"]["conflicting_obligation_ids"]
            for item in items
        )
        cost_ratio = sum(item["treatment_calls"] for item in items) / sum(
            item["control_calls"] for item in items
        )
        gate = {
            "coverage_delta_at_least_10_points": coverage_delta >= 0.10,
            "usefulness_delta_at_least_10_points": usefulness_delta >= 10,
            "no_contradiction_loss": contradiction_loss == 0,
            "simple_noninferiority_within_2_points": simple_delta >= -2,
            "cost_within_50_percent": cost_ratio <= 1.5,
            "no_hard_failures": not failed,
        }
        passed = all(gate.values())
        passed_count += passed
        repetitions.append(
            {
                "repetition": repetition,
                "coverage_delta": coverage_delta,
                "usefulness_delta": usefulness_delta,
                "simple_usefulness_delta": simple_delta,
                "contradiction_loss": contradiction_loss,
                "cost_ratio": cost_ratio,
                "gate": gate,
                "passed": passed,
            }
        )
    decision = (
        "adopt_separable_specialist_pattern"
        if passed_count >= 2
        else "retain_generalist"
    )
    output = {
        "schema_version": "specialist-value-analysis/1",
        "decision": decision,
        "completed_reviews": len(complete),
        "failed_reviews": len(failed),
        "passing_repetitions": passed_count,
        "required_passing_repetitions": 2,
        "repetitions": repetitions,
        "external_validity": "Synthetic evidence and one reviewer route support a bounded task-class decision, not generic fan-out.",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    return int(bool(failed))


if __name__ == "__main__":
    raise SystemExit(main())
