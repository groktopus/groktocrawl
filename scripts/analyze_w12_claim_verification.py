#!/usr/bin/env python3
"""Analyze frozen W12.3 control and independent-verifier decisions."""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agent-svc"))

from agent.experimental.claim_verification_experiment import (
    load_claim_verification_corpus,
)


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)]


def support_probability(record: dict) -> float:
    verification = record["verification"]
    confidence = verification["confidence"] / 100
    return confidence if verification["verdict"] == "supported" else 1 - confidence


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    corpus = load_claim_verification_corpus(str(args.corpus))
    cases = {case.packet.case_id: case for case in corpus.cases}
    records = {
        data["trial_id"]: data
        for path in (args.run_dir / "public/verifications").glob("*.json")
        if (data := json.loads(path.read_bytes())).get("status") == "completed"
    }
    work = json.loads((args.run_dir / "work-order.json").read_bytes())["items"]
    summary = json.loads((args.run_dir / "public/summary.json").read_bytes())
    by_repetition: dict[int, list[dict]] = defaultdict(list)
    rows = []
    hard_failures = []
    latencies: list[float] = []
    high_risk_latencies: list[float] = []
    calls: list[int] = []
    for item in work:
        case = cases[item["case_id"]]
        record = records.get(item["trial_id"])
        expected = case.reference.publish
        control = case.control_publish
        if record is None:
            treatment = not expected
            probability = 1.0 if treatment else 0.0
            verdict = "missing"
            evidence_aligned = False
            call_count = 2
        else:
            verification = record["verification"]
            treatment = verification["publish_recommendation"]
            probability = support_probability(record)
            verdict = verification["verdict"]
            cited = set(verification["evidence_span_ids"]) | set(
                verification["contradiction_span_ids"]
            )
            evidence_aligned = set(case.reference.required_span_ids) <= cited
            receipt = record["receipt"]
            latencies.append(float(receipt["latency_ms"]))
            if case.packet.risk == "high":
                high_risk_latencies.append(float(receipt["latency_ms"]))
            call_count = int(receipt["attempt"]) + sum(
                int(attempt["receipt"]["attempt"])
                for attempt in record["prior_invalid_completions"]
            )
        calls.append(call_count)
        hostile_failure = case.stratum == "hostile_source" and treatment
        if hostile_failure:
            hard_failures.append(item["trial_id"])
        row = {
            **item,
            "risk": case.packet.risk,
            "stratum": case.stratum,
            "expected_publish": expected,
            "control_publish": control,
            "treatment_publish": treatment,
            "verdict": verdict,
            "critical": case.reference.critical_false_accept,
            "control_false_accept": control and not expected,
            "treatment_false_accept": treatment and not expected,
            "control_false_reject": not control and expected,
            "treatment_false_reject": not treatment and expected,
            "missed_contradiction_control": (
                case.reference.verdict == "contradicted" and control
            ),
            "missed_contradiction_treatment": (
                case.reference.verdict == "contradicted" and treatment
            ),
            "inappropriate_abstention": expected and verdict != "supported",
            "evidence_aligned": evidence_aligned,
            "control_brier": (float(control) - float(expected)) ** 2,
            "treatment_brier": (probability - float(expected)) ** 2,
            "calls": call_count,
        }
        rows.append(row)
        by_repetition[item["repetition"]].append(row)

    repetition_results = {}
    selective_passes = 0
    for repetition, items in sorted(by_repetition.items()):
        high = [item for item in items if item["risk"] == "high"]
        control_critical = sum(
            item["control_false_accept"] and item["critical"] for item in high
        )
        treatment_critical = sum(
            item["treatment_false_accept"] and item["critical"] for item in high
        )
        reduction = (
            (control_critical - treatment_critical) / control_critical
            if control_critical
            else 0.0
        )
        false_reject_increase = (
            sum(item["treatment_false_reject"] for item in items)
            - sum(item["control_false_reject"] for item in items)
        ) / len(items)
        abstention_rate = sum(item["inappropriate_abstention"] for item in items) / len(
            items
        )
        missed_control = sum(item["missed_contradiction_control"] for item in items)
        missed_treatment = sum(item["missed_contradiction_treatment"] for item in items)
        brier_control = mean(item["control_brier"] for item in items)
        brier_treatment = mean(item["treatment_brier"] for item in items)
        passed = (
            reduction >= 0.30
            and missed_treatment < missed_control
            and false_reject_increase <= 0.10
            and abstention_rate <= 0.10
            and brier_treatment < brier_control
        )
        selective_passes += passed
        repetition_results[str(repetition)] = {
            "critical_false_accept_reduction": round(reduction, 6),
            "missed_contradictions_control": missed_control,
            "missed_contradictions_treatment": missed_treatment,
            "false_reject_increase": round(false_reject_increase, 6),
            "inappropriate_abstention_rate": round(abstention_rate, 6),
            "control_brier": round(brier_control, 6),
            "treatment_brier": round(brier_treatment, 6),
            "quality_gate_pass": passed,
        }
    operational_pass = summary["failed"] / len(work) < 0.10
    latency_pass = (
        bool(high_risk_latencies) and percentile(high_risk_latencies, 0.95) <= 120_000
    )
    call_pass = mean(calls) <= 1.2
    selective = (
        selective_passes >= 2
        and operational_pass
        and latency_pass
        and call_pass
        and not hard_failures
    )
    low = [item for item in rows if item["risk"] == "low"]
    low_control = sum(item["control_false_accept"] for item in low)
    low_treatment = sum(item["treatment_false_accept"] for item in low)
    low_reduction = (low_control - low_treatment) / low_control if low_control else 0.0
    default = (
        selective
        and low_reduction >= 0.20
        and not any(item["treatment_false_reject"] for item in low)
    )
    result = {
        "schema_version": "claim-verification-analysis/1",
        "decision": (
            "adopt_default_verification"
            if default
            else "adopt_selective_high_risk_verification"
            if selective
            else "reject_or_revise"
        ),
        "allocated": len(work),
        "completed": len(records),
        "terminal_failures": summary["failed"],
        "operational_gate_pass": operational_pass,
        "repetitions": repetition_results,
        "quality_repetitions_passed": selective_passes,
        "hard_failures": hard_failures,
        "mean_latency_ms": round(mean(latencies), 3) if latencies else None,
        "high_risk_p95_latency_ms": (
            round(percentile(high_risk_latencies, 0.95), 3)
            if high_risk_latencies
            else None
        ),
        "latency_gate_pass": latency_pass,
        "mean_incremental_calls": round(mean(calls), 3),
        "call_gate_pass": call_pass,
        "low_risk_false_accept_reduction": round(low_reduction, 6),
        "rows": rows,
        "limitations": [
            "The corpus is synthetic and establishes causal behavior, not production prevalence.",
            "Reference judgments are independently authored but not human adjudication.",
            "The General alias can change upstream model mapping between separately frozen runs.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
