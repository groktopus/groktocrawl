#!/usr/bin/env python3
"""Run a bounded, private W8 retrieval baseline against a SearXNG API."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any

SCHEMA = "enterprise-evaluation/w8-retrieval-baseline/1"


def canonical_url(value: str) -> str:
    """Return a conservative identity for exact known-source matching."""
    parsed = urllib.parse.urlsplit(value.strip())
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").lower()
    host = host.removeprefix("www.")
    port = parsed.port
    netloc = host
    if port and not (scheme == "https" and port == 443):
        netloc = f"{host}:{port}"
    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/")
    if host == "github.com":
        path = path.casefold()
    return urllib.parse.urlunsplit((scheme, netloc, path, parsed.query, ""))


def packet_digest(packet: dict[str, Any]) -> str:
    """Reproduce the packet's self-reference-free canonical digest."""
    clone = json.loads(json.dumps(packet))
    clone["manifest"]["hashes"] = {"corpus_sha256": "", "readme_sha256": ""}
    encoded = json.dumps(
        clone, sort_keys=True, indent=2, ensure_ascii=False
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def score_results(case: dict[str, Any], results: list[dict[str, Any]]) -> dict[str, Any]:
    """Measure exact known-source recall, rank, and duplicate URLs."""
    expected = {
        canonical_url(item["url"]): item["url"] for item in case["known_useful_urls"]
    }
    observed = [canonical_url(str(item.get("url", ""))) for item in results]
    matched = [expected[url] for url in dict.fromkeys(observed) if url in expected]
    ranks = [index for index, url in enumerate(observed, start=1) if url in expected]
    duplicates = len(observed) - len(set(observed))
    return {
        "known_useful_total": len(expected),
        "known_useful_found": len(matched),
        "known_useful_recall": round(len(matched) / len(expected), 6),
        "first_useful_rank": min(ranks) if ranks else None,
        "matched_known_urls": matched,
        "duplicate_results": duplicates,
        "duplicate_rate": round(duplicates / len(observed), 6) if observed else 0.0,
    }


def _request(
    endpoint: str,
    query: str,
    limit: int,
    timeout: float,
    categories: str | None,
    engines: str | None,
) -> tuple[dict[str, Any], int]:
    params: dict[str, Any] = {
        "q": query,
        "format": "json",
        "language": "en",
        "pageno": 1,
    }
    if categories:
        params["categories"] = categories
    if engines:
        params["engines"] = engines
    url = f"{endpoint.rstrip('/')}/search?{urllib.parse.urlencode(params)}"
    started = time.monotonic()
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "groktocrawl-x-w8/1"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = response.read()
    latency_ms = round((time.monotonic() - started) * 1000)
    data = json.loads(payload)
    if not isinstance(data, dict) or not isinstance(data.get("results"), list):
        raise ValueError("search response does not contain a results list")
    data["results"] = data["results"][:limit]
    return data, latency_ms


def _percentile(values: list[int], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return round(
        ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower),
        1,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--label", default="current-slopsearx")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--trials", type=int, default=1)
    parser.add_argument("--seed", type=int, default=20260911)
    parser.add_argument("--categories")
    parser.add_argument("--engines")
    args = parser.parse_args()
    if not 1 <= args.limit <= 100 or not 1 <= args.trials <= 10:
        parser.error("limit must be 1..100 and trials must be 1..10")
    if args.output.exists():
        parser.error("output directory already exists")

    packet = json.loads(args.packet.read_text())
    expected_digest = packet["manifest"]["hashes"]["corpus_sha256"]
    actual_digest = packet_digest(packet)
    if actual_digest != expected_digest:
        parser.error("packet digest does not match its manifest")
    cases = packet["cases"]
    schedule = [
        {"case_id": case["case_id"], "trial": trial}
        for case in cases
        for trial in range(1, args.trials + 1)
    ]
    random.Random(args.seed).shuffle(schedule)
    by_id = {case["case_id"]: case for case in cases}

    args.output.mkdir(parents=True)
    (args.output / "schedule.json").write_text(json.dumps(schedule, indent=2) + "\n")
    rows: list[dict[str, Any]] = []
    with (args.output / "results.jsonl").open("w") as stream:
        for item in schedule:
            case = by_id[item["case_id"]]
            started = time.monotonic()
            base = {
                "case_id": item["case_id"],
                "category": case["category"],
                "trial": item["trial"],
            }
            try:
                response, latency_ms = _request(
                    args.endpoint,
                    case["query"],
                    args.limit,
                    args.timeout,
                    args.categories,
                    args.engines,
                )
                row = {
                    **base,
                    "status": "completed",
                    "latency_ms": latency_ms,
                    "score": score_results(case, response["results"]),
                    "response": response,
                }
            except (OSError, ValueError, json.JSONDecodeError, urllib.error.URLError) as error:
                row = {
                    **base,
                    "status": "failed",
                    "latency_ms": round((time.monotonic() - started) * 1000),
                    "error_type": type(error).__name__,
                    "error": str(error),
                }
                if isinstance(error, urllib.error.HTTPError):
                    row["http_status"] = error.code
            rows.append(row)
            stream.write(json.dumps(row, sort_keys=True) + "\n")
            stream.flush()

    completed = [row for row in rows if row["status"] == "completed"]
    latencies = [row["latency_ms"] for row in completed]
    scores = [row["score"] for row in completed]
    categories = {
        category: {
            "scheduled": sum(row["category"] == category for row in rows),
            "completed": sum(
                row["category"] == category and row["status"] == "completed"
                for row in rows
            ),
            "known_useful_found": sum(
                row["score"]["known_useful_found"]
                for row in completed
                if row["category"] == category
            ),
            "known_useful_total": sum(
                row["score"]["known_useful_total"]
                for row in completed
                if row["category"] == category
            ),
        }
        for category in sorted({row["category"] for row in rows})
    }
    manifest = {
        "schema_version": SCHEMA,
        "status": "execution_complete",
        "label": args.label,
        "packet_sha256": actual_digest,
        "seed": args.seed,
        "bounds": {
            "scheduled_attempts": len(schedule),
            "trials_per_case": args.trials,
            "result_limit": args.limit,
            "timeout_seconds": args.timeout,
            "automatic_retries": 0,
        },
        "scope": {"categories": args.categories, "engines": args.engines},
        "outcomes": dict(Counter(row["status"] for row in rows)),
        "metrics": {
            "known_useful_found": sum(score["known_useful_found"] for score in scores),
            "known_useful_total": sum(score["known_useful_total"] for score in scores),
            "known_useful_recall": round(
                sum(score["known_useful_found"] for score in scores)
                / sum(score["known_useful_total"] for score in scores),
                6,
            )
            if scores
            else None,
            "queries_with_known_useful_result": sum(
                score["known_useful_found"] > 0 for score in scores
            ),
            "mean_first_useful_rank": round(
                statistics.mean(
                    score["first_useful_rank"]
                    for score in scores
                    if score["first_useful_rank"] is not None
                ),
                3,
            )
            if any(score["first_useful_rank"] is not None for score in scores)
            else None,
            "duplicate_results": sum(score["duplicate_results"] for score in scores),
            "returned_results": sum(
                len(row["response"]["results"]) for row in completed
            ),
            "p50_latency_ms": _percentile(latencies, 0.5),
            "p95_latency_ms": _percentile(latencies, 0.95),
            "cached_attempts": sum(
                bool(row["response"].get("meta", {}).get("cached"))
                for row in completed
            ),
            "partial_attempts": sum(
                bool(row["response"].get("meta", {}).get("partial"))
                for row in completed
            ),
        },
        "categories": categories,
        "cost": "unknown; the SearXNG-compatible response reports no monetary cost",
        "production_adoption": False,
    }
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
