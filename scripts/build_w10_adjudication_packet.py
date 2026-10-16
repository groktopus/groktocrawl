#!/usr/bin/env python3
"""Build blinded W10 manual-adjudication packets from completed trial records."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
from collections import defaultdict
from pathlib import Path
from typing import Any


def digest(value: str | bytes) -> str:
    raw = value.encode() if isinstance(value, str) else value
    return hashlib.sha256(raw).hexdigest()


def load_cases(paths: list[Path]) -> dict[str, dict[str, Any]]:
    cases = {}
    for path in paths:
        for case in json.loads(path.read_text())["cases"]:
            cases[case["case_id"]] = case
    return cases


def load_observations(
    run_dirs: list[Path], cases: dict[str, dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    observations = []
    gap_observations = []
    failures = []
    for run_dir in run_dirs:
        for record_path in sorted((run_dir / "records").glob("*.json")):
            record = json.loads(record_path.read_text())
            if record["status"] != "completed":
                failures.append(record)
                continue
            private_path = run_dir / "private-acquisitions" / record_path.name
            private = {
                item["candidate_id"]: item
                for item in json.loads(private_path.read_text())
            }
            admitted_sources = []
            for candidate in record["candidates"]:
                grade = candidate.get("operational_assessment")
                if grade is None:
                    continue
                candidate_id = candidate["candidate_id"]
                private_item = private[candidate_id]
                if candidate.get("admitted"):
                    admitted_sources.append(
                        {
                            "candidate_id": candidate_id,
                            "title": candidate["title"],
                            "url": candidate["url"],
                            "reviewed_excerpt": private_item["reviewed_excerpt"],
                        }
                    )
                observation_id = digest(
                    ":".join(
                        (
                            record["case_id"],
                            record["policy"],
                            str(record["repetition"]),
                            candidate_id,
                        )
                    )
                )[:20]
                observations.append(
                    {
                        "observation_id": observation_id,
                        "case_id": record["case_id"],
                        "policy": record["policy"],
                        "repetition": record["repetition"],
                        "candidate_id": candidate_id,
                        "question": cases[record["case_id"]]["query"],
                        "claims": cases[record["case_id"]]["claims"],
                        "title": candidate["title"],
                        "url": candidate["url"],
                        "reviewed_excerpt": private_item["reviewed_excerpt"],
                        "model_grade": grade,
                    }
                )
            claim_by_id = {
                item["claim_id"]: item for item in cases[record["case_id"]]["claims"]
            }
            interim_by_gap = {
                item["gap_id"]: item
                for item in (record.get("interim_assessment") or {}).get("gaps", [])
            }
            for gap in record["gap_results"]:
                claim = claim_by_id[gap["gap_id"]]
                gap_observations.append(
                    {
                        "observation_id": digest(
                            ":".join(
                                (
                                    "gap",
                                    record["case_id"],
                                    record["policy"],
                                    str(record["repetition"]),
                                    gap["gap_id"],
                                )
                            )
                        )[:20],
                        "case_id": record["case_id"],
                        "policy": record["policy"],
                        "repetition": record["repetition"],
                        "gap_id": gap["gap_id"],
                        "question": cases[record["case_id"]]["query"],
                        "claim": claim,
                        "admitted_sources": admitted_sources,
                        "interim_gap_grade": interim_by_gap.get(gap["gap_id"]),
                        "model_gap_grade": gap,
                    }
                )
    return observations, gap_observations, failures


def grade_signature(observation: dict[str, Any]) -> str:
    grade = observation["model_grade"]
    relevant = {
        key: grade[key]
        for key in (
            "relevant_gap_ids",
            "supports_or_challenges",
            "quality",
            "derivative_of",
            "marginal_value",
            "improves_currency",
            "improves_authority",
            "resolves_contradiction",
        )
    }
    return digest(json.dumps(relevant, sort_keys=True))


def select_gap_disagreements(
    observations: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, set[str]]]:
    reasons: dict[str, set[str]] = defaultdict(set)
    selected: dict[str, dict[str, Any]] = {}
    by_gap: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for item in observations:
        by_gap[(item["case_id"], item["gap_id"])].append(item)
        interim = item.get("interim_gap_grade")
        if interim is not None and interim.get("status") != item["model_gap_grade"].get(
            "status"
        ):
            selected[item["observation_id"]] = item
            reasons[item["observation_id"]].add("interim_final_closure_disagreement")
    for items in by_gap.values():
        if items[0]["claim"]["importance"] != 3:
            continue
        statuses = {item["model_gap_grade"]["status"] for item in items}
        if len(statuses) <= 1:
            continue
        for item in items:
            selected[item["observation_id"]] = item
            reasons[item["observation_id"]].add("high_importance_closure_disagreement")
    return selected, reasons


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, action="append", required=True)
    parser.add_argument("--cases", type=Path, action="append", required=True)
    parser.add_argument("--private-output", type=Path, required=True)
    parser.add_argument("--manifest-output", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=20260911)
    args = parser.parse_args()

    cases = load_cases(args.cases)
    observations, gap_observations, failures = load_observations(args.run_dir, cases)
    by_source: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for item in observations:
        by_source[(item["case_id"], item["candidate_id"])].append(item)

    representatives = [
        sorted(items, key=lambda item: item["observation_id"])[0]
        for items in by_source.values()
    ]
    random.Random(args.seed).shuffle(representatives)
    sample_size = math.ceil(len(representatives) * 0.10)
    reasons: dict[str, set[str]] = defaultdict(set)
    selected: dict[str, dict[str, Any]] = {}
    for item in representatives[:sample_size]:
        selected[item["observation_id"]] = item
        reasons[item["observation_id"]].add("seeded_10_percent_unique_source_sample")

    for items in by_source.values():
        if len({grade_signature(item) for item in items}) <= 1:
            continue
        for item in items:
            selected[item["observation_id"]] = item
            reasons[item["observation_id"]].add("repeated_grade_disagreement")

    selected_gaps, gap_reasons = select_gap_disagreements(gap_observations)

    private_items = []
    public_items = []
    for observation_id, item in sorted(selected.items()):
        blind = {
            key: value
            for key, value in item.items()
            if key not in {"policy", "repetition", "model_grade"}
        }
        blind["selection_reasons"] = sorted(reasons[observation_id])
        blind["item_type"] = "source_grade"
        private_items.append(blind)
        public_items.append(
            {
                "observation_id": observation_id,
                "case_id": item["case_id"],
                "candidate_id": item["candidate_id"],
                "selection_reasons": sorted(reasons[observation_id]),
                "item_type": "source_grade",
                "private_item_sha256": digest(
                    json.dumps(blind, ensure_ascii=False, sort_keys=True)
                ),
            }
        )

    for observation_id, item in sorted(selected_gaps.items()):
        blind = {
            key: value
            for key, value in item.items()
            if key
            not in {
                "policy",
                "repetition",
                "model_gap_grade",
                "interim_gap_grade",
            }
        }
        blind["selection_reasons"] = sorted(gap_reasons[observation_id])
        blind["item_type"] = "claim_closure"
        private_items.append(blind)
        public_items.append(
            {
                "observation_id": observation_id,
                "case_id": item["case_id"],
                "gap_id": item["gap_id"],
                "selection_reasons": sorted(gap_reasons[observation_id]),
                "item_type": "claim_closure",
                "private_item_sha256": digest(
                    json.dumps(blind, ensure_ascii=False, sort_keys=True)
                ),
            }
        )

    private_payload = {
        "schema_version": "enterprise-evaluation/w10-adjudication-private/1",
        "blind_to_policy_and_repetition": True,
        "items": private_items,
    }
    args.private_output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    args.private_output.write_text(
        json.dumps(private_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    manifest = {
        "schema_version": "enterprise-evaluation/w10-adjudication-manifest/1",
        "seed": args.seed,
        "unique_case_sources": len(representatives),
        "seeded_sample_size": sample_size,
        "selected_observations": len(private_items),
        "selected_source_grades": len(selected),
        "selected_claim_closures": len(selected_gaps),
        "schema_failures": failures,
        "private_packet_sha256": digest(args.private_output.read_bytes()),
        "items": public_items,
    }
    args.manifest_output.parent.mkdir(parents=True, exist_ok=True)
    args.manifest_output.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    print(
        json.dumps(
            {
                "unique_case_sources": len(representatives),
                "selected_observations": len(private_items),
                "selected_source_grades": len(selected),
                "selected_claim_closures": len(selected_gaps),
                "schema_failures": len(failures),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
