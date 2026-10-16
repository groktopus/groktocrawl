#!/usr/bin/env python3
"""Analyze W12.2 paired longitudinal results and frozen decision gates."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from statistics import mean

SCORES = (
    "change_accuracy",
    "current_accuracy",
    "historical_preservation",
    "unresolved_accuracy",
    "usefulness",
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    trials = {
        record["trial_id"]: record
        for path in (args.run_dir / "public/trials").glob("*.json")
        if (record := json.loads(path.read_bytes())).get("status") == "completed"
    }
    grades = [
        record
        for path in (args.run_dir / "public/grades").glob("*.json")
        if (record := json.loads(path.read_bytes())).get("status") == "completed"
    ]
    latency_by_arm: dict[str, list[float]] = defaultdict(list)
    tokens_by_arm: dict[str, list[int]] = defaultdict(list)
    for trial in trials.values():
        receipt = trial["receipt"]
        latency_by_arm[trial["arm"]].append(float(receipt["latency_ms"]))
        usage = receipt.get("usage") or {}
        tokens_by_arm[trial["arm"]].append(int(usage.get("total_tokens") or 0))
    paired: dict[tuple[str, int], dict[str, dict]] = defaultdict(dict)
    for record in grades:
        trial = trials[record["trial_id"]]
        paired[(trial["case_id"], trial["repetition"])][trial["arm"]] = record["grade"]
    effects = []
    missing_grade_assignments = []
    repetition_effects: dict[int, list[float]] = defaultdict(list)
    hard_failures = []
    expected_pairs = sorted(
        {(trial["case_id"], trial["repetition"]) for trial in trials.values()}
    )
    for case_id, repetition in expected_pairs:
        arms = paired[(case_id, repetition)]
        for arm in ("control", "treatment"):
            if arm in arms:
                continue
            assigned = 100 if arm == "control" else 0
            arms[arm] = {
                **dict.fromkeys(SCORES, assigned),
                "false_merge": False,
                "stale_current_leak": False,
                "lost_history": False,
                "unsupported_claims": 0,
                "rationale": "Frozen conservative missing-grade assignment.",
            }
            missing_grade_assignments.append(
                {
                    "case_id": case_id,
                    "repetition": repetition,
                    "arm": arm,
                    "assigned_score": assigned,
                }
            )
        control = mean(arms["control"][score] for score in SCORES)
        treatment = mean(arms["treatment"][score] for score in SCORES)
        effect = treatment - control
        repetition_effects[repetition].append(effect)
        effects.append(
            {
                "case_id": case_id,
                "repetition": repetition,
                "control_score": control,
                "treatment_score": treatment,
                "effect_points": effect,
            }
        )
        grade = arms["treatment"]
        if grade["false_merge"] or grade["stale_current_leak"] or grade["lost_history"]:
            hard_failures.append({"case_id": case_id, "repetition": repetition, "grade": grade})
    by_repetition = {
        str(rep): round(mean(values), 3) for rep, values in repetition_effects.items()
    }
    repetitions_over_ten = sum(value >= 10 for value in by_repetition.values())
    mean_latency = {
        arm: round(mean(values), 3) for arm, values in latency_by_arm.items()
    }
    latency_reduction = (
        (mean_latency["control"] - mean_latency["treatment"])
        / mean_latency["control"]
        * 100
        if mean_latency.get("control")
        else 0.0
    )
    mean_tokens = {
        arm: round(mean(values), 3) for arm, values in tokens_by_arm.items()
    }
    no_change_effects = [
        item["effect_points"] for item in effects if item["case_id"] == "stable-anchor"
    ]
    no_change_noninferior = bool(no_change_effects) and min(no_change_effects) >= -2
    run_summary = json.loads((args.run_dir / "public/run-summary.json").read_bytes())
    trial_failure_rate = run_summary["failed"] / max(
        1, run_summary["completed"] + run_summary["failed"]
    )
    grade_summary = json.loads((args.run_dir / "public/grade-summary.json").read_bytes())
    failure_rate = grade_summary["failed"] / max(1, grade_summary["completed"] + grade_summary["failed"])
    operational_pass = trial_failure_rate < 0.10 and failure_rate < 0.10
    efficiency_pass = latency_reduction >= 25
    decision = (
        "adopt_narrow_thread_contract"
        if (
            repetitions_over_ten >= 2
            and not hard_failures
            and operational_pass
            and efficiency_pass
            and no_change_noninferior
        )
        else "reject_or_revise"
    )
    result = {
        "schema_version": "research-thread-analysis/1",
        "decision": decision,
        "paired_count": len(effects),
        "complete_observed_grade_count": len(grades),
        "missing_grade_assignments": missing_grade_assignments,
        "mean_effect_points": round(mean(item["effect_points"] for item in effects), 3) if effects else None,
        "effect_points_by_repetition": by_repetition,
        "repetitions_at_or_above_10_points": repetitions_over_ten,
        "hard_failure_count": len(hard_failures),
        "hard_failures": hard_failures,
        "mean_latency_ms_by_arm": mean_latency,
        "treatment_latency_reduction_percent": round(latency_reduction, 3),
        "mean_total_tokens_by_arm": mean_tokens,
        "efficiency_gate_pass": efficiency_pass,
        "no_change_effect_points": no_change_effects,
        "no_change_noninferiority_pass": no_change_noninferior,
        "trial_failure_rate": round(trial_failure_rate, 6),
        "grade_failure_rate": round(failure_rate, 6),
        "operational_gate_pass": operational_pass,
        "effects": effects,
        "limitations": [
            "Model grading is blinded but not human adjudication.",
            "Latency covers model completion, not live retrieval, because the frozen source pack is pre-supplied.",
            "The synthetic corpus establishes causal behavior, not production prevalence.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
