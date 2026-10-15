#!/usr/bin/env python3
"""Blindly grade W12.2 follow-up answers."""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import importlib.util
import json
import os
import random
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agent-svc"))

from agent.experimental.research_thread import (
    ThreadGrade,
    grade_schema,
    load_thread_experiment_corpus,
)

RUNNER = ROOT / "scripts/run_w12_mission_experiment.py"
SPEC = importlib.util.spec_from_file_location("w12_model_transport", RUNNER)
assert SPEC and SPEC.loader
transport = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(transport)


def candidate_id(trial_id: str) -> str:
    return "candidate-" + hashlib.sha256(trial_id.encode()).hexdigest()[:20]


def grade_one(
    trial: dict[str, Any],
    *,
    case: Any,
    public_dir: Path,
    private_dir: Path,
    base_url: str,
    api_key: str,
    model: str,
    timeout: float,
    max_attempts: int,
    max_tokens: int,
    completion_attempts: int,
) -> dict[str, str]:
    identity = candidate_id(trial["trial_id"])
    output = public_dir / "grades" / f"{identity}.json"
    if output.exists() and json.loads(output.read_bytes()).get("status") == "completed":
        return {"candidate_id": identity, "status": "resumed_completed"}
    required_output = {
        "change_accuracy": 0,
        "current_accuracy": 0,
        "historical_preservation": 0,
        "unresolved_accuracy": 0,
        "usefulness": 0,
        "false_merge": False,
        "stale_current_leak": False,
        "lost_history": False,
        "unsupported_claims": 0,
        "rationale": "Brief justification grounded in the reference and sources.",
    }
    prompt = {
        "task": (
            "Grade the candidate against the reference ledger and exact sources. "
            "Score factual state, not wording. A derivative is not independent. "
            "A near-match capability transfer is a false merge. Old evidence stated "
            "as current is stale-current leakage. Every score is an INTEGER ON A "
            "0 TO 100 SCALE: 100 means fully correct, 50 means half correct, and 0 "
            "means wholly wrong or omitted. Never use a 0-to-1 scale. Return one "
            "JSON object with exactly the keys shown in required_output. Do not "
            "rename, group, wrap, or add fields."
        ),
        "required_output": required_output,
        "field_rules": {
            "change_accuracy": "Accuracy and completeness of what changed.",
            "current_accuracy": "Accuracy and completeness of current truths.",
            "historical_preservation": "Preservation of earlier truths as historical, not current.",
            "unresolved_accuracy": "Accuracy of what remains unresolved; do not reward invented peripheral questions.",
            "usefulness": "Decision usefulness and auditability of the answer.",
            "false_merge": "True only if distinct subjects or capabilities were merged.",
            "stale_current_leak": "True only if superseded evidence was stated as current.",
            "lost_history": "True only if a historically valid claim was omitted or rewritten.",
            "unsupported_claims": "Count unsupported factual claims, from 0 through 20.",
            "rationale": "A concise explanation of the scores and failure flags.",
        },
        "question": case.question,
        "reference": case.expected.model_dump(mode="json"),
        "sources": [
            item.model_dump(mode="json")
            for item in (*case.initial_sources, *case.followup_sources)
        ],
        "candidate": trial["answer"],
    }
    started = datetime.now(UTC).isoformat()
    invalid_completions: list[dict[str, Any]] = []
    try:
        for completion_attempt in range(1, completion_attempts + 1):
            content, receipt, envelope = transport.model_json(
                base_url=base_url,
                api_key=api_key,
                model=model,
                schema_name="w12_thread_grade",
                schema=grade_schema(),
                prompt=prompt,
                timeout=timeout,
                max_attempts=max_attempts,
                reasoning_effort="minimal",
                max_tokens=max_tokens,
            )
            transport.write_json(
                private_dir
                / "grades"
                / f"{identity}-attempt-{completion_attempt}.json",
                {
                    "prompt": prompt,
                    "completion": content,
                    "receipt": receipt,
                    "envelope": envelope,
                },
                private=True,
            )
            try:
                if (
                    content is None
                    or receipt["finish_reason"] != "stop"
                    or receipt["refusal"]
                    or receipt["tool_calls"]
                ):
                    raise ValueError("model completion is not a final grade")
                grade = ThreadGrade.model_validate(json.loads(content))
                break
            except ValueError as error:
                invalid_completions.append(
                    {
                        "attempt": completion_attempt,
                        "error_type": type(error).__name__,
                        "error": str(error),
                        "receipt": receipt,
                    }
                )
                if completion_attempt == completion_attempts:
                    raise
        transport.write_json(
            output,
            {
                "schema_version": "research-thread-grade/1",
                "candidate_id": identity,
                "trial_id": trial["trial_id"],
                "case_id": trial["case_id"],
                "status": "completed",
                "started_at": started,
                "completed_at": datetime.now(UTC).isoformat(),
                "receipt": receipt,
                "completion_attempt": completion_attempt,
                "prior_invalid_completions": invalid_completions,
                "grade": grade.model_dump(mode="json"),
            },
        )
        return {"candidate_id": identity, "status": "completed"}
    except Exception as error:
        failure_receipt = locals().get("receipt")
        transport.write_json(
            output,
            {
                "schema_version": "research-thread-grade/1",
                "candidate_id": identity,
                "trial_id": trial["trial_id"],
                "case_id": trial["case_id"],
                "status": "failed",
                "started_at": started,
                "completed_at": datetime.now(UTC).isoformat(),
                "error_type": type(error).__name__,
                "error": str(error),
                "receipt": failure_receipt,
                "invalid_completions": invalid_completions,
            },
        )
        return {"candidate_id": identity, "status": "failed"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--base-url")
    parser.add_argument("--api-key")
    parser.add_argument("--model", default="general")
    parser.add_argument("--concurrency", type=int, default=5)
    parser.add_argument("--timeout", type=float, default=600)
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--max-tokens", type=int, default=20000)
    parser.add_argument("--completion-attempts", type=int, default=1)
    parser.add_argument("--seed", type=int, default=20260919)
    args = parser.parse_args()
    if not 1 <= args.concurrency <= 10:
        raise SystemExit("concurrency must be between 1 and 10")
    if not 1 <= args.max_attempts <= 3:
        raise SystemExit("max-attempts must be between 1 and 3")
    if not 1000 <= args.max_tokens <= 20000:
        raise SystemExit("max-tokens must be between 1000 and 20000")
    if not 1 <= args.completion_attempts <= 2:
        raise SystemExit("completion-attempts must be between 1 and 2")
    base_url = args.base_url or os.getenv("LLM_BASE_URL")
    api_key = args.api_key or os.getenv("LLM_API_KEY")
    if args.env_file:
        base_url = base_url or transport.read_env_value(args.env_file, "LLM_BASE_URL")
        api_key = api_key or transport.read_env_value(args.env_file, "LLM_API_KEY")
    if not base_url or not api_key:
        raise SystemExit("model endpoint and key are required")
    corpus = load_thread_experiment_corpus(args.corpus)
    cases = {case.case_id: case for case in corpus.cases}
    trials = [
        json.loads(path.read_bytes())
        for path in sorted((args.run_dir / "public/trials").glob("*.json"))
        if json.loads(path.read_bytes()).get("status") == "completed"
    ]
    random.Random(args.seed).shuffle(trials)
    public_dir = args.run_dir / "public"
    private_dir = args.run_dir / "private"
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = [
            pool.submit(
                grade_one,
                trial,
                case=cases[trial["case_id"]],
                public_dir=public_dir,
                private_dir=private_dir,
                base_url=base_url,
                api_key=api_key,
                model=args.model,
                timeout=args.timeout,
                max_attempts=args.max_attempts,
                max_tokens=args.max_tokens,
                completion_attempts=args.completion_attempts,
            )
            for trial in trials
        ]
        results = [future.result() for future in futures]
    transport.write_json(
        public_dir / "grade-summary.json",
        {
            "schema_version": "research-thread-grade-summary/1",
            "completed": sum(item["status"] != "failed" for item in results),
            "failed": sum(item["status"] == "failed" for item in results),
        },
    )
    return 1 if any(item["status"] == "failed" for item in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
