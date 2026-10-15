#!/usr/bin/env python3
"""Run a representative isolated W11 artifact-composition journey."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Protocol

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

from scripts.run_w11_general_retrieval import atomic_json
from scripts.run_w11_provenance import digest
from scripts.w11_mcp_client import W11McpClient


class ToolCaller(Protocol):
    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any: ...


def require(value: Any, operation: str) -> dict[str, Any]:
    if not isinstance(value, dict) or "error" in value:
        code = (
            (value.get("error") or {}).get("code", "invalid_response")
            if isinstance(value, dict)
            else "invalid_response"
        )
        raise RuntimeError(f"{operation} failed: {code}")
    return value


def run_case(
    client: ToolCaller,
    *,
    query: str,
    followup_query: str,
    engines: list[str],
    idempotency_key: str,
    timeout_seconds: float = 120,
) -> tuple[dict[str, Any], dict[str, Any]]:
    search = require(
        client.call_tool("slopsearx_search", {"query": query, "engines": engines}),
        "search",
    )
    source = ((search.get("meta") or {}).get("artifact"))
    if not isinstance(source, dict):
        raise RuntimeError("search did not return an artifact reference")
    arguments = {
        "question": query,
        "initial_plan": [{"query": followup_query, "engines": engines}],
        "max_queries": 1,
        "max_attempts": 1,
        "max_engine_attempts": len(engines),
        "max_results": 10,
        "idempotency_key": idempotency_key,
        "source": source,
    }
    started = require(client.call_tool("slopsearx_start_research", arguments), "start_research")
    replay = require(client.call_tool("slopsearx_start_research", arguments), "replay_research")
    deadline = time.monotonic() + timeout_seconds
    while True:
        job = require(
            client.call_tool("slopsearx_get_job", {"job_id": started["job_id"]}),
            "get_job",
        )
        if job.get("state") not in {"queued", "running"}:
            break
        if time.monotonic() >= deadline:
            raise TimeoutError("composed research did not reach a terminal state")
        time.sleep(1)
    artifact = job.get("artifact") or started.get("artifact")
    manifest = require(
        client.call_tool("slopsearx_export_research_manifest", {"source": artifact}),
        "export_manifest",
    )
    lineage = require(
        client.call_tool(
            "slopsearx_get_artifact_lineage",
            {"artifact": artifact, "direction": "outgoing"},
        ),
        "read_lineage",
    )
    attempts = [
        attempt
        for planned in job.get("queries") or []
        for attempt in planned.get("attempts") or []
    ]
    lineage_edges = lineage.get("edges") or []
    public = {
        "schema_version": "enterprise-evaluation/w11-composition-live/1",
        "query_sha256": digest(query),
        "followup_query_sha256": digest(followup_query),
        "engine_count": len(engines),
        "source_kind": source.get("kind"),
        "source_preserved": started.get("source", {}).get("artifact") == source,
        "same_job_on_replay": started.get("job_id") == replay.get("job_id"),
        "terminal_state": job.get("state"),
        "attempt_count": len(attempts),
        "manifest_item_count": len(manifest.get("items") or []),
        "manifest_source_preserved": manifest.get("source", {}).get("artifact") == artifact,
        "derived_from_source": any(
            edge.get("relation") == "derived_from" and edge.get("to") == source
            for edge in lineage_edges
        ),
        "manifest_disclaims_verification": manifest.get("observations_verified") is False,
    }
    public["hard_gate_passed"] = bool(
        public["source_kind"] == "snapshot"
        and public["source_preserved"]
        and public["same_job_on_replay"]
        and public["terminal_state"] in {"completed", "succeeded"}
        and public["attempt_count"] == 1
        and public["manifest_item_count"] >= 1
        and public["manifest_source_preserved"]
        and public["derived_from_source"]
        and public["manifest_disclaims_verification"]
    )
    return public, {
        "search": search,
        "started": started,
        "replay": replay,
        "job": job,
        "manifest": manifest,
        "lineage": lineage,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--query", required=True)
    parser.add_argument("--followup-query", required=True)
    parser.add_argument("--engines", required=True)
    parser.add_argument("--idempotency-key", required=True)
    parser.add_argument("--public-output", type=Path, required=True)
    parser.add_argument("--private-output", type=Path, required=True)
    parser.add_argument("--token-env", default="SLOPSEARX_MCP_AUTH_TOKEN")
    args = parser.parse_args()
    if args.public_output.resolve() == args.private_output.resolve():
        parser.error("public and private outputs must differ")
    engines = [item.strip() for item in args.engines.split(",") if item.strip()]
    if not engines:
        parser.error("at least one engine is required")
    with W11McpClient(args.endpoint, os.environ.get(args.token_env, ""), timeout_seconds=180) as client:
        public, private = run_case(
            client,
            query=args.query,
            followup_query=args.followup_query,
            engines=engines,
            idempotency_key=args.idempotency_key,
        )
    atomic_json(args.private_output, private, mode=0o600)
    atomic_json(args.public_output, public)
    print(json.dumps({"hard_gate_passed": public["hard_gate_passed"]}, indent=2))
    return 0 if public["hard_gate_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
