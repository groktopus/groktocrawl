import json

from scripts.build_w10_accounting import build_accounting
from scripts.summarize_w10_adaptive_policy import POLICIES

SUMMARY = {
    "schema_version": "enterprise-evaluation/w10-summary/1",
    "complete": True,
    "expected_records": {"challenge": 15, "anchor": 0},
    "observed_records": {"challenge": 15, "anchor": 0},
}


def write_fixture(tmp_path, *, omit_private=False, omit_reason=False):
    run_dir = tmp_path / "run"
    records = run_dir / "records"
    private = run_dir / "private-acquisitions"
    records.mkdir(parents=True)
    private.mkdir()
    cases_path = tmp_path / "cases.json"
    cases_path.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "case_id": "case-1",
                        "challenge_type": "unsupported_claim",
                        "claims": [{"claim_id": "claim-1", "importance": 3}],
                    }
                ]
            }
        )
    )
    index = 0
    for repetition in range(3):
        for policy in POLICIES:
            name = f"record-{index}.json"
            candidate = {
                "candidate_id": "source-1",
                "url": "https://private.example/source",
                "title": "Private title",
                "search_origins": [{"attempt": 0, "rank": 1}],
                "acquisition_status": "acquired",
                "admitted": True,
                "admission_reason": "admitted_within_source_limit",
                "operational_assessment": {
                    "supports_or_challenges": True,
                    "relevant_gap_ids": ["claim-1"],
                },
            }
            if omit_reason and index == 0:
                candidate.pop("admission_reason")
            record = {
                "status": "completed",
                "case_id": "case-1",
                "policy": policy,
                "repetition": repetition,
                "attempts": [{"query": "private query"}],
                "proposals": [],
                "candidates": [candidate],
                "stop_reason": "fixed_query_complete",
                "metrics": {
                    "model_calls": 1,
                    "elapsed_ms": 100 + index,
                    "closed_weight": 3,
                    "total_weight": 3,
                },
            }
            (records / name).write_text(json.dumps(record))
            if not (omit_private and index == 0):
                (private / name).write_text(
                    json.dumps({"reviewed_excerpt": "private excerpt"})
                )
            index += 1
    return run_dir, cases_path


def test_complete_accounting_proves_coverage_without_public_source_content(tmp_path):
    run_dir, cases_path = write_fixture(tmp_path)
    result = build_accounting(
        [run_dir],
        [cases_path],
        SUMMARY,
        summary_sha256="a" * 64,
    )
    assert result["complete"]
    assert result["totals"] == {
        "expected_trials": 15,
        "observed_trials": 15,
        "completed_trials": 15,
        "failed_trials": 0,
        "executed_queries": 15,
        "candidate_records": 15,
        "search_result_sightings": 15,
        "acquired_candidates": 15,
        "admitted_candidates": 15,
        "excluded_candidates": 0,
        "source_to_claim_links": 15,
    }
    assert result["variation"]["by_policy"]["fixed"]["elapsed_ms"] == {
        "count": 3,
        "min": 100,
        "median": 105,
        "max": 110,
    }
    encoded = json.dumps(result)
    assert "private query" not in encoded
    assert "private.example" not in encoded
    assert "private excerpt" not in encoded


def test_missing_private_file_and_disposition_fail_closed(tmp_path):
    run_dir, cases_path = write_fixture(tmp_path, omit_private=True, omit_reason=True)
    result = build_accounting(
        [run_dir],
        [cases_path],
        SUMMARY,
        summary_sha256="a" * 64,
    )
    assert not result["complete"]
    assert not result["completion_gates"]["one_private_acquisition_file_per_record"]
    assert not result["completion_gates"]["no_missing_accounting_fields"]
    assert result["missing_accounting"] == {"candidate_admission_reason": 1}
