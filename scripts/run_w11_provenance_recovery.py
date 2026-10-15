#!/usr/bin/env python3
"""Exercise resumable W11 capture and receipt interruption boundaries."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import httpx

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

from scripts.run_w11_general_retrieval import atomic_json
from scripts.run_w11_provenance import digest
from scripts.w11_mcp_client import W11McpClient


class ToolCaller(Protocol):
    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any: ...


class InjectedInterruption(BaseException):
    """Represent abrupt process loss outside ordinary exception handling."""


def _require(value: Any, operation: str) -> dict[str, Any]:
    if not isinstance(value, dict) or "error" in value:
        code = (
            (value.get("error") or {}).get("code", "invalid_response")
            if isinstance(value, dict)
            else "invalid_response"
        )
        raise RuntimeError(f"{operation} failed: {code}")
    return value


def _capture(api: httpx.Client, handoff: dict[str, Any]) -> dict[str, Any]:
    if handoff.get("eligible") is not True or not isinstance(handoff.get("url"), str):
        return {
            "status": "failed",
            "failure_code": "handoff_ineligible",
            "failure_message": str(handoff.get("url_status", "unknown")),
        }
    try:
        response = api.post(
            "/v2/scrape", json={"url": handoff["url"], "formats": ["markdown"]}
        )
        response.raise_for_status()
        payload = response.json()
        markdown = str((payload.get("data") or {}).get("markdown", ""))
        if not payload.get("success") or not markdown:
            raise RuntimeError("empty_scrape")
        content_sha256 = digest(markdown)
        metadata = (payload.get("data") or {}).get("metadata") or {}
        return {
            "status": "succeeded",
            "captured_at": datetime.now(UTC).isoformat(),
            "final_url": metadata.get("sourceURL") or handoff["url"],
            "content_sha256": content_sha256,
            "capture_ref": f"urn:sha256:{content_sha256}",
            "passage_refs": [
                {
                    "ref": f"urn:sha256:{content_sha256}#full",
                    "label": "reviewed capture",
                }
            ],
            "markdown": markdown,
        }
    except Exception as error:
        return {
            "status": "failed",
            "failure_code": "scrape_failed",
            "failure_message": type(error).__name__,
        }


def run_recovery_case(
    client: ToolCaller,
    api: httpx.Client,
    *,
    query: str,
    engines: list[str],
    case_id: str,
    checkpoint: dict[str, Any],
    save: Callable[[dict[str, Any]], None],
    interrupt_after: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Resume one result across capture and receipt boundaries."""
    audit = checkpoint.setdefault(
        "audit",
        {
            "search_dispatches": 0,
            "result_reads": 0,
            "capture_attempts": 0,
            "receipt_submissions": 0,
        },
    )
    if "search" not in checkpoint:
        audit["search_dispatches"] += 1
        search = _require(
            client.call_tool(
                "slopsearx_search",
                {
                    "query": query,
                    "engines": engines,
                    "max_results": 1,
                    "include": ["results", "engine_status"],
                },
            ),
            "search",
        )
        cards = search.get("results") or []
        if not cards or not cards[0].get("result_id"):
            raise RuntimeError("search returned no stable result")
        checkpoint.update({"search": search, "result_id": str(cards[0]["result_id"])})
        save(checkpoint)

    result_id = str(checkpoint["result_id"])
    if "expanded" not in checkpoint:
        audit["result_reads"] += 1
        expanded = _require(
            client.call_tool("slopsearx_read_result", {"result_id": result_id}),
            "read_result",
        )
        handoff = expanded.get("retrieval")
        if not isinstance(handoff, dict) or handoff.get("result_id") != result_id:
            raise RuntimeError("expanded result omitted its retrieval handoff")
        checkpoint["expanded"] = expanded
        save(checkpoint)

    if "capture" not in checkpoint:
        audit["capture_attempts"] += 1
        save(checkpoint)
        checkpoint["capture"] = _capture(api, checkpoint["expanded"]["retrieval"])
        save(checkpoint)

    capture = checkpoint["capture"]
    receipt_args = {
        "result_id": result_id,
        "retriever": "groktocrawl-x-w11",
        "idempotency_key": f"w11-recovery-{digest(f'{case_id}:{result_id}')[:32]}",
        **{key: value for key, value in capture.items() if key != "markdown"},
    }
    if "receipt" not in checkpoint:
        audit["receipt_submissions"] += 1
        receipt = _require(
            client.call_tool("slopsearx_submit_retrieval_receipt", receipt_args),
            "submit_receipt",
        )
        if receipt.get("state") != "created":
            raise RuntimeError("first receipt submission was not created")
        checkpoint["receipt"] = receipt
        save(checkpoint)
        if interrupt_after == "receipt":
            raise InjectedInterruption("after_receipt_submission")

    audit["receipt_submissions"] += 1
    replay = _require(
        client.call_tool("slopsearx_submit_retrieval_receipt", receipt_args),
        "replay_receipt",
    )
    retained = _require(
        client.call_tool(
            "slopsearx_read_retrieval_receipts", {"result_id": result_id, "limit": 20}
        ),
        "read_receipts",
    )
    manifest = _require(
        client.call_tool(
            "slopsearx_export_research_manifest", {"result_ids": [result_id]}
        ),
        "export_manifest",
    )
    first_id = (checkpoint["receipt"].get("receipt") or {}).get("receipt_id")
    replay_id = (replay.get("receipt") or {}).get("receipt_id")
    public = {
        "schema_version": "enterprise-evaluation/w11-provenance-recovery/1",
        "case_id": case_id,
        "query_sha256": digest(query),
        "engine_scope_sha256": digest(json.dumps(engines, separators=(",", ":"))),
        "result_id_sha256": digest(result_id),
        "capture_status": capture.get("status"),
        "audit": dict(audit),
        "receipt_replayed": replay.get("state") == "replayed",
        "receipt_identity_stable": bool(first_id) and first_id == replay_id,
        "retained_receipt_count": retained.get("total"),
        "manifest_linked": bool(manifest.get("items"))
        and manifest["items"][0].get("result_id") == result_id,
        "observations_verified": manifest.get("observations_verified"),
    }
    public["hard_gate_passed"] = bool(
        audit["search_dispatches"] == 1
        and audit["result_reads"] == 1
        and audit["capture_attempts"] in {1, 2}
        and public["receipt_replayed"]
        and public["receipt_identity_stable"]
        and public["retained_receipt_count"] == 1
        and public["manifest_linked"]
        and public["observations_verified"] is False
    )
    checkpoint["replay"] = replay
    checkpoint["retained"] = retained
    checkpoint["manifest"] = manifest
    save(checkpoint)
    return public, checkpoint


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--api-base-url", required=True)
    parser.add_argument("--query", required=True)
    parser.add_argument("--engines", required=True)
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--public-output", type=Path, required=True)
    parser.add_argument("--token-env", default="SLOPSEARX_MCP_AUTH_TOKEN")
    parser.add_argument("--interrupt-after", choices=["receipt"])
    args = parser.parse_args()
    engines = [value.strip() for value in args.engines.split(",") if value.strip()]
    if not engines:
        parser.error("--engines must contain at least one engine")
    if args.checkpoint.resolve() == args.public_output.resolve():
        parser.error("checkpoint and public output must be different files")
    checkpoint = (
        json.loads(args.checkpoint.read_text()) if args.checkpoint.is_file() else {}
    )

    def save(value: dict[str, Any]) -> None:
        atomic_json(args.checkpoint, value, mode=0o600)

    try:
        with (
            W11McpClient(
                args.endpoint, os.environ.get(args.token_env, ""), timeout_seconds=180
            ) as client,
            httpx.Client(base_url=args.api_base_url, timeout=180) as api,
        ):
            public, _ = run_recovery_case(
                client,
                api,
                query=args.query,
                engines=engines,
                case_id=args.case_id,
                checkpoint=checkpoint,
                save=save,
                interrupt_after=args.interrupt_after,
            )
    except InjectedInterruption as error:
        print(str(error))
        return 75
    atomic_json(args.public_output, public)
    print(
        json.dumps(
            {"hard_gate_passed": public["hard_gate_passed"], "audit": public["audit"]},
            indent=2,
        )
    )
    return 0 if public["hard_gate_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
