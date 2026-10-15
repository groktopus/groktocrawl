#!/usr/bin/env python3
"""Verify every API endpoint in agent-svc has a CLI counterpart.

Reads agent-svc/agent/api.py for @router.*("/v2/..." decorators and
cross-references against groktocrawl's dispatch dict. Exits non-zero
if an unexempted endpoint has no CLI handler.

Usage:
    python3 scripts/check-cli-coverage.py

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
CLI_FILE = REPO_ROOT / "groktocrawl"

# Endpoints that don't need CLI coverage.
EXEMPT = frozenset(
    {
        "/health",
        "/v2/agent/execute",  # Plan-execute — in-progress; CLI TBD
        "/v2/citations/resolve",  # Citations — in-progress; CLI TBD
        "/v2/crawl/{job_id}/stream",  # SSE streaming — consumed by browser/portal
        "/v2/crawl/params-preview",  # Internal helper — crawl param validation
        "/v2/activity",  # Diagnostic — health dashboards
        "/v2/memory/batch/query",  # Research memory batch — in-progress; CLI TBD
        "/v2/memory/batch/store",  # Research memory batch — in-progress; CLI TBD
        "/v2/memory/sweep",  # Research memory — in-progress; CLI TBD
        "/v2/memory/{memory_id}",  # Research memory — in-progress; CLI TBD
        "/v2/research-memory/query",  # Research memory — in-progress; CLI TBD
        "/v2/research-memory/store",  # Research memory — in-progress; CLI TBD
        "/v2/research-memory/{artifact_id}",  # Research memory — in-progress; CLI TBD
        "/v2/session/create",  # Sessions — in-progress; CLI TBD
        "/v2/session/{session_id}",  # Sessions — in-progress; CLI TBD
        "/v2/session/{session_id}/export",  # Sessions — in-progress; CLI TBD
        "/v2/session/{session_id}/resolve",  # Sessions — in-progress; CLI TBD
        "/v2/session/{session_id}/step",  # Sessions — in-progress; CLI TBD
    }
)

# Map API base path components to CLI dispatch command names.
# Key: the first path segment after /v2/ (e.g., "batch" for /v2/batch/scrape/...)
# Value: the dispatch dict key in groktocrawl
PATH_TO_CLI_COMMAND: dict[str, str] = {
    "agent": "agent",
    "followup": "followup",
    "answer": "answer",
    "batch": "batch-scrape",
    "browser": "browser",
    "crawl": "crawl",
    "download": "download",
    "enrich": "enrich",
    "extract": "extract",
    "find-similar": "find-similar",
    "generate-llmstxt": "generate-llmstxt",
    "map": "map",
    "monitor": "monitor",
    "parse": "parse",
    "scrape": "scrape",
    "search": "search",
    "session": "document",
}


def extract_api_endpoints() -> list[str]:
    """Return all /v2/... paths found in route decorators across routes/."""
    if not API_DIR.is_dir():
        print(f"ERROR: Routes directory not found: {API_DIR}")
        sys.exit(1)

    paths: list[str] = []
    for py_file in sorted(API_DIR.glob("*.py")):
        if py_file.name.startswith("_"):
            continue  # Skip __init__.py, _helpers.py
        text = py_file.read_text()
        for m in re.finditer(
            r'@router\.(?:get|post|put|patch|delete)\s*\(\s*"([^"]+)"',
            text,
        ):
            path = m.group(1)
            if path.startswith("/v2/"):
                paths.append(path)
    return sorted(set(paths))


def extract_cli_commands() -> set[str]:
    """Return all dispatch dict keys from the CLI script."""
    if not CLI_FILE.is_file():
        print(f"ERROR: CLI file not found: {CLI_FILE}")
        sys.exit(1)

    text = CLI_FILE.read_text()
    # Find the dispatch dict in main(): {"cmd": func, ...}
    dispatch_match = re.search(
        r"dispatch\s*=\s*\{(.*?)\}",
        text,
        re.DOTALL,
    )
    if not dispatch_match:
        print("ERROR: Could not find dispatch dict in CLI file")
        sys.exit(1)

    commands: set[str] = set()
    for m in re.finditer(
        r'"([a-z][a-z0-9_-]*)"\s*:\s*',
        dispatch_match.group(1),
    ):
        commands.add(m.group(1))
    return commands


def path_to_command(path: str) -> str | None:
    """Map an API path to its expected CLI command name."""
    if path == "/v2/session/{session_id}/evidence/{ref_id}":
        return "evidence"
    # Strip leading /v2/ and take the first segment
    if not path.startswith("/v2/"):
        return None
    remainder = path.removeprefix("/v2/")
    first_seg = remainder.split("/")[0]
    return PATH_TO_CLI_COMMAND.get(first_seg)


def main() -> int:
    api_paths = extract_api_endpoints()
    if not api_paths:
        print("ERROR: No /v2/ API endpoints found in api.py — check the route regex")
        return 1

    cli_commands = extract_cli_commands()

    gaps: list[str] = []
    for path in api_paths:
        if path in EXEMPT:
            continue
        cmd = path_to_command(path)
        if cmd is None:
            gaps.append(f"{path}  (no mapping in PATH_TO_CLI_COMMAND)")
        elif cmd not in cli_commands:
            gaps.append(f"{path}  (expected CLI command '{cmd}' not in dispatch dict)")

    if not gaps:
        exempt_count = len([p for p in api_paths if p in EXEMPT])
        print(
            f"✅ All {len(api_paths)} API endpoints have CLI coverage "
            f"(exempted {exempt_count})"
        )
        return 0

    print(f"❌ {len(gaps)} API endpoint(s) missing CLI coverage:\n")
    for g in gaps:
        print(f"   {g}")
    print()
    print("To fix:")
    print("  1. Add a CLI subcommand for the missing endpoint, or")
    print("  2. Add the path to EXEMPT in scripts/check-cli-coverage.py")
    print("     (only for infrastructure/internal endpoints)")
    print()
    print(
        "See ADR-0039 for the full policy: docs/adr/0039-api-cli-surface-ship-together.md"
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
