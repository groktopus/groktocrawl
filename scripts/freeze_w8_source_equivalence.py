#!/usr/bin/env python3
"""Validate and freeze a complete set of arm-blinded W8 grade records."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_freeze(grades: Path, *, expected_count: int) -> dict[str, Any]:
    records = sorted((grades / "records").glob("*.json"))
    if len(records) != expected_count:
        raise ValueError(
            f"blind grade set is incomplete: expected {expected_count}, found {len(records)}"
        )
    hashes: dict[str, str] = {}
    candidate_ids: set[str] = set()
    for path in records:
        record = json.loads(path.read_text())
        if record.get("schema_version") != "w8-source-equivalence-grade/1":
            raise ValueError(f"unsupported grade record: {path.name}")
        if record.get("status") != "graded" or not isinstance(record.get("grade"), dict):
            raise ValueError(f"grade record is not successful: {path.name}")
        candidate_id = record.get("candidate_id")
        if not isinstance(candidate_id, str) or candidate_id in candidate_ids:
            raise ValueError(f"duplicate or invalid candidate identity: {path.name}")
        if path.name != f"{candidate_id}.json":
            raise ValueError(f"grade filename does not match candidate identity: {path.name}")
        candidate_ids.add(candidate_id)
        hashes[path.name] = digest(path)
    return {
        "schema_version": "w8-source-equivalence-grade-freeze/1",
        "record_count": len(records),
        "record_sha256": hashes,
        "arm_map_read": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--grades", type=Path, required=True)
    parser.add_argument("--expected-count", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    freeze = build_freeze(args.grades, expected_count=args.expected_count)
    args.output.write_text(json.dumps(freeze, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"record_count": freeze["record_count"], "freeze_sha256": digest(args.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
