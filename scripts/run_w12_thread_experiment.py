#!/usr/bin/env python3
"""Run resumable W12.2 initial roots and matched follow-up trials."""

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

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agent-svc"))

from agent.experimental.research_thread import (
    ThreadAnswer,
    ThreadExperimentCase,
    ThreadWorkItem,
    answer_schema,
    build_followup_prompt,
    build_thread_work_order,
    load_thread_experiment_corpus,
    validate_thread_answer,
)

MISSION_RUNNER = ROOT / "scripts/run_w12_mission_experiment.py"
SPEC = importlib.util.spec_from_file_location("w12_model_transport", MISSION_RUNNER)
assert SPEC and SPEC.loader
transport = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(transport)


def initial_prompt(case: ThreadExperimentCase) -> dict[str, Any]:
    return {
        "task": (
            "Answer the initial question using only supplied snapshots. Record current "
            "truths and unresolved limits. Cite snapshot_id values."
        ),
        "question": case.initial_question,
        "current_sources": [
            source.model_dump(mode="json") for source in case.initial_sources
        ],
        "output_contract": answer_schema(case),
    }


def complete(
    *,
    prompt: dict[str, Any],
    case: ThreadExperimentCase,
    base_url: str,
    api_key: str,
    model: str,
    timeout: float,
) -> tuple[ThreadAnswer, dict[str, Any], str, str]:
    content, receipt, envelope = transport.model_json(
        base_url=base_url,
        api_key=api_key,
        model=model,
        schema_name="w12_thread_answer",
        schema=answer_schema(case),
        prompt=prompt,
        timeout=timeout,
        max_attempts=3,
        reasoning_effort="low",
        max_tokens=10000,
    )
    if (
        content is None
        or receipt["finish_reason"] != "stop"
        or receipt["refusal"]
        or receipt["tool_calls"]
    ):
        raise ValueError("model completion is not a final answer")
    return validate_thread_answer(json.loads(content), case), receipt, envelope, content


def prepare_initial(
    case: ThreadExperimentCase,
    *,
    public_dir: Path,
    private_dir: Path,
    base_url: str,
    api_key: str,
    model: str,
    timeout: float,
) -> ThreadAnswer:
    public_path = public_dir / "initial" / f"{case.case_id}.json"
    if public_path.exists():
        record = json.loads(public_path.read_bytes())
        if record.get("status") == "completed":
            return validate_thread_answer(record["answer"], case)
    prompt = initial_prompt(case)
    answer, receipt, envelope, content = complete(
        prompt=prompt,
        case=case,
        base_url=base_url,
        api_key=api_key,
        model=model,
        timeout=timeout,
    )
    transport.write_json(
        private_dir / "initial" / f"{case.case_id}.json",
        {"prompt": prompt, "completion": content, "envelope": envelope},
        private=True,
    )
    transport.write_json(
        public_path,
        {
            "schema_version": "research-thread-initial/1",
            "case_id": case.case_id,
            "status": "completed",
            "receipt": receipt,
            "answer": answer.model_dump(mode="json"),
        },
    )
    return answer


def run_trial(
    item: ThreadWorkItem,
    *,
    case: ThreadExperimentCase,
    prior: ThreadAnswer,
    public_dir: Path,
    private_dir: Path,
    base_url: str,
    api_key: str,
    model: str,
    timeout: float,
) -> dict[str, str]:
    public_path = public_dir / "trials" / f"{item.trial_id}.json"
    if public_path.exists():
        existing = json.loads(public_path.read_bytes())
        if existing.get("status") == "completed":
            return {"trial_id": item.trial_id, "status": "resumed_completed"}
    started = datetime.now(UTC).isoformat()
    prompt = build_followup_prompt(case, arm=item.arm, prior_answer=prior)
    try:
        answer, receipt, envelope, content = complete(
            prompt=prompt,
            case=case,
            base_url=base_url,
            api_key=api_key,
            model=model,
            timeout=timeout,
        )
        transport.write_json(
            private_dir / "trials" / f"{item.trial_id}.json",
            {"prompt": prompt, "completion": content, "envelope": envelope},
            private=True,
        )
        transport.write_json(
            public_path,
            {
                "schema_version": "research-thread-trial/1",
                **item.model_dump(mode="json"),
                "stratum": case.stratum,
                "status": "completed",
                "started_at": started,
                "completed_at": datetime.now(UTC).isoformat(),
                "receipt": receipt,
                "answer": answer.model_dump(mode="json"),
            },
        )
        return {"trial_id": item.trial_id, "status": "completed"}
    except Exception as error:
        transport.write_json(
            public_path,
            {
                "schema_version": "research-thread-trial/1",
                **item.model_dump(mode="json"),
                "stratum": case.stratum,
                "status": "failed",
                "started_at": started,
                "completed_at": datetime.now(UTC).isoformat(),
                "error_type": type(error).__name__,
                "error": str(error),
            },
        )
        return {"trial_id": item.trial_id, "status": "failed"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--base-url")
    parser.add_argument("--api-key")
    parser.add_argument("--model", default="general")
    parser.add_argument("--concurrency", type=int, default=5)
    parser.add_argument("--timeout", type=float, default=600)
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
    corpus = load_thread_experiment_corpus(args.corpus)
    cases = {case.case_id: case for case in corpus.cases}
    public_dir = args.output / "public"
    private_dir = args.output / "private"
    args.output.mkdir(parents=True, exist_ok=True)
    args.output.chmod(0o700)
    prior = {
        case.case_id: prepare_initial(
            case,
            public_dir=public_dir,
            private_dir=private_dir,
            base_url=base_url,
            api_key=api_key,
            model=args.model,
            timeout=args.timeout,
        )
        for case in corpus.cases
    }
    work = build_thread_work_order(corpus, seed=args.seed)
    transport.write_json(
        public_dir / "work-order.json",
        {
            "schema_version": "research-thread-work-order/1",
            "seed": args.seed,
            "items": [item.model_dump(mode="json") for item in work],
        },
    )
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = [
            pool.submit(
                run_trial,
                item,
                case=cases[item.case_id],
                prior=prior[item.case_id],
                public_dir=public_dir,
                private_dir=private_dir,
                base_url=base_url,
                api_key=api_key,
                model=args.model,
                timeout=args.timeout,
            )
            for item in work
        ]
        results = [future.result() for future in futures]
    transport.write_json(
        public_dir / "run-summary.json",
        {
            "schema_version": "research-thread-run-summary/1",
            "completed": sum(item["status"] != "failed" for item in results),
            "failed": sum(item["status"] == "failed" for item in results),
            "initial_roots": len(prior),
        },
    )
    return 1 if any(item["status"] == "failed" for item in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
