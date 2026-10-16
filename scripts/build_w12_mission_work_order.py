#!/usr/bin/env python3
"""Build the deterministic counterbalanced W12.1 downstream work order."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agent-svc"))

from agent.experimental.mission_experiment import (
    build_grade_work_order,
    build_intake_grade_work_order,
    build_intake_work_order,
    build_work_order,
    grade_work_order_record,
    intake_grade_work_order_record,
    intake_work_order_record,
    work_order_record,
)
from agent.experimental.research_mission import load_mission_experiment_corpus


def main() -> int:
    experiment_dir = ROOT / "docs/experiments/research-mission"
    corpus = load_mission_experiment_corpus(
        experiment_dir / "w12.1-cases.json",
        source_corpus_path=ROOT / "docs/experiments/enterprise-evaluation/corpus.json",
    )
    seed = 20260919
    order = build_work_order(corpus.cases, repetitions=3, seed=seed)
    output = experiment_dir / "w12.1-work-order.json"
    output.write_text(
        json.dumps(work_order_record(order, seed=seed), indent=2, sort_keys=True) + "\n"
    )
    print(output.relative_to(ROOT))
    intake_order = build_intake_work_order(corpus.cases, repetitions=3, seed=seed)
    intake_output = experiment_dir / "w12.1-intake-work-order.json"
    intake_output.write_text(
        json.dumps(
            intake_work_order_record(intake_order, seed=seed),
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    print(intake_output.relative_to(ROOT))
    grade_order = build_grade_work_order(order, seed=seed)
    grade_output = experiment_dir / "w12.1-grade-work-order.json"
    grade_output.write_text(
        json.dumps(
            grade_work_order_record(grade_order, seed=seed), indent=2, sort_keys=True
        )
        + "\n"
    )
    print(grade_output.relative_to(ROOT))
    intake_grade_order = build_intake_grade_work_order(intake_order, seed=seed)
    intake_grade_output = experiment_dir / "w12.1-intake-grade-work-order.json"
    intake_grade_output.write_text(
        json.dumps(
            intake_grade_work_order_record(intake_grade_order, seed=seed),
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    print(intake_grade_output.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
