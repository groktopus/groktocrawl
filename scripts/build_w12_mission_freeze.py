#!/usr/bin/env python3
"""Freeze W12.1 inputs and executable tooling before any measured run."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "docs/experiments/research-mission/w12.1-freeze-v11.json"

PINNED_FILES = (
    "docs/experiments/enterprise-evaluation/corpus.json",
    "docs/experiments/research-mission/w12.1-analysis-plan.md",
    "docs/experiments/research-mission/w12.1-cases.json",
    "docs/experiments/research-mission/w12.1-experiment-brief.md",
    "docs/experiments/research-mission/w12.1-grade-work-order.json",
    "docs/experiments/research-mission/w12.1-intake-grade-work-order.json",
    "docs/experiments/research-mission/w12.1-intake-work-order.json",
    "docs/experiments/research-mission/w12.1-protocol.md",
    "docs/experiments/research-mission/w12.1-work-order.json",
    "agent-svc/agent/experimental/mission_experiment.py",
    "agent-svc/agent/experimental/research_mission.py",
    "scripts/grade_w12_mission_experiment.py",
    "scripts/adjudicate_w12_mission_experiment.py",
    "scripts/grade_w12_mission_intake.py",
    "scripts/run_w12_mission_experiment.py",
    "scripts/run_w12_mission_intake.py",
    "scripts/validate_w12_mission_run.py",
    "scripts/analyze_w12_mission_experiment.py",
    "uv.lock",
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    payload = {
        "schema_version": "research-mission-experiment-freeze/11",
        "status": "frozen_before_measurement",
        "supersedes": "w12.1-freeze-v10.json",
        "predecessor_disposition": "excluded_after_regrade_schema_guardrail",
        "inherited_primary_evidence": {
            "source": "w12.1-run-v8-a2a743b",
            "file_count": 436,
            "byte_count": 13790342,
            "canonical_manifest_sha256": "46dadbc8d837bcb863257accbe63dc09d7b2f16c400fd00aa58908311e1f518b",
            "copy_policy": "exact read-only copy before any v9 model call",
        },
        "source_revision": revision,
        "domain": "agentic engineering software factory in the enterprise",
        "model_route": "general",
        "reasoning_effort": {
            "downstream": "low",
            "intake_and_evaluator_lanes": "minimal",
        },
        "maximum_completion_tokens": {
            "downstream": 10000,
            "intake_and_evaluator_lanes": 20000,
        },
        "model_identity_policy": "record provider-returned identity on every call",
        "endpoint_origin": "https://gpuslut.brandyapple.com/",
        "concurrency": 5,
        "maximum_authorized_concurrency": 10,
        "transport_attempt_limit": 3,
        "seed": 20260919,
        "case_count": 12,
        "downstream_trial_count": 72,
        "intake_trial_count": 36,
        "downstream_grade_count": 72,
        "intake_grade_count": 36,
        "maximum_adjudication_count": 72,
        "measured_call_ceiling": 288,
        "required_preflight": "no model preflight; reproduce the inherited v8 primary-tree digest before any v9 evaluator call",
        "bounded_corrections": [
            "exact common output field names included in both arm prompts",
            "exact provider envelope retained before content validation",
            "low reasoning effort and 10000 completion-token ceiling",
            "complete six declared sensitivity outputs",
            "independent automated regrade lane with replay validation",
            "terminal failures admitted below the 10 percent lane guardrail",
            "frozen conservative assignment for missing candidates",
            "provider-enforced strict JSON Schema for intake results",
            "minimal reasoning effort for intake normalization only",
            "20000-token completion ceiling for intake normalization only",
            "minimal reasoning and 20000-token ceiling for evaluator calls",
            "local tested canonical digest for regrade work order",
            "separate exact revision validation for inherited and appended lanes",
            "exact top-level field contract for independent regrades",
        ],
        "files": {name: sha256(ROOT / name) for name in PINNED_FILES},
    }
    OUTPUT.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(OUTPUT.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
