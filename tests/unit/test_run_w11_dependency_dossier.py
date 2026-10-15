from copy import deepcopy

from scripts.run_w11_dependency_dossier import assess


def dossier() -> dict:
    return {
        "state": "partial",
        "partial": True,
        "budget": {
            "used_adapter_calls": 3,
            "max_adapter_calls": 6,
            "captured_results": 1,
            "max_results": 10,
        },
        "resolved_package_identity": {
            "status": "resolved",
            "version_match": "different",
            "observed_versions": ["2"],
        },
        "repository_identity": {
            "status": "requested",
            "basis": "caller_supplied",
            "proven_package_ownership": False,
        },
        "sections": {
            "package_information": {"state": "available", "coverage": []},
            "repository_records": {"state": "failed", "coverage": []},
            "advisory_leads": {
                "state": "empty",
                "coverage": [],
                "applicability": {"status": "not_evaluated"},
            },
        },
        "missing_source_coverage": [{"section": "repository_records", "state": "failed"}],
        "limitations": ["one", "two", "three"],
    }


def test_assess_accepts_explicit_partial_and_stable_honest_limits() -> None:
    report = dossier()
    value = assess(
        {"job_id": "job", "replay": False},
        {"job_id": "job", "replay": True},
        report,
        deepcopy(report),
    )
    assert value["hard_gate_passed"] is True
    assert value["package_ownership_proven"] is False
    assert value["advisory_applicability"] == "not_evaluated"


def test_assess_rejects_missing_coverage_that_hides_a_failed_section() -> None:
    report = dossier()
    report["missing_source_coverage"] = []
    value = assess(
        {"job_id": "job"},
        {"job_id": "job", "replay": True},
        report,
        deepcopy(report),
    )
    assert value["hard_gate_passed"] is False
