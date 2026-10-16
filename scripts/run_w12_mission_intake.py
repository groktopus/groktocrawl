#!/usr/bin/env python3
"""Run resumable W12.1 raw-request mission-normalization trials."""

from __future__ import annotations

import argparse
import concurrent.futures
import importlib.util
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agent-svc"))

from agent.experimental.mission_experiment import (
    IntakeResult,
    IntakeWorkItem,
    build_intake_prompt,
    validate_intake_result,
)
from agent.experimental.research_mission import load_mission_experiment_corpus

RUNNER_PATH = ROOT / "scripts/run_w12_mission_experiment.py"
SPEC = importlib.util.spec_from_file_location("w12_downstream_runner", RUNNER_PATH)
assert SPEC and SPEC.loader
downstream = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(downstream)


def intake_response_schema() -> dict[str, Any]:
    """Return the strict public intake envelope for provider-side validation."""

    return IntakeResult.model_json_schema()


def execute_intake(
    item: IntakeWorkItem,
    *,
    case: Any,
    public_dir: Path,
    private_dir: Path,
    base_url: str,
    api_key: str,
    model: str,
    timeout: float,
    max_attempts: int,
) -> dict[str, str]:
    public_path = public_dir / "intake" / f"{item.trial_id}.json"
    if public_path.exists():
        existing = json.loads(public_path.read_bytes())
        if existing.get("status") == "completed":
            return {"trial_id": item.trial_id, "status": "resumed_completed"}
    started_at = datetime.now(UTC).isoformat()
    prompt = build_intake_prompt(case)
    private_path = private_dir / "intake" / f"{item.trial_id}.json"
    try:
        content, receipt, envelope = downstream.model_json(
            base_url=base_url,
            api_key=api_key,
            model=model,
            schema_name="w12_mission_intake",
            schema=intake_response_schema(),
            prompt=prompt,
            timeout=timeout,
            max_attempts=max_attempts,
            reasoning_effort="minimal",
            max_tokens=20000,
        )
        downstream.write_json(
            private_path,
            {
                "schema_version": "research-mission-private-intake/1",
                "trial_id": item.trial_id,
                "prompt": prompt,
                "raw_completion": content,
                "raw_envelope": envelope,
                "receipt": receipt,
                "recorded_at": datetime.now(UTC).isoformat(),
            },
            private=True,
        )
        if (
            receipt["finish_reason"] != "stop"
            or receipt["refusal"]
            or receipt["tool_calls"]
        ):
            raise ValueError("model completion is not a final intake result")
        if content is None:
            raise ValueError("model response has no string content")
        result = validate_intake_result(json.loads(content))
        downstream.write_json(
            public_path,
            {
                "schema_version": "research-mission-intake-trial/1",
                "trial_id": item.trial_id,
                "case_id": item.case_id,
                "repetition": item.repetition,
                "position": item.position,
                "status": "completed",
                "started_at": started_at,
                "completed_at": datetime.now(UTC).isoformat(),
                "receipt": receipt,
                "result": result.model_dump(mode="json"),
            },
        )
        return {"trial_id": item.trial_id, "status": "completed"}
    except Exception as error:
        downstream.write_json(
            public_path,
            {
                "schema_version": "research-mission-intake-trial/1",
                "trial_id": item.trial_id,
                "case_id": item.case_id,
                "repetition": item.repetition,
                "position": item.position,
                "status": "failed",
                "started_at": started_at,
                "failed_at": datetime.now(UTC).isoformat(),
                "error_type": type(error).__name__,
                "error": str(error),
            },
        )
        return {"trial_id": item.trial_id, "status": "failed"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--model", default="general")
    parser.add_argument("--api-key-env", default="LLM_API_KEY")
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--concurrency", type=int, default=5)
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--max-attempts", type=int, default=3)
    args = parser.parse_args()
    if not 1 <= args.concurrency <= 10:
        raise ValueError("concurrency must be between 1 and 10")
    if downstream.current_revision() != args.source_revision:
        raise ValueError("source revision does not match the checked-out commit")
    api_key = os.environ.get(args.api_key_env, "")
    if not api_key and args.env_file:
        api_key = downstream.read_env_value(args.env_file, args.api_key_env)
    if not api_key:
        raise ValueError(f"missing API key environment variable: {args.api_key_env}")

    experiment_dir = ROOT / "docs/experiments/research-mission"
    corpus = load_mission_experiment_corpus(
        experiment_dir / "w12.1-cases.json",
        source_corpus_path=ROOT / "docs/experiments/enterprise-evaluation/corpus.json",
    )
    cases = {item.case_id: item for item in corpus.cases}
    work_payload = json.loads(
        (experiment_dir / "w12.1-intake-work-order.json").read_bytes()
    )
    work = tuple(IntakeWorkItem(**item) for item in work_payload["trials"])
    private_dir = args.output_dir / "private"
    private_dir.mkdir(parents=True, exist_ok=True)
    private_dir.chmod(0o700)
    public_dir = args.output_dir / "public"
    downstream.write_json(
        public_dir / "intake-manifest.json",
        {
            "schema_version": "research-mission-intake-manifest/1",
            "source_revision": args.source_revision,
            "model_route": args.model,
            "base_url_origin": str(httpx.URL(args.base_url).copy_with(path="/")),
            "concurrency": args.concurrency,
            "work_order_sha256": work_payload["trials_sha256"],
            "started_at": datetime.now(UTC).isoformat(),
        },
    )
    results: list[dict[str, str]] = []
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=args.concurrency
    ) as executor:
        futures = [
            executor.submit(
                execute_intake,
                item,
                case=cases[item.case_id],
                public_dir=public_dir,
                private_dir=private_dir,
                base_url=args.base_url,
                api_key=api_key,
                model=args.model,
                timeout=args.timeout,
                max_attempts=args.max_attempts,
            )
            for item in work
        ]
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            results.append(result)
            print(f"{result['trial_id']}: {result['status']}", flush=True)
    completed = sum(
        item["status"] in {"completed", "resumed_completed"} for item in results
    )
    failed = sum(item["status"] == "failed" for item in results)
    downstream.write_json(
        public_dir / "intake-summary.json",
        {
            "schema_version": "research-mission-intake-summary/1",
            "attempted": len(results),
            "completed": completed,
            "failed": failed,
            "finished_at": datetime.now(UTC).isoformat(),
        },
    )
    print(f"completed={completed} failed={failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
