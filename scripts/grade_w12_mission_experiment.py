#!/usr/bin/env python3
"""Blindly grade W12.1 downstream candidates with resumable receipts."""

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
    GradeWorkItem,
    build_grade_prompt,
    validate_candidate_grade,
)
from agent.experimental.research_mission import load_mission_experiment_corpus

RUNNER_PATH = ROOT / "scripts/run_w12_mission_experiment.py"
SPEC = importlib.util.spec_from_file_location("w12_downstream_runner", RUNNER_PATH)
assert SPEC and SPEC.loader
downstream = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(downstream)


def grade_schema(case: Any) -> dict[str, Any]:
    source_list = {
        "type": "array",
        "items": {"type": "string", "enum": list(case.source_ids)},
        "maxItems": 20,
    }
    obligation_grade = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "status": {"type": "string", "enum": ["closed", "partial", "open"]},
            "source_ids": source_list,
            "rationale": {"type": "string", "minLength": 1, "maxLength": 2000},
        },
        "required": ["status", "source_ids", "rationale"],
    }
    obligation_ids = [item.obligation_id for item in case.reference_mission.obligations]
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "obligation_grades": {
                "type": "object",
                "additionalProperties": False,
                "properties": dict.fromkeys(obligation_ids, obligation_grade),
                "required": obligation_ids,
            },
            "scope_violations": {
                "type": "array",
                "maxItems": 20,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "kind": {
                            "type": "string",
                            "enum": [
                                "unsupported_expansion",
                                "omitted_required_scope",
                                "invented_authority",
                                "lost_freshness",
                                "unauthorized_action",
                            ],
                        },
                        "severity": {
                            "type": "string",
                            "enum": ["minor", "material"],
                        },
                        "rationale": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 2000,
                        },
                    },
                    "required": ["kind", "severity", "rationale"],
                },
            },
            "supported_material_claims": {
                "type": "integer",
                "minimum": 0,
                "maximum": 30,
            },
            "total_material_claims": {
                "type": "integer",
                "minimum": 0,
                "maximum": 30,
            },
            "decision_usefulness": {
                "type": "integer",
                "minimum": 0,
                "maximum": 100,
            },
            "decision_usefulness_rationale": {
                "type": "string",
                "minLength": 1,
                "maxLength": 2000,
            },
            "appropriate_abstention": {
                "type": "string",
                "enum": ["yes", "no", "not_applicable"],
            },
            "hard_boundary_failure": {"type": "boolean"},
            "hard_boundary_rationale": {
                "type": "string",
                "minLength": 1,
                "maxLength": 2000,
            },
        },
        "required": [
            "obligation_grades",
            "scope_violations",
            "supported_material_claims",
            "total_material_claims",
            "decision_usefulness",
            "decision_usefulness_rationale",
            "appropriate_abstention",
            "hard_boundary_failure",
            "hard_boundary_rationale",
        ],
    }


def load_candidates(run_dir: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for path in sorted((run_dir / "public/trials").glob("*.json")):
        record = json.loads(path.read_bytes())
        if record.get("status") != "completed":
            continue
        candidate = record["sealed_candidate"]
        candidate_id = candidate["candidate_id"]
        if candidate_id in result:
            raise ValueError("duplicate sealed candidate identity")
        result[candidate_id] = candidate
    return result


def execute_grade(
    item: GradeWorkItem,
    *,
    case: Any,
    candidate: dict[str, Any],
    sources: tuple[dict[str, str], ...],
    public_dir: Path,
    private_dir: Path,
    base_url: str,
    api_key: str,
    model: str,
    timeout: float,
    max_attempts: int,
) -> dict[str, str]:
    public_path = public_dir / "grades" / f"{item.candidate_id}.json"
    if public_path.exists():
        existing = json.loads(public_path.read_bytes())
        if existing.get("status") == "completed":
            return {"candidate_id": item.candidate_id, "status": "resumed_completed"}
    started_at = datetime.now(UTC).isoformat()
    prompt = build_grade_prompt(case, sources=sources, candidate=candidate)
    private_path = private_dir / "grades" / f"{item.candidate_id}.json"
    try:
        content, receipt, envelope = downstream.model_json(
            base_url=base_url,
            api_key=api_key,
            model=model,
            schema_name="w12_mission_grade",
            schema=grade_schema(case),
            prompt=prompt,
            timeout=timeout,
            max_attempts=max_attempts,
            reasoning_effort="minimal",
            max_tokens=20000,
        )
        downstream.write_json(
            private_path,
            {
                "schema_version": "research-mission-private-grade/1",
                "candidate_id": item.candidate_id,
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
            raise ValueError("model completion is not a final grade")
        if content is None:
            raise ValueError("model response has no string content")
        grade = validate_candidate_grade(json.loads(content), case=case)
        downstream.write_json(
            public_path,
            {
                "schema_version": "research-mission-grade/1",
                "candidate_id": item.candidate_id,
                "case_id": item.case_id,
                "position": item.position,
                "status": "completed",
                "started_at": started_at,
                "completed_at": datetime.now(UTC).isoformat(),
                "receipt": receipt,
                "grade": grade.model_dump(mode="json"),
            },
        )
        return {"candidate_id": item.candidate_id, "status": "completed"}
    except Exception as error:
        downstream.write_json(
            public_path,
            {
                "schema_version": "research-mission-grade/1",
                "candidate_id": item.candidate_id,
                "case_id": item.case_id,
                "position": item.position,
                "status": "failed",
                "started_at": started_at,
                "failed_at": datetime.now(UTC).isoformat(),
                "error_type": type(error).__name__,
                "error": str(error),
            },
        )
        return {"candidate_id": item.candidate_id, "status": "failed"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
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
    source_path = ROOT / "docs/experiments/enterprise-evaluation/corpus.json"
    corpus = load_mission_experiment_corpus(
        experiment_dir / "w12.1-cases.json", source_corpus_path=source_path
    )
    cases = {item.case_id: item for item in corpus.cases}
    sources_by_id = downstream.source_index(source_path)
    work_payload = json.loads(
        (experiment_dir / "w12.1-grade-work-order.json").read_bytes()
    )
    work = tuple(GradeWorkItem(**item) for item in work_payload["trials"])
    candidates = load_candidates(args.run_dir)
    expected = {item.candidate_id for item in work}
    if not set(candidates) <= expected:
        raise ValueError("completed candidate set is outside frozen grade work order")
    eligible_work = tuple(item for item in work if item.candidate_id in candidates)
    private_dir = args.run_dir / "private"
    public_dir = args.run_dir / "public"
    downstream.write_json(
        public_dir / "grade-manifest.json",
        {
            "schema_version": "research-mission-grade-manifest/1",
            "source_revision": args.source_revision,
            "model_route": args.model,
            "base_url_origin": str(httpx.URL(args.base_url).copy_with(path="/")),
            "concurrency": args.concurrency,
            "work_order_sha256": work_payload["trials_sha256"],
            "eligible_candidate_count": len(eligible_work),
            "started_at": datetime.now(UTC).isoformat(),
        },
    )
    results: list[dict[str, str]] = []
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=args.concurrency
    ) as executor:
        futures = []
        for item in eligible_work:
            case = cases[item.case_id]
            sources = tuple(sources_by_id[source_id] for source_id in case.source_ids)
            futures.append(
                executor.submit(
                    execute_grade,
                    item,
                    case=case,
                    candidate=candidates[item.candidate_id],
                    sources=sources,
                    public_dir=public_dir,
                    private_dir=private_dir,
                    base_url=args.base_url,
                    api_key=api_key,
                    model=args.model,
                    timeout=args.timeout,
                    max_attempts=args.max_attempts,
                )
            )
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            results.append(result)
            print(f"{result['candidate_id']}: {result['status']}", flush=True)
    completed = sum(
        item["status"] in {"completed", "resumed_completed"} for item in results
    )
    failed = sum(item["status"] == "failed" for item in results)
    downstream.write_json(
        public_dir / "grade-summary.json",
        {
            "schema_version": "research-mission-grade-summary/1",
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
