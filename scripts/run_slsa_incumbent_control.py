#!/usr/bin/env python3
"""Run the frozen incumbent gap prompt sequentially through an operator proxy.

The control arm sends only the four packet-frozen messages, never the evaluator
pool. It records digests and parsed recommendations, not raw model responses.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

PACKET_SHA256 = "01f362a5ed691a970c58169afb33ee05fc45f313eed2022f00683f929179625c"
CASE_COUNT = 4
MODEL_ALIAS = "free"


def _safe_model(value: Any) -> str | None:
    if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9._:/-]{1,128}", value):
        return value
    return None


def _parse_content(content: str) -> tuple[str, list[str] | None, int | None]:
    cleaned = content.strip().removeprefix("```json").removesuffix("```").strip()
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        return "invalid_json", None, None
    if not isinstance(parsed, list) or len(parsed) > 5:
        return "invalid_shape", None, None
    topics = [value[:500] for value in parsed if isinstance(value, str)]
    return "parsed_incumbent_array", topics, len(parsed) - len(topics)


def run(packet_path: Path, proxy: list[str], checkpoint=None) -> dict[str, Any]:
    raw = packet_path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != PACKET_SHA256:
        raise ValueError("case packet digest differs from signed freeze")
    packet = json.loads(raw)
    if (packet.get("schema_version") != "jev-slsa-replay-pilot/2"
            or packet.get("study_id") != "jev-slsa-deterministic-replay-2026-10-03"
            or len(packet.get("cases", [])) != CASE_COUNT
            or packet.get("call_budget", {}).get("incumbent_control_max_total_requests") != CASE_COUNT):
        raise ValueError("unsupported packet or incumbent budget")
    requests = []
    for case in packet["cases"]:
        control = case["incumbent_gap_control"]
        prompt = control["user_prompt"]
        if hashlib.sha256(prompt.encode()).hexdigest() != control["prompt_sha256"]:
            raise ValueError("frozen incumbent prompt digest mismatch")
        payload = {
            "model": MODEL_ALIAS,
            "messages": [
                {"role": "system", "content": control["system_prompt"]},
                {"role": "user", "content": prompt},
            ],
        }
        request = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        requests.append((case, request))
    # All frozen prompts and digests are checked before the first call.
    outcomes = []

    def record(outcome):
        if outcomes and outcomes[-1].get("status") == "inflight" and outcomes[-1].get("case_id") == outcome.get("case_id"):
            outcomes[-1] = outcome
        else:
            outcomes.append(outcome)
        if checkpoint is not None:
            checkpoint(json.loads(json.dumps({
                "study_id": packet["study_id"], "case_packet_sha256": digest,
                "requested_model_alias": MODEL_ALIAS, "calls_attempted": len(outcomes),
                "calls_evaluated": sum(x["status"] == "parsed_incumbent_array" for x in outcomes),
                "searches_dispatched": 0, "outcomes": outcomes,
            }, allow_nan=False)))

    for case, request in requests:
        record({"case_id": case["case_id"], "status": "inflight", "requested_alias": MODEL_ALIAS})
        try:
            process = subprocess.run(proxy, input=request, capture_output=True, timeout=105, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            record({"case_id": case["case_id"], "status": "proxy_or_timeout_failure", "error_type": type(exc).__name__})
            continue
        if process.returncode:
            record({"case_id": case["case_id"], "status": "proxy_process_failure", "returncode": process.returncode})
            continue
        response_hash = hashlib.sha256(process.stdout).hexdigest()
        try:
            response = json.loads(process.stdout)
        except (json.JSONDecodeError, UnicodeDecodeError):
            record({"case_id": case["case_id"], "status": "invalid_proxy_json", "response_sha256": response_hash})
            continue
        if isinstance(response, dict) and response.get("error"):
            record({
                "case_id": case["case_id"], "status": "proxy_or_provider_failure",
                "error_type": response["error"] if response["error"] in {
                    "provider_http", "proxy_transport", "TimeoutError", "URLError"
                } else "unspecified_proxy_error",
                "response_sha256": response_hash,
            })
            continue
        if not isinstance(response, dict):
            record({"case_id": case["case_id"], "status": "invalid_response_shape", "response_sha256": response_hash})
            continue
        try:
            content = response["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            record({"case_id": case["case_id"], "status": "invalid_response_shape", "response_sha256": response_hash})
            continue
        if not isinstance(content, str):
            record({"case_id": case["case_id"], "status": "invalid_content_type", "response_sha256": response_hash})
            continue
        status, topics, dropped = _parse_content(content)
        outcome: dict[str, Any] = {
            "case_id": case["case_id"], "status": status,
            "response_sha256": response_hash,
            "requested_alias": MODEL_ALIAS,
            "returned_model": _safe_model(response.get("model")),
            "returned_alias": response.get("_configured_model_alias") if response.get("_configured_model_alias") == MODEL_ALIAS else None,
            "elapsed_ms": response.get("_elapsed_ms") if isinstance(response.get("_elapsed_ms"), (int, float)) and not isinstance(response.get("_elapsed_ms"), bool) and 0 <= response["_elapsed_ms"] < 1e9 else None,
        }
        if topics is not None:
            outcome["recommendation_only"] = True
            outcome["topics"] = topics
            outcome["non_string_items_dropped"] = dropped
        record(outcome)
    return {
        "study_id": packet["study_id"], "case_packet_sha256": digest,
        "requested_model_alias": MODEL_ALIAS, "calls_attempted": len(outcomes),
        "calls_evaluated": sum(x["status"] == "parsed_incumbent_array" for x in outcomes),
        "searches_dispatched": 0, "outcomes": outcomes,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("proxy", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.proxy and args.proxy[0] == "--":
        args.proxy = args.proxy[1:]
    if not args.proxy:
        parser.error("supply an operator-configured proxy command after --")
    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "w+", encoding="utf-8") as out:
        out.write('{"status":"running_incomplete"}\n')
        out.flush()
        os.fsync(out.fileno())
        latest = None

        def persist(progress):
            nonlocal latest
            latest = json.loads(json.dumps(progress, allow_nan=False))
            out.seek(0)
            out.truncate()
            out.write(json.dumps(latest, indent=2, sort_keys=True, allow_nan=False) + "\n")
            out.flush()
            os.fsync(out.fileno())

        try:
            result = run(args.packet, args.proxy, checkpoint=persist)
        except Exception as exc:
            out.seek(0)
            out.truncate()
            failed = latest or {"calls_attempted": 0, "outcomes": []}
            for outcome in failed.get("outcomes", []):
                if outcome.get("status") == "inflight":
                    outcome["status"] = "unevaluated_interrupted"
            failed["status"] = "failed_incomplete"
            failed["error_type"] = type(exc).__name__
            out.write(json.dumps(failed, sort_keys=True) + "\n")
            out.flush()
            os.fsync(out.fileno())
            raise
        persist(result)
    print(json.dumps({"calls_attempted": result["calls_attempted"], "calls_evaluated": result["calls_evaluated"], "searches_dispatched": 0}))

if __name__ == "__main__":
    main()
