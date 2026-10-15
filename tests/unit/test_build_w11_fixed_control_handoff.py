from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(ROOT))

from scripts.build_w11_fixed_control_handoff import build


def write(path: Path, value: object) -> None:
    path.write_text(json.dumps(value) + "\n")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inputs(tmp_path: Path) -> tuple[Path, Path, Path]:
    cases = tmp_path / "cases.json"
    write(
        cases,
        {
            "cases": [
                {"case_id": f"case-{index}", "query": f"query {index}"}
                for index in range(12)
            ]
        },
    )
    selection = tmp_path / "selection.json"
    write(
        selection,
        {
            "schema_version": "enterprise-evaluation/w10-policy-selection/1",
            "complete": True,
            "w11_measurement_authorized": True,
            "outcome": "keep_fixed_retrieval",
            "selected_challenge_types": [],
            "inputs": {"challenge_cases": sha(cases)},
        },
    )
    accounting = tmp_path / "accounting.json"
    write(
        accounting,
        {
            "schema_version": "enterprise-evaluation/w10-public-accounting/1",
            "complete": True,
            "completion_gates": {"complete": True},
            "totals": {
                "observed_trials": 540,
                "expected_trials": 540,
                "failed_trials": 0,
            },
            "inputs": {"case_file_sha256": [sha(cases)]},
        },
    )
    return selection, cases, accounting


def test_builds_exact_fixed_query_handoff(tmp_path: Path) -> None:
    selection, cases, accounting = inputs(tmp_path)
    records = tmp_path / "records"
    manifest = build(
        selection_path=selection,
        cases_path=cases,
        accounting_path=accounting,
        output_dir=records,
        manifest_path=tmp_path / "manifest.json",
        repetitions=3,
        result_limit=8,
    )
    assert manifest["records"] == 36
    assert manifest["policies"] == ["fixed"]
    record = json.loads((records / "case-0--fixed--0.json").read_text())
    assert record["attempts"] == [{"query": "query 0"}]


def test_rejects_nonempty_adaptive_selection(tmp_path: Path) -> None:
    selection, cases, accounting = inputs(tmp_path)
    value = json.loads(selection.read_text())
    value["selected_challenge_types"] = ["contradiction"]
    write(selection, value)
    with pytest.raises(ValueError, match="all-fixed"):
        build(
            selection_path=selection,
            cases_path=cases,
            accounting_path=accounting,
            output_dir=tmp_path / "records",
            manifest_path=tmp_path / "manifest.json",
            repetitions=3,
            result_limit=8,
        )
