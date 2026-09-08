#!/usr/bin/env python3
"""Verify every expressible agent-svc API endpoint has an MCP tool.

Reads agent-svc/agent/routes/*.py for ``@router.*`` decorators and
cross-references each ``/v2`` path against the tool surface in
mcp-svc/mcp_server.py.  Exits non-zero if a non-exempted endpoint has
no MCP tool, so the MCP surface cannot silently drift behind the API
(the way the CLI surface is guarded by check-cli-coverage.py).

Usage:
    python3 scripts/check-mcp-coverage.py

Exit codes:
    0 — all endpoints covered or exempted
    1 — one or more gaps found
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
API_DIR = REPO_ROOT / "agent-svc" / "agent" / "routes"
MCP_SERVER = REPO_ROOT / "mcp-svc" / "mcp_server.py"

# Endpoints that intentionally have no MCP tool.  Each entry is documented
# with the reason it is not expressible as (or not yet wired to) a tool.
# Backlog entries mirror the CLI coverage exemptions in check-cli-coverage.py.
EXEMPT: dict[str, str] = {
    "POST /v1/search": "Legacy v1 endpoint — superseded by /v2/search.",
    "PUT /v2/parse/upload/{upload_id}": (
        "Two-phase upload flow — internal plumbing; POST /v2/parse covers parsing."
    ),
    "GET /v2/crawl/{job_id}/stream": (
        "SSE streaming — consumed incrementally; tools expose create + status polling."
    ),
    "POST /v2/crawl/params-preview": (
        "Internal helper for NL-parameter validation; crawl tool accepts prompt."
    ),
    "POST /v2/research-memory/query": "Research memory — backlog; MCP TBD.",
    "POST /v2/research-memory/store": "Research memory — backlog; MCP TBD.",
    "DELETE /v2/research-memory/{artifact_id}": ("Research memory — backlog; MCP TBD."),
    "GET /v2/memory/{memory_id}": "Research memory — backlog; MCP TBD.",
    "DELETE /v2/memory/{memory_id}": "Research memory — backlog; MCP TBD.",
    "POST /v2/memory/sweep": "Research memory — backlog; MCP TBD.",
    "POST /v2/memory/batch/query": "Research memory — backlog; MCP TBD.",
    "POST /v2/memory/batch/store": "Research memory — backlog; MCP TBD.",
}

# Map each expressible API path to the MCP tool that covers it.
PATH_TO_MCP_TOOL: dict[str, str] = {
    "GET /v2/activity": "get_activity",
    "POST /v2/agent": "agent",
    "GET /v2/agent/{job_id}": "get_agent_status",
    "DELETE /v2/agent/{job_id}": "cancel_agent",
    "POST /v2/agent/plan": "create_research_plan",
    "GET /v2/agent/plan/{plan_id}": "get_research_plan",
    "POST /v2/agent/execute": "execute_research_plan",
    "POST /v2/answer": "answer",
    "POST /v2/batch/scrape": "batch_scrape",
    "GET /v2/batch/scrape/{job_id}": "get_batch_scrape_status",
    "DELETE /v2/batch/scrape/{job_id}": "cancel_batch_scrape",
    "GET /v2/batch/scrape/{job_id}/errors": "get_batch_scrape_errors",
    "POST /v2/browser": "create_browser_session",
    "GET /v2/browser": "list_browser_sessions",
    "POST /v2/browser/{session_id}/execute": "browser_execute",
    "DELETE /v2/browser/{session_id}": "destroy_browser_session",
    "POST /v2/citations/resolve": "resolve_citations",
    "POST /v2/crawl": "crawl",
    "GET /v2/crawl/active": "get_active_crawls",
    "GET /v2/crawl/{job_id}": "get_crawl_status",
    "DELETE /v2/crawl/{job_id}": "cancel_crawl",
    "GET /v2/crawl/{job_id}/errors": "get_crawl_errors",
    "POST /v2/enrich": "enrich",
    "POST /v2/extract": "extract",
    "GET /v2/extract/{job_id}": "get_extract_status",
    "POST /v2/find-similar": "find_similar",
    "POST /v2/generate-llmstxt": "generate_llmstxt",
    "GET /v2/generate-llmstxt/{job_id}": "get_llmstxt_status",
    "POST /v2/map": "map",
    "POST /v2/monitor": "create_monitor",
    "GET /v2/monitor": "list_monitors",
    "GET /v2/monitor/{monitor_id}": "get_monitor",
    "PATCH /v2/monitor/{monitor_id}": "update_monitor",
    "DELETE /v2/monitor/{monitor_id}": "delete_monitor",
    "POST /v2/monitor/{monitor_id}/run": "run_monitor",
    "POST /v2/parse": "parse",
    "POST /v2/scrape": "scrape",
    "POST /v2/search": "search",
    "POST /v2/session/create": "create_research_session",
    "POST /v2/session/{session_id}/step": "research_session_step",
    "GET /v2/session/{session_id}": "get_research_session",
    "POST /v2/session/{session_id}/export": "export_research_session",
    "DELETE /v2/session/{session_id}": "delete_research_session",
    "POST /v2/session/{session_id}/resolve": "resolve_research_session",
}


def extract_api_endpoints() -> list[str]:
    """Return all ``METHOD /v2/...`` paths found across routes/."""
    if not API_DIR.is_dir():
        print(f"ERROR: Routes directory not found: {API_DIR}")
        sys.exit(1)

    endpoints: list[str] = []
    for py_file in sorted(API_DIR.glob("*.py")):
        if py_file.name.startswith("_"):
            continue  # Skip __init__.py, _helpers.py
        text = py_file.read_text()
        for m in re.finditer(
            r"@router\.(get|post|put|patch|delete)\s*\(\s*\"([^\"]+)\"",
            text,
        ):
            method, path = m.group(1).upper(), m.group(2)
            if path.startswith("/v2/"):
                endpoints.append(f"{method} {path}")
            elif path.startswith("/v1/"):
                endpoints.append(f"{method} {path}")
    return sorted(set(endpoints))


def extract_mcp_tools() -> set[str]:
    """Return all tool names defined in mcp_server.py."""
    if not MCP_SERVER.is_file():
        print(f"ERROR: MCP server file not found: {MCP_SERVER}")
        sys.exit(1)

    text = MCP_SERVER.read_text()
    tools: set[str] = set()
    for m in re.finditer(r"async def (\w+)\s*\(", text):
        name = m.group(1)
        if name.startswith("_"):
            continue  # Skip helpers (_check_agent_svc, _health_endpoint, ...)
        tools.add(name)
    return tools


def main() -> int:
    endpoints = extract_api_endpoints()
    if not endpoints:
        print("ERROR: No API endpoints found — check the route regex")
        return 1

    tools = extract_mcp_tools()
    if not tools:
        print("ERROR: No MCP tools found — check the tool regex")
        return 1

    # Sanity: every mapped endpoint must resolve to a real tool.
    unmapped = [path for path, tool in PATH_TO_MCP_TOOL.items() if tool not in tools]
    if unmapped:
        print(f"❌ PATH_TO_MCP_TOOL references missing tool(s): {unmapped}")
        return 1

    gaps: list[str] = []
    for endpoint in endpoints:
        if endpoint in EXEMPT:
            continue
        tool = PATH_TO_MCP_TOOL.get(endpoint)
        if tool is None:
            gaps.append(f"{endpoint}  (no mapping in PATH_TO_MCP_TOOL)")
        elif tool not in tools:
            gaps.append(f"{endpoint}  (expected tool '{tool}' not defined)")

    if not gaps:
        exempt_count = len([e for e in endpoints if e in EXEMPT])
        covered = len(endpoints) - exempt_count
        print(
            f"✅ All {len(endpoints)} API endpoints have MCP coverage "
            f"({covered} tools + {exempt_count} exempted)"
        )
        return 0

    print(f"❌ {len(gaps)} API endpoint(s) missing MCP coverage:\n")
    for g in gaps:
        print(f"   {g}")
    print()
    print("To fix:")
    print("  1. Add an MCP tool for the missing endpoint, or")
    print("  2. Add the endpoint to EXEMPT in scripts/check-mcp-coverage.py")
    print("     (only for infrastructure/internal/backlog endpoints)")
    print()
    print(
        "Policy: the MCP surface must not silently drift behind agent-svc. "
        "See docs/adr/0042-mcp-server-architecture.md."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
