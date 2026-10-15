#!/usr/bin/env python3
"""Compare frozen single-pass retrieval with one bounded query-planning step."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from run_w8_retrieval_baseline import canonical_url, packet_digest, score_results

SCHEMA = "enterprise-evaluation/w8-adaptive-planning/1"
PURPOSES = {"missing_support", "disambiguation", "terminology", "freshness", "opposition"}


@dataclass(frozen=True)
class Bounds:
    max_searches: int = 3
    max_results_per_search: int = 20
    max_model_calls: int = 1
    max_model_output_tokens: int = 512
    max_elapsed_ms: int = 90_000


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _percentile(values: list[int], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return round(ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower), 1)


def validate_decision(payload: Any, original_query: str, bounds: Bounds) -> dict[str, Any]:
    """Admit a small planner decision without repairing model output."""
    if not isinstance(payload, dict) or set(payload) != {
        "sufficient", "reason", "unresolved_needs", "follow_ups"
    }:
        raise ValueError("planner decision has the wrong fields")
    if not isinstance(payload["sufficient"], bool):
        raise ValueError("sufficient must be boolean")
    if payload["reason"] not in {"adequate", "weak", "ambiguous", "stale", "contradictory"}:
        raise ValueError("invalid planning reason")
    needs = payload["unresolved_needs"]
    follow_ups = payload["follow_ups"]
    if not isinstance(needs, list) or not 0 <= len(needs) <= 3:
        raise ValueError("invalid unresolved needs")
    if any(not isinstance(item, str) or not item.strip() or len(item) > 240 for item in needs):
        raise ValueError("invalid unresolved need")
    if not isinstance(follow_ups, list) or len(follow_ups) > bounds.max_searches - 1:
        raise ValueError("invalid follow-up count")
    admitted: list[dict[str, str]] = []
    seen = {" ".join(original_query.casefold().split())}
    for item in follow_ups:
        if not isinstance(item, dict) or set(item) != {"query", "purpose"}:
            raise ValueError("invalid follow-up fields")
        query, purpose = item["query"], item["purpose"]
        if not isinstance(query, str) or not query.strip() or len(query) > 500:
            raise ValueError("invalid follow-up query")
        if purpose not in PURPOSES:
            raise ValueError("invalid follow-up purpose")
        normalized = " ".join(query.casefold().split())
        if normalized in seen:
            raise ValueError("planner repeated a query")
        seen.add(normalized)
        admitted.append({"query": query.strip(), "purpose": purpose})
    if payload["sufficient"] and (needs or admitted or payload["reason"] != "adequate"):
        raise ValueError("sufficient decision contains follow-up work")
    if not payload["sufficient"] and (not needs or not admitted or payload["reason"] == "adequate"):
        raise ValueError("insufficient decision omits bounded follow-up work")
    return {**payload, "unresolved_needs": [item.strip() for item in needs], "follow_ups": admitted}


def _planner_request(
    *, base_url: str, api_key: str, model: str, case: dict[str, Any],
    initial_results: list[dict[str, Any]], bounds: Bounds, timeout: float,
) -> tuple[dict[str, Any], dict[str, Any], int]:
    evidence = [
        {"title": str(item.get("title", ""))[:300], "url": str(item.get("url", ""))[:1000],
         "snippet": str(item.get("content", ""))[:700]}
        for item in initial_results[: bounds.max_results_per_search]
    ]
    prompt = {
        "question": case["query"],
        "as_of": case["as_of"],
        "search_results": evidence,
        "task": (
            "Decide whether these results are sufficient to research the question. If not, return at most "
            f"{bounds.max_searches - 1} distinct search queries aimed at specific missing evidence. "
            "Do not answer the question. Do not assume URLs or facts absent from the results."
        ),
        "required_json": {
            "sufficient": "boolean", "reason": "adequate|weak|ambiguous|stale|contradictory",
            "unresolved_needs": ["one to three concrete evidence needs"],
            "follow_ups": [{"query": "search query", "purpose": "missing_support|disambiguation|terminology|freshness|opposition"}],
        },
    }
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are a conservative research query planner. Return only valid JSON matching the requested shape."},
            {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
        ],
        "stream": False, "max_tokens": bounds.max_model_output_tokens,
        "temperature": 0,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "bounded_query_plan",
                "strict": True,
                "schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["sufficient", "reason", "unresolved_needs", "follow_ups"],
                    "properties": {
                        "sufficient": {"type": "boolean"},
                        "reason": {"type": "string", "enum": ["adequate", "weak", "ambiguous", "stale", "contradictory"]},
                        "unresolved_needs": {
                            "type": "array", "maxItems": 3,
                            "items": {"type": "string", "minLength": 1, "maxLength": 240},
                        },
                        "follow_ups": {
                            "type": "array", "maxItems": bounds.max_searches - 1,
                            "items": {
                                "type": "object", "additionalProperties": False,
                                "required": ["query", "purpose"],
                                "properties": {
                                    "query": {"type": "string", "minLength": 1, "maxLength": 500},
                                    "purpose": {"type": "string", "enum": sorted(PURPOSES)},
                                },
                            },
                        },
                    },
                },
            },
        },
    }
    request = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions", data=json.dumps(body).encode(),
        headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    started = time.monotonic()
    with urllib.request.urlopen(request, timeout=timeout) as response:
        envelope = json.loads(response.read(65_537))
    latency_ms = round((time.monotonic() - started) * 1000)
    content = envelope["choices"][0]["message"]["content"]
    if envelope["choices"][0].get("finish_reason") != "stop" or not isinstance(content, str):
        raise ValueError("planner did not return a final response")
    normalized = content.strip()
    if normalized.startswith("```json\n") and normalized.endswith("\n```"):
        normalized = normalized[len("```json\n") : -len("\n```")]
    decision = validate_decision(json.loads(normalized), case["query"], bounds)
    usage = envelope.get("usage") or {}
    return decision, {"model": envelope.get("model"), "usage": usage, "response_sha256": _digest(content)}, latency_ms


def _search(endpoint: str, query: str, limit: int, timeout: float) -> tuple[dict[str, Any], int]:
    params = urllib.parse.urlencode({"q": query, "format": "json", "language": "en", "pageno": 1})
    request = urllib.request.Request(
        f"{endpoint.rstrip('/')}/search?{params}",
        headers={"Accept": "application/json", "User-Agent": "groktocrawl-x-w8-planning/1"},
    )
    started = time.monotonic()
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read())
    latency = round((time.monotonic() - started) * 1000)
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        raise ValueError("search response does not contain results")
    payload["results"] = payload["results"][:limit]
    return payload, latency


def run_case(
    case: dict[str, Any], baseline: dict[str, Any], *, endpoint: str, base_url: str,
    api_key: str, model: str, bounds: Bounds, timeout: float,
) -> dict[str, Any]:
    """Execute at most one plan and two searches; retain every attempt."""
    started = time.monotonic()
    initial = baseline["response"]["results"][: bounds.max_results_per_search]
    attempts: list[dict[str, Any]] = [{"query": case["query"], "purpose": "initial", "status": "replayed", "results": initial, "latency_ms": baseline["latency_ms"]}]
    row: dict[str, Any] = {"case_id": case["case_id"], "category": case["category"], "attempts": attempts, "model_calls": 0}
    try:
        decision, model_receipt, model_latency = _planner_request(
            base_url=base_url, api_key=api_key, model=model, case=case,
            initial_results=initial, bounds=bounds, timeout=timeout,
        )
        row.update(decision=decision, model_receipt=model_receipt, model_calls=1, model_latency_ms=model_latency)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError, OSError, urllib.error.URLError) as error:
        row.update(
            status="failed", stop_reason="model_failure",
            error_type=type(error).__name__, error=str(error)[:500],
            elapsed_ms=round((time.monotonic() - started) * 1000),
        )
        return row
    if decision["sufficient"]:
        row.update(status="completed", stop_reason="sufficient_initial")
    else:
        row.update(status="completed", stop_reason="plan_exhausted")
        for follow_up in decision["follow_ups"]:
            remaining = bounds.max_elapsed_ms / 1000 - (time.monotonic() - started)
            if remaining <= 0:
                row["stop_reason"] = "time_limit"
                break
            try:
                response, latency = _search(
                    endpoint, follow_up["query"], bounds.max_results_per_search,
                    min(timeout, remaining),
                )
                attempts.append({**follow_up, "status": "completed", "latency_ms": latency, "results": response["results"], "meta": response.get("meta"), "unresponsive_engines": response.get("unresponsive_engines", [])})
            except (ValueError, OSError, urllib.error.URLError, json.JSONDecodeError) as error:
                attempts.append({**follow_up, "status": "failed", "error_type": type(error).__name__, "error": str(error)[:500]})
    all_results, seen = [], set()
    for attempt in attempts:
        for result in attempt.get("results", []):
            identity = canonical_url(str(result.get("url", "")))
            if identity not in seen:
                seen.add(identity)
                all_results.append(result)
    row["score"] = score_results(case, all_results)
    row["searches"] = len(attempts)
    row["results_inspected"] = sum(len(item.get("results", [])) for item in attempts)
    row["unique_results"] = len(all_results)
    row["elapsed_ms"] = round((time.monotonic() - started) * 1000)
    return row


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", default="local")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--seed", type=int, default=20260911)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output directory already exists")
    base_url, api_key = os.environ.get("LLM_BASE_URL", ""), os.environ.get("LLM_API_KEY", "")
    if not base_url or not api_key:
        parser.error("LLM_BASE_URL and LLM_API_KEY are required")
    packet = json.loads(args.packet.read_text())
    if packet_digest(packet) != packet["manifest"]["hashes"]["corpus_sha256"]:
        parser.error("packet digest does not match")
    baselines = {json.loads(line)["case_id"]: json.loads(line) for line in (args.baseline / "results.jsonl").read_text().splitlines()}
    bounds = Bounds()
    cases = list(packet["cases"])
    random.Random(args.seed).shuffle(cases)
    args.output.mkdir(parents=True)
    rows = []
    with (args.output / "results.jsonl").open("w") as stream:
        for case in cases:
            baseline = baselines.get(case["case_id"])
            if not baseline or baseline.get("status") != "completed":
                row = {"case_id": case["case_id"], "category": case["category"], "status": "failed", "stop_reason": "baseline_unavailable"}
            else:
                row = run_case(case, baseline, endpoint=args.endpoint, base_url=base_url, api_key=api_key, model=args.model, bounds=bounds, timeout=args.timeout)
            rows.append(row)
            stream.write(json.dumps(row, sort_keys=True) + "\n")
            stream.flush()
    complete = [row for row in rows if row.get("status") == "completed"]
    scores = [row["score"] for row in complete]
    manifest = {
        "schema_version": SCHEMA, "status": "execution_complete", "packet_sha256": packet_digest(packet),
        "baseline_manifest_sha256": hashlib.sha256((args.baseline / "manifest.json").read_bytes()).hexdigest(),
        "bounds": bounds.__dict__, "seed": args.seed, "model_alias": args.model,
        "outcomes": {name: sum(r.get("status") == name for r in rows) for name in ("completed", "failed")},
        "metrics": {
            "known_useful_found": sum(s["known_useful_found"] for s in scores),
            "known_useful_total": sum(s["known_useful_total"] for s in scores),
            "queries_with_known_useful_result": sum(s["known_useful_found"] > 0 for s in scores),
            "searches": sum(r.get("searches", 0) for r in rows), "model_calls": sum(r.get("model_calls", 0) for r in rows),
            "results_inspected": sum(r.get("results_inspected", 0) for r in rows),
            "opposition_searches": sum(a.get("purpose") == "opposition" for r in rows for a in r.get("attempts", [])),
            "p50_elapsed_ms": _percentile([r["elapsed_ms"] for r in complete], .5), "p95_elapsed_ms": _percentile([r["elapsed_ms"] for r in complete], .95),
            "prompt_tokens": sum((r.get("model_receipt", {}).get("usage", {}).get("prompt_tokens") or 0) for r in rows),
            "completion_tokens": sum((r.get("model_receipt", {}).get("usage", {}).get("completion_tokens") or 0) for r in rows),
        },
        "cost": "unknown; local gateway and search response report no monetary cost", "production_adoption": False,
    }
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
