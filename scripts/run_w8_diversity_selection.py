#!/usr/bin/env python3
"""Compare bounded source-selection policies on a private W8 packet."""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import time
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

QUALITY = {"low": 1, "medium": 2, "high": 3}
SOURCE_TYPE = {"aggregator": 0, "secondary": 1, "primary": 2}
SCHEMA = "enterprise-evaluation/w8-diversity-selection/1"


def fact_ids(candidate: dict[str, Any]) -> set[str]:
    return {str(item).split(":", 1)[0] for item in candidate["facts"]}


def relevance_only(case: dict[str, Any]) -> list[dict[str, Any]]:
    return sorted(case["candidates"], key=lambda item: item["rank"])[
        : case["selection_limit"]
    ]


def _quality_pool(case: dict[str, Any]) -> list[dict[str, Any]]:
    """Collapse canonical copies and decline low-quality padding."""
    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for candidate in sorted(case["candidates"], key=lambda item: item["rank"]):
        identity = candidate["canonical_document_id"]
        if identity not in seen:
            seen.add(identity)
            unique.append(candidate)
    usable = [candidate for candidate in unique if candidate["quality"] != "low"]
    return usable or unique[:1]


def independent_quality(case: dict[str, Any]) -> list[dict[str, Any]]:
    pool = _quality_pool(case)
    selected: list[dict[str, Any]] = []
    publishers: set[str] = set()
    domains: set[str] = set()
    facts: set[str] = set()
    while pool and len(selected) < case["selection_limit"]:
        candidate = max(
            pool,
            key=lambda item: (
                QUALITY[item["quality"]] * 100
                + (item["publisher_id"] not in publishers) * 35
                + (item["registrable_domain"] not in domains) * 15
                + len(fact_ids(item) - facts) * 12
                + SOURCE_TYPE[item["source_type"]] * 10
                - item["rank"]
            ),
        )
        pool.remove(candidate)
        selected.append(candidate)
        publishers.add(candidate["publisher_id"])
        domains.add(candidate["registrable_domain"])
        facts.update(fact_ids(candidate))
    return selected


def quality_gated_opposition(case: dict[str, Any]) -> list[dict[str, Any]]:
    """Add credible opposition without treating low-quality dissent as balance."""
    selected = independent_quality(case)
    if any(item["stance"] == "contradicts" for item in selected):
        return selected
    publishers = {item["publisher_id"] for item in selected}
    opposing = [
        item
        for item in _quality_pool(case)
        if item["stance"] == "contradicts"
        and item["quality"] in {"medium", "high"}
        and item["publisher_id"] not in publishers
    ]
    if not opposing:
        return selected
    opposition = max(
        opposing,
        key=lambda item: (
            QUALITY[item["quality"]], SOURCE_TYPE[item["source_type"]], -item["rank"]
        ),
    )
    if len(selected) < case["selection_limit"]:
        return [*selected, opposition]
    victims = sorted(
        selected,
        key=lambda item: (
            QUALITY[item["quality"]], SOURCE_TYPE[item["source_type"]], -item["rank"]
        ),
    )
    for victim in victims:
        replacement = [item for item in selected if item is not victim] + [opposition]
        if any(item["source_type"] == "primary" for item in replacement):
            return replacement
    return selected


def score(case: dict[str, Any], selected: list[dict[str, Any]]) -> dict[str, Any]:
    ground = case["ground_truth"]
    facts = set().union(*(fact_ids(item) for item in selected)) if selected else set()
    required = set(ground["required_fact_ids"])
    stances = {item["stance"] for item in selected}
    engines = set().union(*(set(item["engines"]) for item in selected)) if selected else set()
    publishers = {item["publisher_id"] for item in selected}
    canonical = {item["canonical_document_id"] for item in selected}
    return {
        "selected_ids": [item["candidate_id"] for item in selected],
        "selected_count": len(selected),
        "required_facts_found": len(required & facts),
        "required_facts_total": len(required),
        "fact_coverage": round(len(required & facts) / len(required), 6),
        "answer_supported": required <= facts,
        "material_contradiction_discovered": bool(
            ground["material_contradiction_present"]
            and {"supports", "contradicts"} <= stances
        ),
        "false_balance": bool(ground["false_balance_risk"] and "contradicts" in stances),
        "engine_count": len(engines),
        "publisher_count": len(publishers),
        "publisher_independence": round(len(publishers) / len(selected), 6),
        "canonical_duplicates": len(selected) - len(canonical),
        "primary_present": any(item["source_type"] == "primary" for item in selected),
        "mean_quality": round(statistics.mean(QUALITY[item["quality"]] for item in selected), 6),
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    scores = [row["score"] for row in rows]
    elapsed = [row["elapsed_us"] for row in rows]
    return {
        "cases": len(rows),
        "mean_fact_coverage": round(statistics.mean(item["fact_coverage"] for item in scores), 6),
        "answers_supported": sum(item["answer_supported"] for item in scores),
        "material_contradictions_discovered": sum(item["material_contradiction_discovered"] for item in scores),
        "false_balance_cases": sum(item["false_balance"] for item in scores),
        "mean_publishers": round(statistics.mean(item["publisher_count"] for item in scores), 6),
        "mean_engines": round(statistics.mean(item["engine_count"] for item in scores), 6),
        "engine_agreement_exceeds_publishers": sum(
            item["engine_count"] > item["publisher_count"] for item in scores
        ),
        "mean_publisher_independence": round(statistics.mean(item["publisher_independence"] for item in scores), 6),
        "canonical_duplicates": sum(item["canonical_duplicates"] for item in scores),
        "cases_with_primary": sum(item["primary_present"] for item in scores),
        "mean_quality": round(statistics.mean(item["mean_quality"] for item in scores), 6),
        "mean_selected": round(statistics.mean(item["selected_count"] for item in scores), 6),
        "p50_latency_us": statistics.median(elapsed),
        "max_latency_us": max(elapsed),
        "provider_calls": 0,
        "monetary_cost": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--packet-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output directory already exists")
    packet_bytes = args.packet.read_bytes()
    actual_packet_digest = hashlib.sha256(packet_bytes).hexdigest()
    if actual_packet_digest != args.packet_sha256:
        parser.error("packet digest does not match the frozen input")
    packet = json.loads(packet_bytes)
    if packet.get("schema_version") != "w8-diversity-packet/3":
        parser.error("unsupported diversity packet")
    policies: dict[str, Callable[[dict[str, Any]], list[dict[str, Any]]]] = {
        "relevance_only": relevance_only,
        "independent_quality": independent_quality,
        "quality_gated_opposition": quality_gated_opposition,
    }
    args.output.mkdir(parents=True)
    all_rows: dict[str, list[dict[str, Any]]] = {}
    with (args.output / "results.jsonl").open("w") as stream:
        for name, policy in policies.items():
            rows = []
            for case in packet["cases"]:
                started = time.perf_counter_ns()
                selected = policy(case)
                elapsed_us = (time.perf_counter_ns() - started) // 1000
                row = {
                    "policy": name, "case_id": case["case_id"],
                    "scenario_type": case["scenario_type"], "elapsed_us": elapsed_us,
                    "score": score(case, selected),
                }
                rows.append(row)
                stream.write(json.dumps(row, sort_keys=True) + "\n")
            all_rows[name] = rows
    manifest = {
        "schema_version": SCHEMA,
        "packet_sha256": actual_packet_digest,
        "policies": {
            name: {
                **summarize(rows),
                "scenarios": {
                    scenario: summarize(
                        [row for row in rows if row["scenario_type"] == scenario]
                    )
                    for scenario in sorted(
                        {row["scenario_type"] for row in rows}
                    )
                },
            }
            for name, rows in all_rows.items()
        },
        "scenario_counts": dict(Counter(case["scenario_type"] for case in packet["cases"])),
        "production_adoption": False,
    }
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
