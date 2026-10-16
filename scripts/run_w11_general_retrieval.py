#!/usr/bin/env python3
"""Run resumable matched W11 retrieval trials from the frozen W10 plans."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

from scripts.w11_mcp_client import W11McpClient
from scripts.w11_retrieval_transports import (
    flat_http_searches,
    recorded_continuation_searches,
)

MAX_CHECKPOINT_BYTES = 5 * 1024 * 1024


def digest(value: str | bytes) -> str:
    raw = value.encode() if isinstance(value, str) else value
    return hashlib.sha256(raw).hexdigest()


def atomic_json(
    path: Path,
    value: Any,
    *,
    mode: int = 0o644,
    max_bytes: int = MAX_CHECKPOINT_BYTES,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    encoded = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()
    if len(encoded) > max_bytes:
        raise ValueError(
            f"checkpoint exceeds the {max_bytes}-byte W11 evidence limit"
        )
    temporary.write_bytes(encoded)
    temporary.chmod(mode)
    temporary.replace(path)


def load_w10_record(
    records_dir: Path, entry: dict[str, Any]
) -> tuple[dict[str, Any], Path]:
    filename = (
        f"{entry['case_id']}--{entry['control_policy']}--{entry['repetition']}.json"
    )
    path = records_dir / filename
    if not path.is_file():
        raise ValueError(f"missing W10 source record: {filename}")
    record = json.loads(path.read_text())
    if record.get("schema_version") not in {
        None,
        "enterprise-evaluation/w10-policy-trial/1",
        "enterprise-evaluation/w11-fixed-control-record/1",
    }:
        raise ValueError(f"unsupported W10 source record schema: {filename}")
    identity = (record.get("case_id"), record.get("policy"), record.get("repetition"))
    expected = (entry["case_id"], entry["control_policy"], entry["repetition"])
    if identity != expected or record.get("status") != "completed":
        raise ValueError(f"invalid W10 source record: {filename}")
    return record, path


def frozen_queries(record: dict[str, Any]) -> list[str]:
    queries = [item.get("query") for item in record.get("attempts", [])]
    if not queries or not all(
        isinstance(item, str) and item.strip() for item in queries
    ):
        raise ValueError("W10 source record has no valid executed query sequence")
    return queries


def public_record(
    *,
    entry: dict[str, Any],
    source_sha256: str,
    queries: list[str],
    engines: list[str],
    raw: list[dict[str, Any]],
    elapsed_ms: float,
    job: dict[str, Any] | None,
) -> dict[str, Any]:
    attempts = []
    for item in raw:
        urls = [str(result.get("url", "")) for result in item.get("results", [])]
        attempts.append(
            {
                "query_sha256": digest(str(item["query"])),
                "result_count": item.get("result_count", len(urls)),
                "returned_results": len(urls),
                "result_url_sha256": [digest(url) for url in urls if url],
                "coverage": item.get("coverage"),
                "unresponsive_engine_count": len(item.get("unresponsive_engines", [])),
            }
        )
    result: dict[str, Any] = {
        "schema_version": "enterprise-evaluation/w11-general-retrieval/1",
        "status": "completed",
        "position": entry["position"],
        "case_id": entry["case_id"],
        "challenge_type": entry["challenge_type"],
        "repetition": entry["repetition"],
        "arm": entry["arm"],
        "control_policy": entry["control_policy"],
        "w10_source_record_sha256": source_sha256,
        "query_plan_sha256": digest(json.dumps(queries, separators=(",", ":"))),
        "engine_scope_sha256": digest(json.dumps(engines, separators=(",", ":"))),
        "query_count": len(queries),
        "engine_count": len(engines),
        "elapsed_ms": round(elapsed_ms, 3),
        "attempts": attempts,
    }
    if job is not None:
        result["workflow"] = {
            "state": job.get("state"),
            "stop_reason": job.get("stop_reason"),
            "caller_completed": job.get("caller_completed"),
            "budgets": job.get("budgets"),
            "coverage": job.get("coverage"),
        }
    return result


def validate_pair_plans(entries: list[dict[str, Any]], records_dir: Path) -> None:
    pairs: dict[tuple[str, int], list[tuple[str, str]]] = {}
    for entry in entries:
        record, _ = load_w10_record(records_dir, entry)
        key = (entry["case_id"], entry["repetition"])
        pairs.setdefault(key, []).append(
            (entry["arm"], digest(json.dumps(frozen_queries(record))))
        )
    for key, values in pairs.items():
        if {arm for arm, _ in values} != {"flat_http", "recorded_continuation"}:
            raise ValueError(f"pair {key} does not contain both W11 arms")
        if len({plan for _, plan in values}) != 1:
            raise ValueError(f"pair {key} does not use one frozen query plan")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-order", type=Path, required=True)
    parser.add_argument("--w10-records", type=Path, required=True)
    parser.add_argument("--scope", type=Path, required=True)
    parser.add_argument("--public-output", type=Path, required=True)
    parser.add_argument("--private-output", type=Path, required=True)
    parser.add_argument("--http-base-url", required=True)
    parser.add_argument("--mcp-endpoint", required=True)
    parser.add_argument("--token-env", default="SLOPSEARX_MCP_AUTH_TOKEN")
    args = parser.parse_args()
    if args.public_output.resolve() == args.private_output.resolve():
        raise ValueError("public and private output directories must differ")
    args.public_output.mkdir(parents=True, exist_ok=True)
    args.private_output.mkdir(parents=True, exist_ok=True, mode=0o700)
    args.private_output.chmod(0o700)
    work_order = json.loads(args.work_order.read_text())
    scope = json.loads(args.scope.read_text())
    if (
        work_order.get("schema_version")
        != "enterprise-evaluation/w11-general-work-order/1"
    ):
        raise ValueError("unsupported W11 work-order schema")
    if not scope.get("scope_equal") or scope.get("dispatches") != 0:
        raise ValueError("scope-equivalence preflight did not pass")
    engines = scope["http_control"]["engines"]
    if engines != scope["research_arm"]["initial_plan_engines"]:
        raise ValueError("W11 arm engine scopes differ")
    entries = work_order["entries"]
    max_results = work_order.get("result_limit")
    if type(max_results) is not int or not 1 <= max_results <= 20:
        raise ValueError("W11 work order lacks the frozen W10 result limit")
    validate_pair_plans(entries, args.w10_records)

    token = os.environ.get(args.token_env, "")
    failures = 0
    with httpx.Client(base_url=args.http_base_url, timeout=180) as http_client:
        for entry in entries:
            name = f"{entry['position']:03d}-{entry['case_id']}-{entry['repetition']}-{entry['arm']}"
            public_path = args.public_output / f"{name}.json"
            private_path = args.private_output / f"{name}.json"
            if public_path.is_file() and private_path.is_file():
                continue
            if public_path.is_file() and not private_path.is_file():
                raise RuntimeError(f"public checkpoint lacks private evidence: {name}")
            record, source_path = load_w10_record(args.w10_records, entry)
            queries = frozen_queries(record)
            if private_path.is_file():
                retained = json.loads(private_path.read_text())
                if isinstance(retained.get("retrieval"), list):
                    atomic_json(
                        public_path,
                        public_record(
                            entry=entry,
                            source_sha256=digest(source_path.read_bytes()),
                            queries=queries,
                            engines=engines,
                            raw=retained["retrieval"],
                            elapsed_ms=float(retained["elapsed_ms"]),
                            job=retained.get("job"),
                        ),
                    )
                else:
                    atomic_json(
                        public_path,
                        {
                            "schema_version": "enterprise-evaluation/w11-general-retrieval/1",
                            "status": "failed",
                            **entry,
                            "error_type": retained.get("error_type", "UnknownError"),
                        },
                    )
                    failures += 1
                continue
            idempotency_key = (
                "w11-" + digest(name + digest(source_path.read_bytes()))[:32]
            )
            started = time.monotonic()
            try:
                job = None
                if entry["arm"] == "flat_http":
                    raw = flat_http_searches(
                        http_client, queries, engines, max_results=max_results
                    )
                elif entry["arm"] == "recorded_continuation":
                    if not token:
                        raise ValueError("MCP bearer token must not be empty")
                    with W11McpClient(
                        args.mcp_endpoint, token, timeout_seconds=180
                    ) as mcp:
                        raw, job = recorded_continuation_searches(
                            mcp,
                            question=queries[0],
                            queries=queries,
                            engines=engines,
                            max_results=max_results,
                            idempotency_key=idempotency_key,
                        )
                else:
                    raise ValueError(f"unknown W11 arm: {entry['arm']}")
                elapsed_ms = (time.monotonic() - started) * 1000
                atomic_json(
                    private_path,
                    {
                        "entry": entry,
                        "retrieval": raw,
                        "job": job,
                        "elapsed_ms": elapsed_ms,
                    },
                    mode=0o600,
                )
                atomic_json(
                    public_path,
                    public_record(
                        entry=entry,
                        source_sha256=digest(source_path.read_bytes()),
                        queries=queries,
                        engines=engines,
                        raw=raw,
                        elapsed_ms=elapsed_ms,
                        job=job,
                    ),
                )
            except Exception as error:
                failures += 1
                atomic_json(
                    private_path,
                    {
                        "entry": entry,
                        "error_type": type(error).__name__,
                        "error": str(error),
                    },
                    mode=0o600,
                )
                atomic_json(
                    public_path,
                    {
                        "schema_version": "enterprise-evaluation/w11-general-retrieval/1",
                        "status": "failed",
                        **entry,
                        "error_type": type(error).__name__,
                    },
                )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
