#!/usr/bin/env python3
"""Exercise the W11 discovery-to-capture receipt and manifest boundary."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import httpx

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

from scripts.run_w11_general_retrieval import atomic_json
from scripts.w11_mcp_client import W11McpClient


class ToolCaller(Protocol):
    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any: ...


def digest(value: str | bytes) -> str:
    raw = value.encode() if isinstance(value, str) else value
    return hashlib.sha256(raw).hexdigest()


def _require(value: Any, operation: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RuntimeError(f"{operation} returned no object")
    if "error" in value:
        error = value["error"]
        code = error.get("code", "unknown") if isinstance(error, dict) else "unknown"
        raise RuntimeError(f"{operation} failed: {code}")
    return value


def run_case(
    client: ToolCaller,
    api: httpx.Client,
    *,
    query: str,
    engines: list[str],
    max_results: int,
    case_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    search = _require(
        client.call_tool(
            "slopsearx_search",
            {
                "query": query,
                "engines": engines,
                "max_results": max_results,
                "include": ["results", "engine_status"],
            },
        ),
        "search",
    )
    cards = search.get("results")
    if not isinstance(cards, list):
        raise RuntimeError("search returned no result list")
    result_ids = [str(card.get("result_id", "")) for card in cards[:max_results]]
    if any(not result_id for result_id in result_ids):
        raise RuntimeError("search card omitted its result identity")

    private_items = []
    public_items = []
    for index, result_id in enumerate(result_ids):
        expanded = _require(
            client.call_tool("slopsearx_read_result", {"result_id": result_id}),
            "read_result",
        )
        handoff = expanded.get("retrieval")
        if not isinstance(handoff, dict) or handoff.get("result_id") != result_id:
            raise RuntimeError("expanded result omitted its retrieval handoff")
        receipt_key = f"w11-{digest(f'{case_id}:{index}:{result_id}')[:32]}"
        capture: dict[str, Any]
        if handoff.get("eligible") is True and isinstance(handoff.get("url"), str):
            try:
                response = api.post(
                    "/v2/scrape",
                    json={"url": handoff["url"], "formats": ["markdown"]},
                )
                response.raise_for_status()
                payload = response.json()
                markdown = str((payload.get("data") or {}).get("markdown", ""))
                if not payload.get("success") or not markdown:
                    raise RuntimeError("empty_scrape")
                content_sha256 = digest(markdown)
                metadata = (payload.get("data") or {}).get("metadata") or {}
                final_url = metadata.get("sourceURL") or handoff["url"]
                capture = {
                    "status": "succeeded",
                    "captured_at": datetime.now(UTC).isoformat(),
                    "final_url": final_url,
                    "content_sha256": content_sha256,
                    "capture_ref": f"urn:sha256:{content_sha256}",
                    "passage_refs": [
                        {"ref": f"urn:sha256:{content_sha256}#full", "label": "reviewed capture"}
                    ],
                    "markdown": markdown,
                }
            except Exception as error:
                capture = {
                    "status": "failed",
                    "failure_code": "scrape_failed",
                    "failure_message": type(error).__name__,
                }
        else:
            capture = {
                "status": "failed",
                "failure_code": "handoff_ineligible",
                "failure_message": str(handoff.get("url_status", "unknown")),
            }
        receipt_args = {
            "result_id": result_id,
            "retriever": "groktocrawl-x-w11",
            "idempotency_key": receipt_key,
            **{key: value for key, value in capture.items() if key != "markdown"},
        }
        receipt = _require(
            client.call_tool("slopsearx_submit_retrieval_receipt", receipt_args),
            "submit_receipt",
        )
        replay = _require(
            client.call_tool("slopsearx_submit_retrieval_receipt", receipt_args),
            "replay_receipt",
        )
        first_id = (receipt.get("receipt") or {}).get("receipt_id")
        replay_id = (replay.get("receipt") or {}).get("receipt_id")
        if replay.get("state") != "replayed" or not first_id or first_id != replay_id:
            raise RuntimeError("receipt replay was not idempotent")
        retained = _require(
            client.call_tool(
                "slopsearx_read_retrieval_receipts",
                {"result_id": result_id, "limit": 20},
            ),
            "read_receipts",
        )
        linkage = (
            (receipt.get("receipt") or {}).get("result_id") == result_id
            and (receipt.get("receipt") or {}).get("discovery", {}).get("result_id") == result_id
            and retained.get("result_id") == result_id
        )
        public_items.append(
            {
                "result_id_sha256": digest(result_id),
                "handoff_eligible": handoff.get("eligible") is True,
                "url_status": handoff.get("url_status"),
                "capture_status": capture["status"],
                "content_sha256": capture.get("content_sha256"),
                "receipt_created": receipt.get("state") == "created",
                "receipt_replayed": replay.get("state") == "replayed",
                "receipt_identity_stable": first_id == replay_id,
                "discovery_capture_linked": linkage,
                "observations_verified": retained.get("observations_verified"),
            }
        )
        private_items.append(
            {
                "result": expanded,
                "capture": capture,
                "receipt": receipt,
                "replay": replay,
                "retained": retained,
            }
        )
    manifest = _require(
        client.call_tool(
            "slopsearx_export_research_manifest", {"result_ids": result_ids}
        ),
        "export_manifest",
    ) if result_ids else None
    manifest_ids = {
        item.get("result_id") for item in (manifest or {}).get("items", [])
    }
    public: dict[str, Any] = {
        "schema_version": "enterprise-evaluation/w11-provenance-case/1",
        "case_id": case_id,
        "query_sha256": digest(query),
        "engine_scope_sha256": digest(json.dumps(engines, separators=(",", ":"))),
        "result_count": len(result_ids),
        "items": public_items,
        "manifest": {
            "present": manifest is not None,
            "all_results_present": bool(result_ids) and manifest_ids == set(result_ids),
            "observations_verified": (manifest or {}).get("observations_verified"),
            "verification_disclosed": bool((manifest or {}).get("verification_note")),
        },
    }
    # The manifest's observations_verified field must be false, while every
    # other manifest gate must be true.
    public["hard_gate_passed"] = bool(public_items) and all(
        item["receipt_created"]
        and item["receipt_replayed"]
        and item["receipt_identity_stable"]
        and item["discovery_capture_linked"]
        and item["observations_verified"] is False
        for item in public_items
    ) and (
        public["manifest"]["present"]
        and public["manifest"]["all_results_present"]
        and public["manifest"]["observations_verified"] is False
        and public["manifest"]["verification_disclosed"]
    )
    return public, {"search": search, "items": private_items, "manifest": manifest}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--api-base-url", required=True)
    parser.add_argument("--query", required=True)
    parser.add_argument("--engines", required=True)
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--max-results", type=int, default=8)
    parser.add_argument("--public-output", type=Path, required=True)
    parser.add_argument("--private-output", type=Path, required=True)
    parser.add_argument("--token-env", default="SLOPSEARX_MCP_AUTH_TOKEN")
    args = parser.parse_args()
    engines = [value.strip() for value in args.engines.split(",") if value.strip()]
    if not engines:
        parser.error("--engines must contain at least one engine")
    if args.max_results < 1:
        parser.error("--max-results must be positive")
    if args.public_output.resolve() == args.private_output.resolve():
        parser.error("public and private outputs must be different files")
    args.private_output.parent.mkdir(parents=True, exist_ok=True)
    args.private_output.parent.chmod(0o700)
    token = os.environ.get(args.token_env, "")
    with (
        W11McpClient(args.endpoint, token, timeout_seconds=180) as client,
        httpx.Client(base_url=args.api_base_url, timeout=180) as api,
    ):
        public, private = run_case(
            client,
            api,
            query=args.query,
            engines=engines,
            max_results=args.max_results,
            case_id=args.case_id,
        )
    atomic_json(args.private_output, private, mode=0o600)
    atomic_json(args.public_output, public)
    print(json.dumps({"result_count": public["result_count"], "hard_gate_passed": public["hard_gate_passed"]}, indent=2))
    return 0 if public["hard_gate_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
