#!/usr/bin/env python3
"""Run the isolated W11 saved-search lifecycle and event-delivery pilot."""

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


def wait_for_reports(
    client: ToolCaller,
    search_id: str,
    minimum: int,
    *,
    timeout_seconds: float,
    poll_seconds: float = 2,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    while True:
        value = require(
            client.call_tool(
                "slopsearx_read_saved_search_reports",
                {"search_id": search_id, "limit": 20},
            ),
            "read_reports",
        )
        if len(value.get("reports") or []) >= minimum:
            return value
        if time.monotonic() >= deadline:
            raise TimeoutError(f"saved search did not produce {minimum} reports")
        time.sleep(poll_seconds)


def assess(
    *,
    baseline: dict[str, Any],
    compared: dict[str, Any],
    paused: dict[str, Any],
    paused_report_count: int,
    first_events: dict[str, Any],
    repeated_events: dict[str, Any],
    first_ack: dict[str, Any],
    repeated_ack: dict[str, Any],
    after_ack: dict[str, Any],
    resumed: dict[str, Any],
    deleted: dict[str, Any],
) -> dict[str, Any]:
    baseline_reports = baseline.get("reports") or []
    compared_reports = compared.get("reports") or []
    first_items = first_events.get("events") or []
    repeated_items = repeated_events.get("events") or []
    event_ids = [item.get("event_id") for item in first_items]
    repeated_ids = [item.get("event_id") for item in repeated_items]
    statuses = [report.get("status") for report in compared_reports]
    comparison_events = [
        event
        for report in compared_reports
        if report.get("status") == "compared"
        for event in (report.get("events") or [])
    ]
    event_types = [item.get("event_type") for item in first_items]
    public = {
        "schema_version": "enterprise-evaluation/w11-saved-search-live/1",
        "baseline_report_count": len(baseline_reports),
        "comparison_report_count": len(compared_reports),
        "report_statuses": statuses,
        "no_change_event_count": len(comparison_events),
        "paused": paused.get("paused"),
        "paused_report_count": paused_report_count,
        "event_types": event_types,
        "at_least_once_replay_stable": bool(event_ids) and event_ids == repeated_ids,
        "event_ack_stable": first_ack.get("acknowledged_cursor")
        == repeated_ack.get("acknowledged_cursor"),
        "post_ack_event_count": len(after_ack.get("events") or []),
        "resumed": resumed.get("paused") is False,
        "deleted": deleted.get("state") == "deleted",
        "absence_semantics_disclosed": "never a deletion"
        in str(compared.get("note", "")),
    }
    public["hard_gate_passed"] = bool(
        len(baseline_reports) >= 1
        and len(compared_reports) >= 2
        and statuses.count("baseline_initialized") == 1
        and statuses.count("compared") == 1
        and public["no_change_event_count"] == 0
        and public["paused"] is True
        and paused_report_count == len(compared_reports)
        and "definition_paused" in event_types
        and public["at_least_once_replay_stable"]
        and public["event_ack_stable"]
        and public["post_ack_event_count"] == 0
        and public["resumed"]
        and public["deleted"]
        and public["absence_semantics_disclosed"]
    )
    return public


def run_case(
    client: ToolCaller,
    *,
    query: str,
    engines: list[str],
    consumer_id: str,
    interval_seconds: int = 60,
) -> tuple[dict[str, Any], dict[str, Any]]:
    created = require(
        client.call_tool(
            "slopsearx_create_saved_search",
            {
                "query": query,
                "engines": engines,
                "interval_seconds": interval_seconds,
                "retention_seconds": 600,
                "expires_in_seconds": 600,
                "max_results": 5,
                "max_reports": 5,
                "start_immediately": True,
            },
        ),
        "create_saved_search",
    )
    search_id = str(created["search_id"])
    try:
        baseline = wait_for_reports(client, search_id, 1, timeout_seconds=90)
        compared = wait_for_reports(client, search_id, 2, timeout_seconds=90)
        current = require(
            client.call_tool("slopsearx_get_saved_search", {"search_id": search_id}),
            "get_saved_search",
        )
        paused = require(
            client.call_tool(
                "slopsearx_pause_saved_search",
                {
                    "search_id": search_id,
                    "expected_revision": current["revision"],
                    "paused": True,
                },
            ),
            "pause_saved_search",
        )
        time.sleep(interval_seconds + 5)
        during_pause = require(
            client.call_tool(
                "slopsearx_read_saved_search_reports",
                {"search_id": search_id, "limit": 20},
            ),
            "read_paused_reports",
        )
        first_events = require(
            client.call_tool(
                "slopsearx_read_saved_search_events",
                {"consumer_id": consumer_id, "limit": 50},
            ),
            "read_events",
        )
        repeated_events = require(
            client.call_tool(
                "slopsearx_read_saved_search_events",
                {"consumer_id": consumer_id, "limit": 50},
            ),
            "repeat_events",
        )
        cursor = first_events.get("next_cursor")
        if not isinstance(cursor, str) or cursor == "0-0":
            raise RuntimeError(
                "saved-search event stream returned no acknowledgeable cursor"
            )
        first_ack = require(
            client.call_tool(
                "slopsearx_ack_saved_search_events",
                {"consumer_id": consumer_id, "cursor": cursor},
            ),
            "ack_events",
        )
        repeated_ack = require(
            client.call_tool(
                "slopsearx_ack_saved_search_events",
                {"consumer_id": consumer_id, "cursor": cursor},
            ),
            "repeat_ack",
        )
        after_ack = require(
            client.call_tool(
                "slopsearx_read_saved_search_events",
                {"consumer_id": consumer_id, "limit": 50},
            ),
            "read_after_ack",
        )
        resumed = require(
            client.call_tool(
                "slopsearx_pause_saved_search",
                {
                    "search_id": search_id,
                    "expected_revision": paused["revision"],
                    "paused": False,
                },
            ),
            "resume_saved_search",
        )
        deleted = require(
            client.call_tool(
                "slopsearx_delete_saved_search",
                {"search_id": search_id, "expected_revision": resumed["revision"]},
            ),
            "delete_saved_search",
        )
    except BaseException:
        try:
            current = client.call_tool(
                "slopsearx_get_saved_search", {"search_id": search_id}
            )
            if isinstance(current, dict) and "revision" in current:
                client.call_tool(
                    "slopsearx_delete_saved_search",
                    {"search_id": search_id, "expected_revision": current["revision"]},
                )
        finally:
            raise
    public = assess(
        baseline=baseline,
        compared=compared,
        paused=paused,
        paused_report_count=len(during_pause.get("reports") or []),
        first_events=first_events,
        repeated_events=repeated_events,
        first_ack=first_ack,
        repeated_ack=repeated_ack,
        after_ack=after_ack,
        resumed=resumed,
        deleted=deleted,
    )
    public.update(
        {
            "query_sha256": digest(query),
            "engine_scope_sha256": digest(json.dumps(engines, separators=(",", ":"))),
        }
    )
    private = {
        "created": created,
        "baseline": baseline,
        "compared": compared,
        "paused": paused,
        "during_pause": during_pause,
        "first_events": first_events,
        "repeated_events": repeated_events,
        "first_ack": first_ack,
        "repeated_ack": repeated_ack,
        "after_ack": after_ack,
        "resumed": resumed,
        "deleted": deleted,
    }
    return public, private


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--query", required=True)
    parser.add_argument("--engines", required=True)
    parser.add_argument("--consumer-id", required=True)
    parser.add_argument("--public-output", type=Path, required=True)
    parser.add_argument("--private-output", type=Path, required=True)
    parser.add_argument("--token-env", default="SLOPSEARX_MCP_AUTH_TOKEN")
    args = parser.parse_args()
    engines = [item.strip() for item in args.engines.split(",") if item.strip()]
    if not engines:
        parser.error("--engines must contain at least one engine")
    if args.public_output.resolve() == args.private_output.resolve():
        parser.error("public and private outputs must differ")
    with W11McpClient(
        args.endpoint, os.environ.get(args.token_env, ""), timeout_seconds=180
    ) as client:
        public, private = run_case(
            client, query=args.query, engines=engines, consumer_id=args.consumer_id
        )
    atomic_json(args.private_output, private, mode=0o600)
    atomic_json(args.public_output, public)
    print(
        json.dumps(
            {
                "hard_gate_passed": public["hard_gate_passed"],
                "reports": public["comparison_report_count"],
            },
            indent=2,
        )
    )
    return 0 if public["hard_gate_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
