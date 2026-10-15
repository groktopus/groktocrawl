#!/usr/bin/env python3
"""Apply the preregistered W12.4 decision gate."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Any


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    records = json.loads(args.run.read_bytes())["records"]
    groups: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        groups[(record["policy"], record["repetition"])].append(record)

    repetitions: list[dict[str, Any]] = []
    passes = 0
    for repetition in sorted({record["repetition"] for record in records}):
        rows: dict[str, dict[str, float]] = {}
        for policy in ("fixed", "w10_diagnostic", "obligation"):
            items = groups[(policy, repetition)]
            challenge = [item for item in items if item["stratum"] != "easy_stop"]
            rows[policy] = {
                "challenge_weighted_closure": mean(
                    item["scores"]["weighted_closure"] for item in challenge
                ),
                "admitted_precision": mean(
                    item["scores"]["admitted_precision"] for item in items
                ),
                "unsupported_high_importance": sum(
                    item["scores"]["unsupported_high_importance"] for item in items
                ),
                "queries": sum(item["scores"]["queries"] for item in items),
                "post_gain_queries": sum(
                    item["scores"]["post_gain_queries"] for item in items
                ),
            }
        treatment = rows["obligation"]
        control = rows["fixed"]
        post_gain_rate = treatment["post_gain_queries"] / treatment["queries"]
        gate = {
            "closure_delta_at_least_10_points": treatment["challenge_weighted_closure"]
            - control["challenge_weighted_closure"]
            >= 0.10,
            "no_extra_unsupported_high_importance": treatment[
                "unsupported_high_importance"
            ]
            <= control["unsupported_high_importance"],
            "precision_within_5_points": treatment["admitted_precision"]
            >= control["admitted_precision"] - 0.05,
            "post_gain_queries_at_most_10_percent": post_gain_rate <= 0.10,
        }
        passed = all(gate.values())
        passes += passed
        repetitions.append(
            {
                "repetition": repetition,
                "policies": rows,
                "treatment_post_gain_rate": post_gain_rate,
                "gate": gate,
                "passed": passed,
            }
        )
    decision = (
        "adopt_experimental_obligation_control" if passes >= 2 else "retain_fixed"
    )
    output = {
        "schema_version": "obligation-replay-analysis/1",
        "decision": decision,
        "passing_repetitions": passes,
        "required_passing_repetitions": 2,
        "repetitions": repetitions,
        "external_validity": (
            "Frozen synthetic replay establishes deterministic policy behavior only; "
            "live retrieval prevalence and effect size remain unproven."
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
