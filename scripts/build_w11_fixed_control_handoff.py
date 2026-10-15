#!/usr/bin/env python3
"""Build a hash-bound W11 fixed-control handoff from public W10 evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def build(
    *,
    selection_path: Path,
    cases_path: Path,
    accounting_path: Path,
    output_dir: Path,
    manifest_path: Path,
    repetitions: int,
    result_limit: int,
) -> dict[str, Any]:
    selection = json.loads(selection_path.read_text())
    cases = json.loads(cases_path.read_text())
    accounting = json.loads(accounting_path.read_text())
    case_rows = cases.get("cases", [])
    case_sha256 = digest(cases_path)
    if (
        selection.get("schema_version")
        != "enterprise-evaluation/w10-policy-selection/1"
        or selection.get("complete") is not True
        or selection.get("w11_measurement_authorized") is not True
        or selection.get("outcome") != "keep_fixed_retrieval"
        or selection.get("selected_challenge_types") != []
        or (selection.get("inputs") or {}).get("challenge_cases") != case_sha256
    ):
        raise ValueError("W10 does not authorize an all-fixed W11 handoff")
    totals = accounting.get("totals", {})
    gates = accounting.get("completion_gates", {})
    if (
        accounting.get("schema_version")
        != "enterprise-evaluation/w10-public-accounting/1"
        or accounting.get("complete") is not True
        or not gates
        or not all(gates.values())
        or totals.get("observed_trials") != totals.get("expected_trials")
        or totals.get("failed_trials") != 0
        or case_sha256 not in (accounting.get("inputs") or {}).get(
            "case_file_sha256", []
        )
    ):
        raise ValueError("public W10 accounting is incomplete or mismatched")
    if len(case_rows) != 12 or repetitions != 3:
        raise ValueError("the frozen W11 design requires 12 cases and 3 repetitions")
    if type(result_limit) is not int or not 1 <= result_limit <= 20:
        raise ValueError("result limit must be an integer from 1 to 20")

    output_dir.mkdir(parents=True, exist_ok=True)
    expected_names: set[str] = set()
    for case in case_rows:
        query = case.get("query")
        if not isinstance(query, str) or not query.strip():
            raise ValueError("every frozen case requires one non-empty query")
        for repetition in range(repetitions):
            name = f"{case['case_id']}--fixed--{repetition}.json"
            expected_names.add(name)
            atomic_json(
                output_dir / name,
                {
                    "schema_version": (
                        "enterprise-evaluation/w11-fixed-control-record/1"
                    ),
                    "case_id": case["case_id"],
                    "policy": "fixed",
                    "repetition": repetition,
                    "status": "completed",
                    "attempts": [{"query": query}],
                    "derivation": {
                        "kind": "frozen_case_query",
                        "selection_sha256": digest(selection_path),
                        "cases_sha256": case_sha256,
                        "accounting_sha256": digest(accounting_path),
                    },
                },
            )
    unexpected = {
        path.name for path in output_dir.glob("*.json") if path.name not in expected_names
    }
    if unexpected:
        raise ValueError(f"handoff directory contains unexpected records: {sorted(unexpected)}")

    manifest = {
        "schema_version": "enterprise-evaluation/w11-fixed-control-handoff/1",
        "cases_sha256": case_sha256,
        "selection_sha256": digest(selection_path),
        "accounting_sha256": digest(accounting_path),
        "records": len(expected_names),
        "completed": len(expected_names),
        "failed": 0,
        "failed_attempts": 0,
        "repetitions": repetitions,
        "policies": ["fixed"],
        "result_limit": result_limit,
        "query_source": "committed_frozen_case_query",
    }
    atomic_json(manifest_path, manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--w10-selection", type=Path, required=True)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--w10-accounting", type=Path, required=True)
    parser.add_argument("--records-dir", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--result-limit", type=int, required=True)
    args = parser.parse_args()
    result = build(
        selection_path=args.w10_selection,
        cases_path=args.cases,
        accounting_path=args.w10_accounting,
        output_dir=args.records_dir,
        manifest_path=args.manifest,
        repetitions=args.repetitions,
        result_limit=args.result_limit,
    )
    print(json.dumps({"records": result["records"], "result_limit": result["result_limit"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
