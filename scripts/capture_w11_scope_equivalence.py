#!/usr/bin/env python3
"""Freeze one engine scope usable by both W11 HTTP and research transports."""

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

from scripts.w11_mcp_client import W11McpClient


class ToolCaller(Protocol):
    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any: ...


def capture(client: ToolCaller, *, query: str) -> dict[str, Any]:
    category = client.call_tool(
        "slopsearx_explain_search_scope",
        {"query": query, "categories": ["general"]},
    )
    if not isinstance(category, dict) or category.get("error"):
        raise ValueError("category scope preview failed")
    engines = category.get("selected_engines")
    if not isinstance(engines, list) or not engines or not all(isinstance(x, str) for x in engines):
        raise ValueError("category scope preview returned no valid engines")
    explicit = client.call_tool(
        "slopsearx_explain_search_scope",
        {"query": query, "engines": engines},
    )
    if not isinstance(explicit, dict) or explicit.get("selected_engines") != engines:
        raise ValueError("explicit engine scope does not reproduce category scope")
    return {
        "schema_version": "enterprise-evaluation/w11-scope-equivalence/1",
        "http_control": {"categories": ["general"], "engines": engines},
        "research_arm": {"initial_plan_engines": engines},
        "selected_engine_count": len(engines),
        "scope_equal": True,
        "dispatches": 0,
        "note": "Both arms must use the frozen explicit engine list during measurement.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--token-env", default="SLOPSEARX_MCP_AUTH_TOKEN")
    parser.add_argument("--query", default="software engineering research")
    args = parser.parse_args()
    token = os.environ.get(args.token_env, "")
    with W11McpClient(args.endpoint, token) as client:
        result = capture(client, query=args.query)
    result["probe_query_sha256"] = hashlib.sha256(args.query.encode()).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"scope_equal": True, "selected_engine_count": result["selected_engine_count"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
