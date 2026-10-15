#!/usr/bin/env python3
"""Evaluate W11 entity projection without treating identity as corroboration."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Protocol

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

from scripts.run_w11_general_retrieval import atomic_json
from scripts.w11_mcp_client import W11McpClient


class ToolCaller(Protocol):
    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any: ...


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def require_object(value: Any, operation: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RuntimeError(f"{operation} returned no object")
    if "error" in value:
        error = value["error"]
        code = error.get("code", "unknown") if isinstance(error, dict) else "unknown"
        raise RuntimeError(f"{operation} failed: {code}")
    return value


def run_case(client: ToolCaller, case: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    search = require_object(
        client.call_tool(
            "slopsearx_search",
            {
                "query": case["query"],
                "engines": case["engines"],
                "max_results": case.get("max_results", 20),
                "include": ["results", "engine_status"],
            },
        ),
        "search",
    )
    cards = search.get("results")
    cursor = (search.get("meta") or {}).get("cursor")
    if not isinstance(cards, list) or not isinstance(cursor, str) or not cursor:
        raise RuntimeError("search omitted results or snapshot cursor")
    result_ids = {
        str(card.get("result_id"))
        for card in cards
        if isinstance(card, dict) and card.get("result_id")
    }
    if len(result_ids) != len(cards):
        raise RuntimeError("search result identities are missing or duplicated")

    flat_before = require_object(
        client.call_tool("slopsearx_read_results", {"cursor": cursor, "max_results": 50}),
        "read_results_before",
    )
    first = require_object(
        client.call_tool(
            "slopsearx_read_entities",
            {"cursor": cursor, "page": 1, "max_results": 50, "version": 2},
        ),
        "read_entities",
    )
    entities = list(first.get("entities") or [])
    meta = first.get("meta") or {}
    page = 2
    while meta.get("has_more") is True:
        current = require_object(
            client.call_tool(
                "slopsearx_read_entities",
                {"cursor": cursor, "page": page, "max_results": 50, "version": 2},
            ),
            "read_entities",
        )
        entities.extend(current.get("entities") or [])
        meta = current.get("meta") or {}
        page += 1
    relationships = list(first.get("relationships") or [])
    flat_after = require_object(
        client.call_tool("slopsearx_read_results", {"cursor": cursor, "max_results": 50}),
        "read_results_after",
    )

    member_ids = {
        str(result_id)
        for entity in entities
        if isinstance(entity, dict)
        for result_id in entity.get("result_ids", [])
    }
    entity_ids = {
        str(entity["entity_id"])
        for entity in entities
        if isinstance(entity, dict) and entity.get("entity_id")
    }
    allowed_relations = {
        "source_reported_alias",
        "candidate_repository",
        "source_reported_advisory",
    }
    relationships_valid = all(
        isinstance(edge, dict)
        and edge.get("relation") in allowed_relations
        and edge.get("from_entity_id") in entity_ids
        and edge.get("to_entity_id") in entity_ids
        for edge in relationships
    )
    grouped_members = sum(
        max(0, len(entity.get("result_ids", [])) - 1)
        for entity in entities
        if isinstance(entity, dict) and entity.get("entity_id")
    )
    flat_unchanged = digest(flat_before) == digest(flat_after)
    conservation = member_ids == result_ids
    public_entities = [
        {
            "entity_id_sha256": digest(entity.get("entity_id")) if entity.get("entity_id") else None,
            "namespace": entity.get("namespace"),
            "member_count": len(entity.get("result_ids", [])),
            "conflicting_fields": sorted(entity.get("conflicting_fields", [])),
            "unresolved": entity.get("entity_id") is None,
        }
        for entity in entities
        if isinstance(entity, dict)
    ]
    public = {
        "case_id": case["case_id"],
        "query_sha256": digest(case["query"]),
        "engine_scope_sha256": digest(case["engines"]),
        "result_count": len(result_ids),
        "entity_count": len(entities),
        "entities": public_entities,
        "relationship_types": sorted(
            {str(edge.get("relation")) for edge in relationships if isinstance(edge, dict)}
        ),
        "potential_repeated_acquisitions": grouped_members,
        "automatic_fetch_suppression_authorized": False,
        "result_conservation_passed": conservation,
        "relationships_valid": relationships_valid,
        "flat_snapshot_unchanged": flat_unchanged,
    }
    public["hard_gate_passed"] = bool(result_ids) and all(
        (conservation, relationships_valid, flat_unchanged)
    )
    return public, {
        "case": case,
        "search": search,
        "flat_before": flat_before,
        "projection": {"entities": entities, "relationships": relationships},
        "flat_after": flat_after,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--public-output", type=Path, required=True)
    parser.add_argument("--private-output", type=Path, required=True)
    parser.add_argument("--token-env", default="SLOPSEARX_MCP_AUTH_TOKEN")
    args = parser.parse_args()
    if args.public_output.resolve() == args.private_output.resolve():
        parser.error("public and private outputs must be different files")
    cases = json.loads(args.cases.read_text())
    if not isinstance(cases, list) or not cases:
        parser.error("cases must be a non-empty JSON list")
    public_cases = []
    private_cases = []
    with W11McpClient(args.endpoint, os.environ.get(args.token_env, ""), timeout_seconds=180) as client:
        for case in cases:
            public, private = run_case(client, case)
            public_cases.append(public)
            private_cases.append(private)
    public_output = {
        "schema_version": "enterprise-evaluation/w11-entity-projection/1",
        "case_manifest_sha256": digest(cases),
        "cases": public_cases,
        "hard_gate_passed": all(case["hard_gate_passed"] for case in public_cases),
        "interpretation": (
            "Entity identity is an organizational view. It does not establish source "
            "independence, corroboration, ownership, applicability, or verification."
        ),
    }
    args.private_output.parent.mkdir(parents=True, exist_ok=True)
    args.private_output.parent.chmod(0o700)
    atomic_json(args.private_output, {"cases": private_cases}, mode=0o600)
    atomic_json(args.public_output, public_output)
    print(json.dumps({"cases": len(public_cases), "hard_gate_passed": public_output["hard_gate_passed"]}))
    return 0 if public_output["hard_gate_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
