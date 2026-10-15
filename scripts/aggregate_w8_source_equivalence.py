#!/usr/bin/env python3
"""Join frozen blind grades to the sealed arm map and emit aggregate results."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

USEFUL = {"exact_reference", "substantively_equivalent"}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def wilson(successes: int, total: int, z: float = 1.96) -> list[float] | None:
    if total == 0:
        return None
    p = successes / total
    denominator = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denominator
    margin = z * ((p * (1 - p) / total + z * z / (4 * total * total)) ** 0.5) / denominator
    return [round(centre - margin, 6), round(centre + margin, 6)]


def validate_freeze(freeze: dict[str, Any], record_count: int) -> None:
    if freeze.get("schema_version") != "w8-source-equivalence-grade-freeze/1":
        raise ValueError("unsupported blind-grade freeze")
    if freeze.get("arm_map_read") is not False:
        raise ValueError("blind-grade freeze does not attest sealed arms")
    if freeze.get("record_count") != record_count:
        raise ValueError("blind-grade freeze count mismatch")


def aggregate(grades: list[dict[str, Any]], mapping: list[dict[str, Any]]) -> dict[str, Any]:
    by_id = {item["candidate_id"]: item for item in grades}
    if len(by_id) != len(grades):
        raise ValueError("duplicate grade candidate identity")
    if any(item.get("status") != "graded" for item in grades):
        raise ValueError("blind grading is incomplete")
    if {item["candidate_id"] for item in mapping} != set(by_id):
        raise ValueError("sealed mapping and grades cover different candidates")

    sightings: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in mapping:
        grade = by_id[item["candidate_id"]]["grade"]
        for sighting in item["sightings"]:
            sightings[sighting["arm"]].append(
                {"case_id": item["case_id"], "position": sighting["position"], "label": grade["label"]}
            )

    arms: dict[str, Any] = {}
    for arm, rows in sorted(sightings.items()):
        labels = Counter(row["label"] for row in rows)
        useful = sum(row["label"] in USEFUL for row in rows)
        cases: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            cases[row["case_id"]].append(row)
        cases_with_useful = sum(any(row["label"] in USEFUL for row in case) for case in cases.values())
        reciprocal_ranks = [
            1 / min(row["position"] for row in case if row["label"] in USEFUL)
            for case in cases.values() if any(row["label"] in USEFUL for row in case)
        ]
        arms[arm] = {
            "sightings": len(rows),
            "labels": dict(sorted(labels.items())),
            "useful_sightings": useful,
            "useful_sighting_rate": round(useful / len(rows), 6) if rows else None,
            "useful_sighting_rate_95pct_wilson": wilson(useful, len(rows)),
            "cases": len(cases),
            "cases_with_at_least_one_useful_source": cases_with_useful,
            "case_coverage_rate": round(cases_with_useful / len(cases), 6) if cases else None,
            "mean_reciprocal_rank_first_useful": round(sum(reciprocal_ranks) / len(cases), 6) if cases else None,
        }
    return {"schema_version": "w8-source-equivalence-aggregate/1", "arms": arms}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--grades", type=Path, required=True)
    parser.add_argument("--grades-freeze", type=Path, required=True)
    parser.add_argument("--arm-map", type=Path, required=True)
    parser.add_argument("--arm-map-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    freeze = json.loads(args.grades_freeze.read_text())
    if digest(args.arm_map) != args.arm_map_sha256:
        raise ValueError("sealed arm-map digest mismatch")
    records = sorted((args.grades / "records").glob("*.json"))
    validate_freeze(freeze, len(records))
    hashes = {path.name: digest(path) for path in records}
    if hashes != freeze.get("record_sha256"):
        raise ValueError("blind grades differ from their freeze")
    result = aggregate(
        [json.loads(path.read_text()) for path in records],
        json.loads(args.arm_map.read_text()),
    )
    result.update({
        "blind_grade_freeze_sha256": digest(args.grades_freeze),
        "sealed_arm_map_sha256": args.arm_map_sha256,
    })
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
