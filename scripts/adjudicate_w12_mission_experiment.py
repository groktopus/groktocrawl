#!/usr/bin/env python3
"""Select and independently regrade the frozen W12.1 adjudication sample."""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import importlib.util
import json
import math
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agent-svc"))

from agent.experimental.mission_experiment import (
    build_adjudication_prompt,
    validate_candidate_grade,
)
from agent.experimental.research_mission import load_mission_experiment_corpus


def import_script(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


downstream = import_script(
    "w12_downstream_runner", ROOT / "scripts/run_w12_mission_experiment.py"
)
grader = import_script(
    "w12_grader", ROOT / "scripts/grade_w12_mission_experiment.py"
)


def canonical_digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode()
    ).hexdigest()


def select_adjudications(
    *,
    grade_records: dict[str, dict[str, Any]],
    cases: dict[str, Any],
    seed: int,
) -> list[dict[str, Any]]:
    candidate_ids = sorted(grade_records)
    selected: dict[str, set[str]] = {}
    for candidate_id, record in grade_records.items():
        grade = record["grade"]
        case = cases[record["case_id"]]
        weights = {
            item.obligation_id: item.weight
            for item in case.reference_mission.obligations
        }
        if grade["hard_boundary_failure"]:
            selected.setdefault(candidate_id, set()).add("hard_boundary_finding")
        if any(
            weights[obligation_id] >= 4 and value["status"] != "closed"
            for obligation_id, value in grade["obligation_grades"].items()
        ):
            selected.setdefault(candidate_id, set()).add(
                "high_weight_obligation_not_closed"
            )
    remaining = [value for value in candidate_ids if value not in selected]
    sample_size = math.ceil(len(remaining) * 0.20)
    sample = sorted(
        remaining,
        key=lambda value: hashlib.sha256(
            f"{seed}:adjudication:{value}".encode()
        ).hexdigest(),
    )[:sample_size]
    for candidate_id in sample:
        selected.setdefault(candidate_id, set()).add("seeded_20_percent_of_remaining")
    ordered = sorted(
        selected,
        key=lambda value: hashlib.sha256(
            f"{seed}:adjudication-order:{value}".encode()
        ).hexdigest(),
    )
    return [
        {
            "candidate_id": candidate_id,
            "case_id": grade_records[candidate_id]["case_id"],
            "position": position,
            "reasons": sorted(selected[candidate_id]),
        }
        for position, candidate_id in enumerate(ordered, 1)
    ]


def execute_adjudication(
    item: dict[str, Any],
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
    candidate_id = item["candidate_id"]
    public_path = public_dir / "adjudications" / f"{candidate_id}.json"
    if public_path.exists() and json.loads(public_path.read_bytes()).get("status") == "completed":
        return {"candidate_id": candidate_id, "status": "resumed_completed"}
    started_at = datetime.now(UTC).isoformat()
    prompt = build_adjudication_prompt(case, sources=sources, candidate=candidate)
    private_path = private_dir / "adjudications" / f"{candidate_id}.json"
    try:
        content, receipt, envelope = downstream.model_json(
            base_url=base_url,
            api_key=api_key,
            model=model,
            schema_name="w12_mission_adjudication",
            schema=grader.grade_schema(case),
            prompt=prompt,
            timeout=timeout,
            max_attempts=max_attempts,
            reasoning_effort="minimal",
            max_tokens=20000,
        )
        downstream.write_json(
            private_path,
            {
                "schema_version": "research-mission-private-adjudication/1",
                "candidate_id": candidate_id,
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
            or content is None
        ):
            raise ValueError("model completion is not a final adjudication")
        result = validate_candidate_grade(json.loads(content), case=case)
        downstream.write_json(
            public_path,
            {
                "schema_version": "research-mission-adjudication/1",
                **item,
                "status": "completed",
                "started_at": started_at,
                "completed_at": datetime.now(UTC).isoformat(),
                "receipt": receipt,
                "grade": result.model_dump(mode="json"),
            },
        )
        return {"candidate_id": candidate_id, "status": "completed"}
    except Exception as error:
        downstream.write_json(
            public_path,
            {
                "schema_version": "research-mission-adjudication/1",
                **item,
                "status": "failed",
                "started_at": started_at,
                "failed_at": datetime.now(UTC).isoformat(),
                "error_type": type(error).__name__,
                "error": str(error),
            },
        )
        return {"candidate_id": candidate_id, "status": "failed"}


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
    parser.add_argument("--seed", type=int, default=20260919)
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
    grade_records = {
        path.stem: json.loads(path.read_bytes())
        for path in (args.run_dir / "public/grades").glob("*.json")
        if json.loads(path.read_bytes()).get("status") == "completed"
    }
    if not grade_records:
        raise ValueError("adjudication requires at least one completed original grade")
    work = select_adjudications(grade_records=grade_records, cases=cases, seed=args.seed)
    work_record = {
        "schema_version": "research-mission-adjudication-work-order/1",
        "seed": args.seed,
        "selection_rule": "every hard boundary and high-weight non-closure, plus a seeded 20 percent of remaining candidates",
        "trials": work,
    }
    work_record["trials_sha256"] = canonical_digest(work)
    downstream.write_json(
        args.run_dir / "public/adjudication-work-order.json", work_record
    )
    candidates = grader.load_candidates(args.run_dir)
    sources_by_id = downstream.source_index(source_path)
    public_dir, private_dir = args.run_dir / "public", args.run_dir / "private"
    downstream.write_json(
        public_dir / "adjudication-manifest.json",
        {
            "schema_version": "research-mission-adjudication-manifest/1",
            "source_revision": args.source_revision,
            "model_route": args.model,
            "base_url_origin": str(httpx.URL(args.base_url).copy_with(path="/")),
            "concurrency": args.concurrency,
            "selection_sha256": hashlib.sha256(
                json.dumps(work_record, separators=(",", ":"), sort_keys=True).encode()
            ).hexdigest(),
            "work_order_sha256": work_record["trials_sha256"],
            "started_at": datetime.now(UTC).isoformat(),
        },
    )
    results: list[dict[str, str]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        futures = []
        for item in work:
            case = cases[item["case_id"]]
            sources = tuple(sources_by_id[source_id] for source_id in case.source_ids)
            futures.append(
                executor.submit(
                    execute_adjudication,
                    item,
                    case=case,
                    candidate=candidates[item["candidate_id"]],
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
    completed = sum(item["status"] in {"completed", "resumed_completed"} for item in results)
    failed = sum(item["status"] == "failed" for item in results)
    downstream.write_json(
        public_dir / "adjudication-summary.json",
        {
            "schema_version": "research-mission-adjudication-summary/1",
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
