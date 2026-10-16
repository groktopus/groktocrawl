#!/usr/bin/env python3
"""Execute the frozen continuation pilot through an operator-supplied proxy.

Only frozen stage-specific request bytes are sent. The runner never sends
evaluator references or complete local source blobs, never retries, and writes
content-free validated receipts rather than provider response text.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess
from pathlib import Path
from typing import Any

import jev_continuation_request_builder as builder

PACKET_SHA256 = "6bdff861da0dfb8f20b7d11983034520d7a37bda6e8d0ad7037da40d314d023c"
CASE_COUNT = 8
MAX_REQUESTS = 24


def _call(proxy: list[str], plan: builder.RequestPlan) -> tuple[dict[str, Any], Any]:
    try:
        process = subprocess.run(
            proxy,
            input=plan.serialized,
            capture_output=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"status": "proxy_or_timeout_failure", "error_type": type(exc).__name__}, None
    if process.returncode:
        return {"status": "proxy_process_failure", "returncode": process.returncode}, None
    try:
        response = json.loads(process.stdout)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {"status": "invalid_proxy_json"}, None
    checked = builder.validate_noul_response(response, plan)
    if isinstance(checked, builder.Abstention):
        return {"status": "unevaluated_response", "reason": checked.reason}, None
    receipt: dict[str, Any] = {
        "status": "evaluated",
        "model": checked.model,
        "request_sha256": checked.request_sha256,
        "response_sha256": hashlib.sha256(process.stdout).hexdigest(),
        "probabilities": dict(checked.probabilities),
        "elapsed_ms": _finite_nonnegative(response.get("_elapsed_ms")),
    }
    if isinstance(response.get("usage"), dict):
        usage = {
            key: value
            for key in ("input_tokens", "output_tokens", "total_tokens")
            if isinstance((value := response["usage"].get(key)), int)
            and not isinstance(value, bool)
            and value >= 0
        }
        if usage:
            receipt["usage"] = usage
    return receipt, checked


def _finite_nonnegative(value: Any) -> float | None:
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not math.isfinite(value)
        or value < 0
    ):
        return None
    return float(value)


def run(packet_path: Path, proxy: list[str]) -> dict[str, Any]:
    packet_bytes = packet_path.read_bytes()
    if hashlib.sha256(packet_bytes).hexdigest() != PACKET_SHA256:
        raise ValueError("case packet digest differs from signed freeze")
    packet = json.loads(packet_bytes)
    if (
        packet.get("study_id") != "jev-continuation-v2-pilot-2026-10-03"
        or len(packet.get("cases", [])) != CASE_COUNT
    ):
        raise ValueError("unsupported frozen packet")
    results: dict[str, Any] = {
        "study_id": packet["study_id"],
        "case_packet_sha256": hashlib.sha256(packet_bytes).hexdigest(),
        "requested_model": builder.MODEL,
        "requests_attempted": 0,
        "requests_unevaluated": 0,
        "searches_dispatched": 0,
        "cases": [],
    }

    def call(case: dict[str, Any], stage: str, plan: builder.RequestPlan) -> tuple[dict[str, Any], Any]:
        if results["requests_attempted"] >= MAX_REQUESTS:
            raise ValueError("frozen pilot request cap exceeded")
        expected = case["frozen_request_sha256"][stage]
        actual = hashlib.sha256(plan.serialized).hexdigest()
        if actual != expected:
            raise ValueError(f"{case['case_id']} {stage} differs from frozen request")
        results["requests_attempted"] += 1
        receipt, validated = _call(proxy, plan)
        receipt["request_sha256"] = expected
        if receipt["status"] == "evaluated":
            returned = receipt["model"]
            if results.get("returned_model") not in (None, returned):
                receipt = {"status": "unevaluated_response", "reason": "model revision changed"}
                validated = None
            else:
                results["returned_model"] = returned
        if receipt["status"] != "evaluated":
            results["requests_unevaluated"] += 1
        return receipt, validated

    for case in packet["cases"]:
        state = case["state"]
        hypothesis = case["research_agent_proposal"]
        sources = {
            source_id: packet["acquired_public_source_blobs"][path]
            for source_id, path in case["source_blob_by_id"].items()
        }
        row: dict[str, Any] = {"case_id": case["case_id"]}
        plan1 = builder.build_sufficiency_request(state)
        if isinstance(plan1, builder.Abstention):
            row["stage1"] = {"status": "pre_call_abstention", "reason": plan1.reason}
            row["stage3"] = {"status": "skipped_after_stage1_abstention"}
            results["cases"].append(row)
            continue
        row["stage1"], _ = call(case, "stage1_sufficiency", plan1)
        if row["stage1"]["status"] != "evaluated":
            row["stage3"] = {"status": "skipped_after_stage1_failure"}
            results["cases"].append(row)
            continue

        plan3 = builder.build_hypothesis_review_request(state, [hypothesis], sources)
        if isinstance(plan3, builder.Abstention):
            row["stage3"] = {"status": "pre_call_abstention", "reason": plan3.reason}
            results["cases"].append(row)
            continue
        row["stage3"], checked3 = call(case, "stage3_hypothesis_review", plan3)
        if row["stage3"]["status"] != "evaluated" or checked3 is None:
            row["stage4"] = {"status": "skipped_after_stage3_failure"}
            results["cases"].append(row)
            continue
        probability = checked3.probabilities["q0000"]
        if probability <= 0.5:
            row["stage4"] = {
                "status": "skipped_by_frozen_binary_argmax",
                "stage3_yes_probability": probability,
            }
        else:
            plan4 = builder.build_addressability_request(
                state,
                hypothesis,
                sources,
                stage3_plan=plan3,
                stage3_response=checked3,
                stage3_question_id="q0000",
                policy_qualified_hypothesis_id=hypothesis["hypothesis_id"],
            )
            if isinstance(plan4, builder.Abstention):
                row["stage4"] = {"status": "pre_call_abstention", "reason": plan4.reason}
            else:
                row["stage4"], _ = call(case, "stage4_addressability_if_stage3_positive", plan4)
        results["cases"].append(row)
    results["requests_validated"] = (
        results["requests_attempted"] - results["requests_unevaluated"]
    )
    results["status"] = "completed"
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("proxy", nargs=argparse.REMAINDER, help="proxy argv after --")
    args = parser.parse_args()
    if args.proxy and args.proxy[0] == "--":
        args.proxy = args.proxy[1:]
    if not args.proxy:
        parser.error("supply an operator-configured proxy command after --")
    descriptor = os.open(
        args.output,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    with os.fdopen(descriptor, "w+", encoding="utf-8") as output:
        output.write('{"status":"running_incomplete"}\n')
        output.flush()
        os.fsync(output.fileno())
        try:
            result = run(args.packet, args.proxy)
        except Exception as exc:
            result = {"status": "failed_incomplete", "error_type": type(exc).__name__}
            output.seek(0)
            output.truncate()
            output.write(json.dumps(result, sort_keys=True) + "\n")
            output.flush()
            os.fsync(output.fileno())
            raise
        output.seek(0)
        output.truncate()
        output.write(json.dumps(result, indent=2, sort_keys=True) + "\n")
        output.flush()
        os.fsync(output.fileno())
    print(json.dumps({key: result[key] for key in (
        "study_id", "requests_attempted", "requests_validated", "requests_unevaluated",
        "searches_dispatched"
    )}, sort_keys=True))


if __name__ == "__main__":
    main()
