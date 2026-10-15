import json
from pathlib import Path

import pytest

from scripts.run_w11_general_retrieval import (
    atomic_json,
    frozen_queries,
    load_w10_record,
    public_record,
    validate_pair_plans,
)


def _write_record(path: Path, *, arm_policy: str = "full") -> None:
    path.write_text(
        json.dumps(
            {
                "case_id": "case-1",
                "policy": arm_policy,
                "repetition": 0,
                "status": "completed",
                "attempts": [{"query": "q0"}, {"query": "q1"}],
            }
        )
    )


def _entry(arm: str) -> dict:
    return {
        "position": 1 if arm == "flat_http" else 2,
        "case_id": "case-1",
        "challenge_type": "freshness",
        "repetition": 0,
        "arm": arm,
        "control_policy": "full",
    }


def test_source_record_and_pair_plan_validation(tmp_path: Path) -> None:
    path = tmp_path / "case-1--full--0.json"
    _write_record(path)
    record, observed_path = load_w10_record(tmp_path, _entry("flat_http"))
    assert observed_path == path
    assert frozen_queries(record) == ["q0", "q1"]
    validate_pair_plans([_entry("flat_http"), _entry("recorded_continuation")], tmp_path)


def test_private_checkpoint_mode_is_explicit(tmp_path: Path) -> None:
    path = tmp_path / "private.json"
    atomic_json(path, {"raw": True}, mode=0o600)
    assert path.stat().st_mode & 0o777 == 0o600


def test_checkpoint_rejects_oversized_evidence(tmp_path: Path) -> None:
    path = tmp_path / "too-large.json"
    with pytest.raises(ValueError, match="evidence limit"):
        atomic_json(path, {"raw": "too large"}, max_bytes=5)
    assert not path.exists()


def test_pair_validation_rejects_missing_arm(tmp_path: Path) -> None:
    _write_record(tmp_path / "case-1--full--0.json")
    with pytest.raises(ValueError, match="both W11 arms"):
        validate_pair_plans([_entry("flat_http")], tmp_path)


def test_public_record_hashes_queries_and_urls() -> None:
    result = public_record(
        entry=_entry("flat_http"),
        source_sha256="a" * 64,
        queries=["secret query"],
        engines=["engine"],
        raw=[
            {
                "query": "secret query",
                "results": [{"url": "https://private.example/path"}],
                "result_count": 1,
            }
        ],
        elapsed_ms=2.5,
        job=None,
    )
    encoded = json.dumps(result)
    assert result["status"] == "completed"
    assert "secret query" not in encoded
    assert "private.example" not in encoded


def test_public_record_retains_only_workflow_accounting() -> None:
    result = public_record(
        entry=_entry("recorded_continuation"),
        source_sha256="a" * 64,
        queries=["q"],
        engines=["engine"],
        raw=[{"query": "q", "results": [], "result_count": 0}],
        elapsed_ms=1,
        job={
            "state": "succeeded",
            "stop_reason": "caller_completed",
            "caller_completed": True,
            "budgets": {"used": {"attempts": 1}},
            "coverage": {"complete": True},
            "question": "must remain private",
        },
    )
    assert result["workflow"]["caller_completed"] is True
    assert "must remain private" not in json.dumps(result)
