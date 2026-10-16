#!/usr/bin/env python3
"""Capture and validate a redacted SlopSearX MCP preflight for W11."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from w11_mcp_client import W11McpClient

SPECIALIST_GRANTS = {
    "dependency_dossier",
    "jobs",
    "research",
    "retrieval_receipts",
    "saved_search_events",
    "saved_searches",
    "science",
    "security",
    "staged_search",
}

REQUIRED_TOOLS = {
    "slopsearx_export_research_manifest",
    "slopsearx_extend_research",
    "slopsearx_get_dependency_dossier",
    "slopsearx_get_job",
    "slopsearx_get_service_status",
    "slopsearx_get_staged_search",
    "slopsearx_list_capabilities",
    "slopsearx_preview_staged_search",
    "slopsearx_read_entities",
    "slopsearx_read_result",
    "slopsearx_read_results",
    "slopsearx_read_retrieval_receipts",
    "slopsearx_read_saved_search_events",
    "slopsearx_read_saved_search_reports",
    "slopsearx_retry_research",
    "slopsearx_retry_staged_search",
    "slopsearx_search",
    "slopsearx_search_jobs",
    "slopsearx_search_science",
    "slopsearx_search_security",
    "slopsearx_search_targeted",
    "slopsearx_search_staged",
    "slopsearx_start_dependency_dossier",
    "slopsearx_start_research",
    "slopsearx_submit_retrieval_receipt",
    "slopsearx_update_research",
}

DISABLED_PROBES: dict[str, tuple[str, dict[str, Any]]] = {
    "dependency_dossier": (
        "slopsearx_start_dependency_dossier",
        {"ecosystem": "pypi", "package": "w11-policy-probe"},
    ),
    "research": (
        "slopsearx_start_research",
        {
            "question": "W11 policy probe",
            "max_queries": 1,
            "max_attempts": 1,
            "max_engine_attempts": 1,
            "max_results": 1,
            "subquestions": [{"id": "probe", "question": "Policy probe"}],
            "initial_plan": [
                {
                    "query": "w11 policy probe",
                    "engines": ["wikipedia"],
                    "subquestion_id": "probe",
                    "rationale": "Verify denial before dispatch",
                }
            ],
        },
    ),
    "jobs": ("slopsearx_search_jobs", {"company": "w11-policy-probe"}),
    "retrieval_receipts": (
        "slopsearx_read_retrieval_receipts",
        {"result_id": "w11-policy-probe:0", "limit": 1},
    ),
    "saved_search_events": (
        "slopsearx_read_saved_search_events",
        {"consumer_id": "w11-policy-probe", "limit": 1},
    ),
    "saved_searches": (
        "slopsearx_get_saved_search",
        {"search_id": "w11-policy-probe"},
    ),
    "science": (
        "slopsearx_search_science",
        {"query": "w11 policy probe", "max_results": 1},
    ),
    "security": (
        "slopsearx_search_security",
        {"query": "w11 policy probe", "max_results": 1},
    ),
    "staged_search": (
        "slopsearx_preview_staged_search",
        {
            "query": "w11 policy probe",
            "objectives": {"deadline_ms": 1000, "max_engine_calls": 1},
            "initial_scope": {"engines": ["wikipedia"]},
        },
    ),
}


class PreflightError(RuntimeError):
    """Raised when the target cannot support the declared W11 arm."""


def _digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _grant_sets(value: list[str]) -> set[str]:
    grants = set(value)
    unknown = grants - SPECIALIST_GRANTS
    if unknown:
        raise PreflightError(f"unknown specialist grants: {sorted(unknown)}")
    return grants


def _error_code(payload: Any) -> tuple[str | None, str | None]:
    if not isinstance(payload, dict):
        return None, None
    error = payload.get("error")
    if not isinstance(error, dict):
        return None, None
    code = error.get("code")
    grant = error.get("grant")
    return (
        str(code) if isinstance(code, str) else None,
        str(grant) if isinstance(grant, str) else None,
    )


def _research_execution(status: dict[str, Any]) -> dict[str, Any]:
    value = status.get("research_execution")
    if not isinstance(value, dict):
        return {}
    stable_fields = (
        "mode",
        "lease_ttl_seconds",
        "max_concurrent_jobs",
        "poll_interval_seconds",
        "note",
    )
    return {field: value.get(field) for field in stable_fields if field in value}


def capture_preflight(
    client: W11McpClient,
    *,
    expected_version: str,
    expected_enabled: set[str],
    expected_disabled: set[str],
    target_label: str,
    source_revision: str,
    image_digest: str,
    expected_targeted_sensitive: bool = False,
) -> dict[str, Any]:
    if expected_enabled & expected_disabled:
        raise PreflightError("a grant cannot be both enabled and disabled")
    if expected_enabled | expected_disabled != SPECIALIST_GRANTS:
        raise PreflightError("every specialist grant must have an expected state")

    tools = client.list_tools()
    names = sorted(
        name
        for item in tools
        if isinstance((name := item.get("name")), str)
    )
    missing = sorted(REQUIRED_TOOLS - set(names))
    if missing:
        raise PreflightError(f"required W11 tools are missing: {missing}")

    status = client.call_tool("slopsearx_get_service_status", {})
    if not isinstance(status, dict):
        raise PreflightError("service status is not a JSON object")
    if status.get("version") != expected_version:
        raise PreflightError(
            f"service version {status.get('version')!r} does not match {expected_version!r}"
        )
    specialist = status.get("grants", {}).get("specialist", {})
    if not isinstance(specialist, dict):
        raise PreflightError("service status lacks specialist grants")
    observed_enabled = {
        name for name in SPECIALIST_GRANTS if specialist.get(name) is True
    }
    observed_disabled = SPECIALIST_GRANTS - observed_enabled
    if observed_enabled != expected_enabled or observed_disabled != expected_disabled:
        raise PreflightError(
            "observed specialist grants do not match the declared arm policy"
        )
    targeted_sensitive = status.get("grants", {}).get("targeted_sensitive_allowed")
    if targeted_sensitive is not expected_targeted_sensitive:
        raise PreflightError(
            "targeted-sensitive policy does not match the declared arm policy"
        )

    denial_probes: dict[str, dict[str, str | None]] = {}
    for grant in sorted(expected_disabled):
        tool, arguments = DISABLED_PROBES[grant]
        code, reported_grant = _error_code(client.call_tool(tool, arguments))
        if code != "tool_disabled":
            raise PreflightError(
                f"disabled grant {grant} returned {code!r} instead of 'tool_disabled'"
            )
        denial_probes[grant] = {
            "tool": tool,
            "error_code": code,
            "reported_grant": reported_grant,
        }

    frozen = {
        "schema_version": "enterprise-evaluation/w11-preflight/1",
        "target_label": target_label,
        "slopsearx": {
            "version": status.get("version"),
            "contract_version": status.get("contract_version"),
            "source_revision": source_revision,
            "image_digest": image_digest,
            "active_engines": status.get("active_engines"),
            "grants": {
                "enabled": sorted(observed_enabled),
                "disabled": sorted(observed_disabled),
                "targeted_sensitive_allowed": targeted_sensitive,
            },
            "policy_bounds": status.get("policy_bounds"),
            "research_execution": _research_execution(status),
            "snapshots_available": status.get("snapshots_available"),
            "job_store_available": status.get("job_store_available"),
            "valkey": status.get("valkey"),
            "workflow_health": status.get("workflow_health"),
        },
        "mcp": {
            "tool_count": len(names),
            "tool_names": names,
            "tool_names_sha256": _digest(names),
            "disabled_grant_probes": denial_probes,
        },
    }
    return {
        **frozen,
        "configuration_sha256": _digest(frozen),
        "captured_at": datetime.now(UTC).isoformat(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--token-env", default="SLOPSEARX_MCP_AUTH_TOKEN")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--target-label", required=True)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--image-digest", required=True)
    parser.add_argument("--expected-version", default="0.5.0")
    parser.add_argument("--enabled-grant", action="append", default=[])
    parser.add_argument("--disabled-grant", action="append", default=[])
    parser.add_argument("--targeted-sensitive-allowed", action="store_true")
    args = parser.parse_args()
    token = os.environ.get(args.token_env, "")
    with W11McpClient(args.endpoint, token) as client:
        record = capture_preflight(
            client,
            expected_version=args.expected_version,
            expected_enabled=_grant_sets(args.enabled_grant),
            expected_disabled=_grant_sets(args.disabled_grant),
            target_label=args.target_label,
            source_revision=args.source_revision,
            image_digest=args.image_digest,
            expected_targeted_sensitive=args.targeted_sensitive_allowed,
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as handle:
        json.dump(record, handle, indent=2, sort_keys=True)
        handle.write("\n")
    print(f"captured W11 preflight {record['configuration_sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
