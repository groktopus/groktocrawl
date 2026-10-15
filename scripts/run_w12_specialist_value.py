#!/usr/bin/env python3
"""Run blinded paired reviews for the frozen W12.5 artifacts."""

from __future__ import annotations

import argparse
import concurrent.futures
import importlib.util
import json
import os
import random
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agent-svc"))

from agent.experimental.specialist_value_experiment import (
    ReconciledArtifact,
    SpecialistHandoff,
    reconcile_handoffs,
)

TRANSPORT_PATH = ROOT / "scripts/run_w12_mission_experiment.py"
SPEC = importlib.util.spec_from_file_location("w12_model_transport", TRANSPORT_PATH)
assert SPEC and SPEC.loader
transport = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(transport)


def review_schema() -> dict[str, Any]:
    score = {
        "type": "object",
        "additionalProperties": False,
        "required": ["usefulness", "trustworthiness", "reason"],
        "properties": {
            "usefulness": {"type": "integer", "minimum": 0, "maximum": 100},
            "trustworthiness": {"type": "integer", "minimum": 0, "maximum": 100},
            "reason": {"type": "string", "minLength": 1, "maxLength": 800},
        },
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["A", "B", "preferred"],
        "properties": {
            "A": score,
            "B": score,
            "preferred": {"type": "string", "enum": ["A", "B", "tie"]},
        },
    }


def artifact_wire(artifact: ReconciledArtifact | dict[str, Any]) -> dict[str, Any]:
    payload = (
        artifact.model_dump(mode="json")
        if isinstance(artifact, ReconciledArtifact)
        else artifact
    )
    return {
        "evidence": payload["admitted_evidence"],
        "covered_obligations": payload["covered_obligation_ids"],
        "conflicts": payload["conflicting_obligation_ids"],
        "failures": payload["handoff_failures"],
    }


def execute_one(
    case: dict[str, Any],
    repetition: int,
    *,
    base_url: str,
    api_key: str,
    model: str,
    public_dir: Path,
    private_dir: Path,
    timeout: float,
    seed: int,
) -> dict[str, str]:
    trial_id = f"{case['case_id']}-r{repetition}"
    output = public_dir / "reviews" / f"{trial_id}.json"
    if output.exists() and json.loads(output.read_bytes()).get("status") == "completed":
        return {"trial_id": trial_id, "status": "resumed_completed"}
    control = ReconciledArtifact.model_validate(case["control_artifact"])
    if case["separable"]:
        handoffs = tuple(
            SpecialistHandoff.model_validate(item)
            for item in case["specialist_handoffs"]
        )
        treatment = reconcile_handoffs(
            handoffs,
            allowed_obligation_ids=frozenset(case["obligation_ids"]),
            call_budget=case["call_budget"],
        )
        treatment_calls = treatment.total_calls + 1
    else:
        treatment = control
        treatment_calls = control.total_calls
    arms = {"control": artifact_wire(control), "treatment": artifact_wire(treatment)}
    labels = ["control", "treatment"]
    random.Random(f"{seed}:{trial_id}").shuffle(labels)
    blinded = {"A": arms[labels[0]], "B": arms[labels[1]]}
    prompt = {
        "task": (
            "Score both research artifacts using only the supplied obligations and evidence. "
            "Usefulness rewards complete obligation coverage. Trustworthiness rewards exact "
            "source-bound evidence, preserved conflict, and visible failure state. Do not reward "
            "length or assume that agreement means truth. Return exactly the requested JSON."
        ),
        "question": case["question"],
        "obligation_ids": case["obligation_ids"],
        "artifacts": blinded,
    }
    try:
        content, receipt, envelope = transport.model_json(
            base_url=base_url,
            api_key=api_key,
            model=model,
            schema_name="w12_specialist_blind_review",
            schema=review_schema(),
            prompt=prompt,
            timeout=timeout,
            max_attempts=3,
            reasoning_effort="minimal",
            max_tokens=5000,
        )
        review = json.loads(content)
        by_arm = {labels[0]: review["A"], labels[1]: review["B"]}
        preferred = (
            "tie"
            if review["preferred"] == "tie"
            else labels[0 if review["preferred"] == "A" else 1]
        )
        public = {
            "schema_version": "specialist-value-review/1",
            "trial_id": trial_id,
            "case_id": case["case_id"],
            "stratum": case["stratum"],
            "separable": case["separable"],
            "repetition": repetition,
            "status": "completed",
            "scores": by_arm,
            "preferred": preferred,
            "control_artifact": control.model_dump(mode="json"),
            "treatment_artifact": treatment.model_dump(mode="json"),
            "control_calls": control.total_calls,
            "treatment_calls": treatment_calls,
            "receipt": receipt,
        }
        transport.write_json(output, public)
        transport.write_json(
            private_dir / "reviews" / f"{trial_id}.json",
            {
                "prompt": prompt,
                "labels": labels,
                "completion": content,
                "envelope": envelope,
            },
            private=True,
        )
        return {"trial_id": trial_id, "status": "completed"}
    except Exception as error:
        transport.write_json(
            output,
            {
                "trial_id": trial_id,
                "case_id": case["case_id"],
                "repetition": repetition,
                "status": "failed",
                "error_type": type(error).__name__,
                "error": str(error),
            },
        )
        return {"trial_id": trial_id, "status": "failed"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--base-url")
    parser.add_argument("--api-key")
    parser.add_argument("--model", default="general")
    parser.add_argument("--concurrency", type=int, default=5)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--seed", type=int, default=20260919)
    args = parser.parse_args()
    if not 1 <= args.concurrency <= 10:
        raise SystemExit("concurrency must be between 1 and 10")
    base_url = args.base_url or os.getenv("LLM_BASE_URL")
    api_key = args.api_key or os.getenv("LLM_API_KEY")
    if args.env_file:
        base_url = base_url or transport.read_env_value(args.env_file, "LLM_BASE_URL")
        api_key = api_key or transport.read_env_value(args.env_file, "LLM_API_KEY")
    if not base_url or not api_key:
        raise SystemExit("model endpoint and key are required")
    cases = json.loads(args.corpus.read_bytes())["cases"]
    work = [
        (case, repetition) for repetition in range(args.repetitions) for case in cases
    ]
    random.Random(args.seed).shuffle(work)
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = [
            pool.submit(
                execute_one,
                case,
                repetition,
                base_url=base_url,
                api_key=api_key,
                model=args.model,
                public_dir=args.run_dir / "public",
                private_dir=args.run_dir / "private",
                timeout=args.timeout,
                seed=args.seed,
            )
            for case, repetition in work
        ]
        results = [future.result() for future in futures]
    transport.write_json(
        args.run_dir / "public/summary.json",
        {
            "completed": sum(item["status"] != "failed" for item in results),
            "failed": sum(item["status"] == "failed" for item in results),
        },
    )
    return int(any(item["status"] == "failed" for item in results))


if __name__ == "__main__":
    raise SystemExit(main())
