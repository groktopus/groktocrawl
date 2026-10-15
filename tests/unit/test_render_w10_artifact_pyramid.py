from scripts.render_w10_artifact_pyramid import render


def fixture_inputs():
    summary = {
        "schema_version": "enterprise-evaluation/w10-summary/1",
        "complete": True,
        "decision": "retain_fixed_default",
        "selected_challenge_types": [],
        "challenge": {
            "summaries": {
                "fixed": {
                    "weighted_closure": 0.5,
                    "precision": 0.75,
                    "trials": 3,
                }
            }
        },
        "anchor": {
            "gate": {
                "passed": True,
                "closure_delta": 0.01,
                "precision_delta": -0.01,
            }
        },
        "sensitivity": {
            "leave_one_challenge_case_out": {"case-1": []},
            "equal_claim_weights": {"selected_types": []},
        },
    }
    accounting = {
        "schema_version": "enterprise-evaluation/w10-public-accounting/1",
        "complete": True,
        "inputs": {
            "summary_sha256": "a" * 64,
            "private_acquisition_manifest": {"files": 3},
        },
        "totals": {
            "expected_trials": 3,
            "observed_trials": 3,
            "completed_trials": 3,
            "failed_trials": 0,
            "executed_queries": 3,
            "candidate_records": 9,
            "search_result_sightings": 12,
            "acquired_candidates": 8,
            "admitted_candidates": 6,
            "excluded_candidates": 3,
            "source_to_claim_links": 7,
        },
        "variation": {
            "by_policy": {
                "fixed": {
                    "trials": 3,
                    "searches": {"median": 1},
                    "admitted": {"median": 2},
                    "elapsed_ms": {"median": 100},
                }
            }
        },
    }
    adjudication = {
        "schema_version": "enterprise-evaluation/w10-adjudication-analysis/1",
        "analysis_role": "declared sensitivity; frozen model-graded primary is unchanged",
        "primary_summary_sha256": "a" * 64,
        "decision_changed": False,
        "primary_decision": "retain_fixed_default",
        "adjudicated_sensitivity_decision": "retain_fixed_default",
        "agreement": {
            "source_usefulness": {"reviewed": 2, "agreement": 0.5},
            "source_quality_components": {"reviewed": 10, "agreement": 0.8},
            "claim_status": {"reviewed": 1, "agreement": 1.0},
        },
    }
    return summary, accounting, adjudication


def test_render_produces_readable_three_level_pyramid_with_sources():
    summary, accounting, adjudication = fixture_inputs()
    documents = render(
        summary,
        accounting,
        adjudication,
        input_digests={
            "summary": "a" * 64,
            "accounting": "b" * 64,
            "adjudication": "c" * 64,
        },
    )
    assert set(documents) == {
        "00-index.md",
        "01-summary/findings.md",
        "02-analysis/policy-effects.md",
        "02-analysis/boundaries-and-sensitivities.md",
        "03-dossiers/accounting.md",
        "03-dossiers/adjudication.md",
        "03-dossiers/method.md",
    }
    assert (
        "Keep fixed-query retrieval as the default"
        in documents["01-summary/findings.md"]
    )
    assert all(
        document.rstrip().endswith("w10-research-log.md)") or "## SOURCES" in document
        for document in documents.values()
    )
    assert all("## SOURCES" in document for document in documents.values())


def test_render_rejects_an_unbound_adjudication():
    summary, accounting, adjudication = fixture_inputs()
    adjudication["primary_summary_sha256"] = "wrong"
    try:
        render(
            summary,
            accounting,
            adjudication,
            input_digests={
                "summary": "a" * 64,
                "accounting": "b" * 64,
                "adjudication": "c" * 64,
            },
        )
    except ValueError as error:
        assert "does not bind" in str(error)
    else:
        raise AssertionError("unbound adjudication was accepted")
