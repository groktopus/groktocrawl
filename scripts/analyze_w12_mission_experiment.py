#!/usr/bin/env python3
"""Compute the frozen W12.1 decision gates from validated run evidence."""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agent-svc"))

from agent.experimental.mission_experiment import (
    sealed_candidate_id,
)
from agent.experimental.research_mission import load_mission_experiment_corpus

TARGET_STRATA = {"ambiguous", "compound"}


def load(path: Path) -> Any:
    return json.loads(path.read_bytes())


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(0, math.ceil(fraction * len(ordered)) - 1)
    return ordered[rank]


def relative_reduction(control: float, treatment: float) -> float | None:
    if control == 0:
        return 0.0 if treatment == 0 else -math.inf
    return (control - treatment) / control


def usage_cost(receipt: dict[str, Any]) -> float:
    usage = receipt.get("usage") or {}
    value = usage.get("cost")
    return float(value) if isinstance(value, int | float) else 0.0


def status_credit(status: str, *, partial_as_open: bool = False) -> float:
    if status == "closed":
        return 1.0
    if status == "partial" and not partial_as_open:
        return 0.5
    return 0.0


def coverage_for(
    grade: dict[str, Any],
    weights: dict[str, int],
    *,
    equal_weights: bool = False,
    partial_as_open: bool = False,
) -> tuple[float, float]:
    numerator = sum(
        status_credit(value["status"], partial_as_open=partial_as_open)
        * (1 if equal_weights else weights[key])
        for key, value in grade["obligation_grades"].items()
    )
    denominator = float(len(weights) if equal_weights else sum(weights.values()))
    return numerator, denominator


def difficult_repetition_gates(
    rows: list[dict[str, Any]],
    *,
    coverage_key: str = "weighted_coverage",
    target_strata: set[str] = TARGET_STRATA,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for repetition in (1, 2, 3):
        target = [
            row
            for row in rows
            if row["repetition"] == repetition and row["stratum"] in target_strata
        ]
        arms: dict[str, dict[str, float]] = {}
        for arm in ("control", "treatment"):
            arm_rows = [row for row in target if row["arm"] == arm]
            arms[arm] = {
                "coverage": (
                    statistics.mean(row[coverage_key] for row in arm_rows)
                    if arm_rows
                    else None
                ),
                "scope_violations": float(
                    sum(row["scope_violations"] for row in arm_rows)
                ),
            }
        gain = (
            arms["treatment"]["coverage"] - arms["control"]["coverage"]
            if arms["treatment"]["coverage"] is not None
            and arms["control"]["coverage"] is not None
            else None
        )
        reduction = relative_reduction(
            arms["control"]["scope_violations"], arms["treatment"]["scope_violations"]
        )
        result.append(
            {
                "repetition": repetition,
                "control": arms["control"],
                "treatment": arms["treatment"],
                "coverage_gain": gain,
                "scope_violation_relative_reduction": reduction,
                "passes": gain is not None
                and gain >= 0.10
                and reduction is not None
                and reduction >= 0.30,
            }
        )
    return result


def analyze(run_dir: Path) -> dict[str, Any]:
    experiment_dir = ROOT / "docs/experiments/research-mission"
    source_path = ROOT / "docs/experiments/enterprise-evaluation/corpus.json"
    corpus = load_mission_experiment_corpus(
        experiment_dir / "w12.1-cases.json", source_corpus_path=source_path
    )
    cases = {item.case_id: item for item in corpus.cases}

    grades = {
        path.stem: load(path)["grade"]
        for path in (run_dir / "public/grades").glob("*.json")
        if load(path).get("status") == "completed"
    }
    trial_rows: list[dict[str, Any]] = []
    for path in sorted((run_dir / "public/trials").glob("*.json")):
        trial = load(path)
        if trial.get("status") != "completed":
            continue
        case = cases[trial["case_id"]]
        grade = grades.get(sealed_candidate_id(trial["trial_id"]))
        if grade is None:
            continue
        obligation_weights = {
            item.obligation_id: item.weight
            for item in case.reference_mission.obligations
        }
        covered_weight, total_weight = coverage_for(grade, obligation_weights)
        equal_covered, equal_total = coverage_for(
            grade, obligation_weights, equal_weights=True
        )
        open_covered, open_total = coverage_for(
            grade, obligation_weights, partial_as_open=True
        )
        trial_rows.append(
            {
                "trial_id": trial["trial_id"],
                "candidate_id": sealed_candidate_id(trial["trial_id"]),
                "case_id": case.case_id,
                "stratum": case.stratum,
                "arm": trial["arm"],
                "repetition": trial["repetition"],
                "covered_weight": covered_weight,
                "total_weight": total_weight,
                "weighted_coverage": covered_weight / total_weight,
                "equal_weight_coverage": equal_covered / equal_total,
                "partial_as_open_coverage": open_covered / open_total,
                "scope_violations": len(grade["scope_violations"]),
                "material_scope_violations": sum(
                    item["severity"] == "material" for item in grade["scope_violations"]
                ),
                "decision_usefulness": grade["decision_usefulness"],
                "hard_boundary_failure": grade["hard_boundary_failure"],
                "supported_claim_rate": (
                    grade["supported_material_claims"] / grade["total_material_claims"]
                    if grade["total_material_claims"]
                    else None
                ),
                "latency_ms": trial["receipt"]["latency_ms"],
                "cost": usage_cost(trial["receipt"]),
            }
        )

    by_pair = {
        (row["case_id"], row["repetition"], row["arm"]): row for row in trial_rows
    }
    repetition_gates = difficult_repetition_gates(trial_rows)

    paired_rows = []
    for case in corpus.cases:
        for repetition in (1, 2, 3):
            control = by_pair.get((case.case_id, repetition, "control"))
            treatment = by_pair.get((case.case_id, repetition, "treatment"))
            if control is None or treatment is None:
                continue
            paired_rows.append(
                {
                    "case_id": case.case_id,
                    "stratum": case.stratum,
                    "repetition": repetition,
                    "coverage_change": treatment["weighted_coverage"]
                    - control["weighted_coverage"],
                    "scope_violation_change": treatment["scope_violations"]
                    - control["scope_violations"],
                    "decision_usefulness_change": treatment["decision_usefulness"]
                    - control["decision_usefulness"],
                }
            )

    arm_ops: dict[str, Any] = {}
    for arm in ("control", "treatment"):
        rows = [row for row in trial_rows if row["arm"] == arm]
        arm_ops[arm] = {
            "p95_latency_ms": percentile([row["latency_ms"] for row in rows], 0.95),
            "total_cost": sum(row["cost"] for row in rows),
            "hard_boundary_failures": sum(row["hard_boundary_failure"] for row in rows),
        }
    latency_increase = (
        arm_ops["treatment"]["p95_latency_ms"] / arm_ops["control"]["p95_latency_ms"]
        - 1
    )
    cost_increase = (
        arm_ops["treatment"]["total_cost"] / arm_ops["control"]["total_cost"] - 1
        if arm_ops["control"]["total_cost"]
        else None
    )
    target_gate = sum(row["passes"] for row in repetition_gates) >= 2
    anchor_regression = any(
        row["stratum"] == "straightforward" and row["decision_usefulness_change"] < -2
        for row in paired_rows
    )
    operational_gate = latency_increase <= 0.25 and (
        cost_increase is None or cost_increase <= 0.25
    )
    boundary_gate = arm_ops["treatment"]["hard_boundary_failures"] == 0
    if target_gate and operational_gate and boundary_gate:
        disposition = (
            "adopt_universally" if not anchor_regression else "adopt_selectively"
        )
    else:
        disposition = "reject"

    intake_grades = [
        load(path)["grade"]
        for path in (run_dir / "public/intake-grades").glob("*.json")
        if load(path).get("status") == "completed"
    ]
    intake = {
        "grade_count": len(intake_grades),
        "action_accuracy": statistics.mean(
            int(item["action_correct"]) for item in intake_grades
        ),
        "mean_required_field_recall": statistics.mean(
            item["required_field_recall"] for item in intake_grades
        ),
        "hard_boundary_failures": sum(
            item["hard_boundary_failure"] for item in intake_grades
        ),
        "total_correction_actions": sum(
            len(item["correction_actions"]) for item in intake_grades
        ),
    }
    adjudicated = {
        path.stem: load(path)["grade"]
        for path in (run_dir / "public/adjudications").glob("*.json")
        if load(path).get("status") == "completed"
    }
    adjudicated_rows = []
    for row in trial_rows:
        replacement = adjudicated.get(row["candidate_id"])
        if replacement is None:
            adjudicated_rows.append(row)
            continue
        case = cases[row["case_id"]]
        weights = {
            item.obligation_id: item.weight
            for item in case.reference_mission.obligations
        }
        covered, total = coverage_for(replacement, weights)
        revised = dict(row)
        revised.update(
            weighted_coverage=covered / total,
            scope_violations=len(replacement["scope_violations"]),
            decision_usefulness=replacement["decision_usefulness"],
            hard_boundary_failure=replacement["hard_boundary_failure"],
        )
        adjudicated_rows.append(revised)
    leave_one_out = []
    for stratum in sorted(TARGET_STRATA):
        case_ids = sorted(case.case_id for case in corpus.cases if case.stratum == stratum)
        for case_id in case_ids:
            rows = [
                row
                for row in trial_rows
                if not (row["stratum"] == stratum and row["case_id"] == case_id)
            ]
            leave_one_out.append(
                {
                    "stratum": stratum,
                    "omitted_case_id": case_id,
                    "repetition_gates": difficult_repetition_gates(
                        rows, target_strata={stratum}
                    ),
                }
            )
    intake_trials = [
        load(path)
        for path in (run_dir / "public/intake").glob("*.json")
        if load(path).get("status") == "completed"
    ]
    clarification_ops = {}
    for name, predicate in {
        "clarification": lambda item: item["result"]["action"] == "clarify",
        "without_clarification": lambda item: item["result"]["action"] != "clarify",
    }.items():
        rows = [item for item in intake_trials if predicate(item)]
        clarification_ops[name] = {
            "count": len(rows),
            "p95_latency_ms": percentile(
                [item["receipt"]["latency_ms"] for item in rows], 0.95
            ),
            "total_cost": sum(usage_cost(item["receipt"]) for item in rows),
        }
    failed_trials = [
        load(path)
        for directory in ("trials", "intake", "grades", "intake-grades", "adjudications")
        for path in (run_dir / "public" / directory).glob("*.json")
        if load(path).get("status") == "failed"
    ]
    available = {
        (row["case_id"], row["repetition"], row["arm"]): row for row in trial_rows
    }
    frozen_work = load(experiment_dir / "w12.1-work-order.json")["trials"]
    worst_case_rows = list(trial_rows)
    for item in frozen_work:
        key = (item["case_id"], item["repetition"], item["arm"])
        if key in available:
            continue
        case = cases[item["case_id"]]
        treatment_failure = item["arm"] == "treatment"
        worst_case_rows.append(
            {
                **item,
                "stratum": case.stratum,
                "weighted_coverage": 0.0 if treatment_failure else 1.0,
                "equal_weight_coverage": 0.0 if treatment_failure else 1.0,
                "partial_as_open_coverage": 0.0 if treatment_failure else 1.0,
                "scope_violations": 1 if treatment_failure else 0,
                "decision_usefulness": 0 if treatment_failure else 100,
                "hard_boundary_failure": treatment_failure,
            }
        )
    worst_case_gates = difficult_repetition_gates(worst_case_rows)
    worst_case_target = sum(row["passes"] for row in worst_case_gates) >= 2
    if disposition != "reject" and not worst_case_target:
        disposition = "reject"
    return {
        "schema_version": "research-mission-analysis/1",
        "trial_count": len(trial_rows),
        "paired_case_repetitions": paired_rows,
        "repetition_gates": repetition_gates,
        "operations": {
            **arm_ops,
            "latency_increase": latency_increase,
            "cost_increase": cost_increase,
        },
        "gates": {
            "target_effect": target_gate,
            "anchor_regression": anchor_regression,
            "operational": operational_gate,
            "hard_boundary": boundary_gate,
        },
        "intake": intake,
        "sensitivity": {
            "equal_obligation_weights": difficult_repetition_gates(
                trial_rows, coverage_key="equal_weight_coverage"
            ),
            "partial_grades_counted_open": difficult_repetition_gates(
                trial_rows, coverage_key="partial_as_open_coverage"
            ),
            "leave_one_case_out": leave_one_out,
            "failed_trials": {
                "count": len(failed_trials),
                "missing_case_repetition_gates": repetition_gates,
                "worst_case_repetition_gates": worst_case_gates,
                "worst_case_rule": "missing treatment gets zero coverage, one scope violation, usefulness zero, and a hard-boundary failure; missing control gets full coverage, no scope violation, and usefulness 100",
            },
            "adjudicated_grades": {
                "count": len(adjudicated),
                "repetition_gates": difficult_repetition_gates(adjudicated_rows),
            },
            "clarification_operations": clarification_ops,
        },
        "disposition": disposition,
    }


def render_markdown(analysis: dict[str, Any]) -> str:
    gates = analysis["gates"]
    intake = analysis["intake"]
    lines = [
        "# W12.1 Research Mission outcome",
        "",
        f"Decision: **{analysis['disposition'].replace('_', ' ')}**",
        "",
        "## Frozen gates",
        "",
        f"- Difficult-case practical effect: {'pass' if gates['target_effect'] else 'fail'}",
        f"- Straightforward-case regression: {'present' if gates['anchor_regression'] else 'absent'}",
        f"- Latency and cost: {'pass' if gates['operational'] else 'fail'}",
        f"- Hard boundary: {'pass' if gates['hard_boundary'] else 'fail'}",
        "",
        "## Intake",
        "",
        f"- Action accuracy: {intake['action_accuracy']:.1%}",
        f"- Mean required-field recall: {intake['mean_required_field_recall']:.1f}/100",
        f"- Hard boundary failures: {intake['hard_boundary_failures']}",
        f"- Required correction actions: {intake['total_correction_actions']}",
        "",
        "## Repetition-level difficult-case results",
        "",
        "| Repetition | Coverage gain | Scope-violation reduction | Gate |",
        "|---:|---:|---:|---|",
    ]
    for row in analysis["repetition_gates"]:
        reduction = row["scope_violation_relative_reduction"]
        rendered_reduction = "n/a" if reduction is None else f"{reduction:.1%}"
        lines.append(
            f"| {row['repetition']} | {row['coverage_gain']:.1%} | "
            f"{rendered_reduction} | {'pass' if row['passes'] else 'fail'} |"
        )
    lines.extend(
        [
            "",
            "This report is a mechanical rendering of the retained grades. Case-level ",
            "results, raw receipts, failed attempts, and sensitivity review remain part ",
            "of the evidence packet and must be reviewed before an ADR is accepted.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-markdown", type=Path, required=True)
    args = parser.parse_args()
    result = analyze(args.run_dir)
    args.output_json.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    args.output_markdown.write_text(render_markdown(result))
    print(result["disposition"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
