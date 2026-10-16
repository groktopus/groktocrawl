#!/usr/bin/env python3
"""Run and evaluate an isolated W11 dependency-dossier workflow."""

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
        code = (value.get("error") or {}).get("code", "invalid_response") if isinstance(value, dict) else "invalid_response"
        raise RuntimeError(f"{operation} failed: {code}")
    return value


def assess(
    start: dict[str, Any],
    replay: dict[str, Any],
    report: dict[str, Any],
    reread: dict[str, Any],
) -> dict[str, Any]:
    sections = report.get("sections") or {}
    section_states = {name: value.get("state") for name, value in sections.items()}
    missing = report.get("missing_source_coverage") or []
    missing_sections = {item.get("section") for item in missing}
    expected_missing = {
        name for name, state in section_states.items() if state not in {"available", "empty"}
    }
    budget = report.get("budget") or {}
    package = report.get("resolved_package_identity") or {}
    repository = report.get("repository_identity") or {}
    advisory = (sections.get("advisory_leads") or {}).get("applicability") or {}
    limitations = report.get("limitations") or []
    report_digest = digest(json.dumps(report, sort_keys=True, separators=(",", ":")))
    reread_digest = digest(json.dumps(reread, sort_keys=True, separators=(",", ":")))
    public = {
        "schema_version": "enterprise-evaluation/w11-dependency-dossier-live/1",
        "same_job_on_start_replay": start.get("job_id") == replay.get("job_id"),
        "start_replayed": replay.get("replay") is True,
        "terminal_state": report.get("state"),
        "partial": report.get("partial"),
        "package_resolution_status": package.get("status"),
        "version_match": package.get("version_match"),
        "observed_version_count": len(package.get("observed_versions") or []),
        "repository_status": repository.get("status"),
        "repository_basis": repository.get("basis"),
        "package_ownership_proven": repository.get("proven_package_ownership"),
        "advisory_applicability": advisory.get("status"),
        "section_states": section_states,
        "missing_sections": sorted(str(item) for item in missing_sections),
        "missing_coverage_consistent": missing_sections == expected_missing,
        "adapter_calls_used": budget.get("used_adapter_calls"),
        "adapter_calls_limit": budget.get("max_adapter_calls"),
        "captured_results": budget.get("captured_results"),
        "result_limit": budget.get("max_results"),
        "limitation_count": len(limitations),
        "report_stable_on_reread": report_digest == reread_digest,
        "report_sha256": report_digest,
    }
    public["hard_gate_passed"] = bool(
        public["same_job_on_start_replay"]
        and public["start_replayed"]
        and public["terminal_state"] in {"succeeded", "partial"}
        and public["package_resolution_status"] == "resolved"
        and public["version_match"] in {"exact", "different", "unknown"}
        and public["repository_status"] in {"requested", "unresolved", "unverified_metadata", "ambiguous"}
        and public["package_ownership_proven"] is False
        and public["advisory_applicability"] == "not_evaluated"
        and set(section_states) == {"package_information", "repository_records", "advisory_leads"}
        and all(isinstance(value.get("coverage"), list) for value in sections.values())
        and public["missing_coverage_consistent"]
        and type(public["adapter_calls_used"]) is int
        and type(public["adapter_calls_limit"]) is int
        and public["adapter_calls_used"] <= public["adapter_calls_limit"]
        and type(public["captured_results"]) is int
        and type(public["result_limit"]) is int
        and public["captured_results"] <= public["result_limit"]
        and public["limitation_count"] >= 3
        and public["report_stable_on_reread"]
    )
    return public


def run_case(
    client: ToolCaller,
    *,
    ecosystem: str,
    package: str,
    version: str | None,
    repository: str | None,
    idempotency_key: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    arguments = {
        "ecosystem": ecosystem,
        "package": package,
        "version": version,
        "repository": repository,
        "idempotency_key": idempotency_key,
    }
    start = require(client.call_tool("slopsearx_start_dependency_dossier", arguments), "start_dossier")
    replay = require(client.call_tool("slopsearx_start_dependency_dossier", arguments), "replay_dossier")
    job_id = str(start["job_id"])
    deadline = time.monotonic() + 120
    while True:
        report = require(
            client.call_tool("slopsearx_get_dependency_dossier", {"job_id": job_id, "max_results": 5}),
            "get_dossier",
        )
        if report.get("state") not in {"queued", "running"}:
            break
        if time.monotonic() >= deadline:
            raise TimeoutError("dependency dossier did not reach a terminal state")
        time.sleep(1)
    reread = require(
        client.call_tool("slopsearx_get_dependency_dossier", {"job_id": job_id, "max_results": 5}),
        "reread_dossier",
    )
    public = assess(start, replay, report, reread)
    public.update(
        {
            "ecosystem": ecosystem,
            "package_sha256": digest(package.casefold()),
            "requested_version_sha256": digest(version) if version else None,
            "repository_sha256": digest(repository.casefold()) if repository else None,
        }
    )
    return public, {"start": start, "replay": replay, "report": report, "reread": reread}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--ecosystem", required=True)
    parser.add_argument("--package", required=True)
    parser.add_argument("--version")
    parser.add_argument("--repository")
    parser.add_argument("--idempotency-key", required=True)
    parser.add_argument("--public-output", type=Path, required=True)
    parser.add_argument("--private-output", type=Path, required=True)
    parser.add_argument("--token-env", default="SLOPSEARX_MCP_AUTH_TOKEN")
    args = parser.parse_args()
    if args.public_output.resolve() == args.private_output.resolve():
        parser.error("public and private outputs must differ")
    with W11McpClient(args.endpoint, os.environ.get(args.token_env, ""), timeout_seconds=180) as client:
        public, private = run_case(
            client,
            ecosystem=args.ecosystem,
            package=args.package,
            version=args.version,
            repository=args.repository,
            idempotency_key=args.idempotency_key,
        )
    atomic_json(args.private_output, private, mode=0o600)
    atomic_json(args.public_output, public)
    print(json.dumps({"hard_gate_passed": public["hard_gate_passed"], "state": public["terminal_state"]}, indent=2))
    return 0 if public["hard_gate_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
