import copy

import pytest

from scripts.analyze_w10_adjudication import (
    apply_adjudication,
    claim_observation_id,
    sensitivity_summary,
    source_observation_id,
)


def record(policy="fixed"):
    return {
        "status": "completed",
        "case_id": "case-1",
        "policy": policy,
        "repetition": 0,
        "attempts": [{"query": "initial"}],
        "proposals": [],
        "candidates": [
            {
                "candidate_id": "source-1",
                "acquisition_status": "acquired",
                "admitted": True,
                "operational_assessment": {
                    "supports_or_challenges": True,
                    "quality": {
                        "currency": 2,
                        "relevance": 2,
                        "authority": 1,
                        "accuracy": 2,
                        "purpose": 2,
                    },
                },
            }
        ],
        "gap_results": [{"gap_id": "claim-1", "status": "closed"}],
        "metrics": {
            "closed_weight": 3,
            "total_weight": 3,
            "searches": 1,
            "model_calls": 1,
            "admitted_count": 1,
            "elapsed_ms": 10,
        },
    }


def adjudication(item_record):
    return {
        "schema_version": "enterprise-evaluation/w10-adjudication-public/1",
        "reviewer_kind": "agent",
        "blind_to_policy_and_repetition": True,
        "items": [
            {
                "observation_id": source_observation_id(item_record, "source-1"),
                "item_type": "source_grade",
                "selection_reasons": ["sample"],
                "verdict": {
                    "useful": False,
                    "currency": 2,
                    "relevance": 2,
                    "authority": 2,
                    "accuracy": 2,
                    "purpose": 2,
                },
            },
            {
                "observation_id": claim_observation_id(item_record, "claim-1"),
                "item_type": "claim_closure",
                "selection_reasons": ["disagreement"],
                "verdict": {"claim_status": "open"},
            },
        ],
    }


def test_adjudication_overrides_only_selected_judgments_and_reports_agreement():
    original = record()
    adjusted, anchor, agreement = apply_adjudication(
        [original], [], adjudication(original)
    )
    assert not anchor
    assert original["candidates"][0]["operational_assessment"]["supports_or_challenges"]
    assert not adjusted[0]["candidates"][0]["operational_assessment"][
        "supports_or_challenges"
    ]
    assert adjusted[0]["gap_results"][0]["status"] == "open"
    assert agreement["source_usefulness"]["agreement"] == 0
    assert agreement["source_quality_components"]["agreement"] == 0.8
    assert agreement["claim_status"]["agreement"] == 0


def test_foreign_observation_fails_closed():
    item = record()
    review = adjudication(item)
    review["items"][0]["observation_id"] = "foreign"
    with pytest.raises(ValueError, match="did not map"):
        apply_adjudication([item], [], review)


def test_sensitivity_can_change_selected_policy_without_rewriting_primary():
    fixed = record("fixed")
    full = copy.deepcopy(record("full"))
    full["metrics"]["closed_weight"] = 3
    cases = {
        "case-1": {
            "challenge_type": "unsupported_claim",
            "claims": [{"claim_id": "claim-1", "importance": 3}],
        }
    }
    positions = {("case-1", 0, "fixed"): 1, ("case-1", 0, "full"): 2}
    result = sensitivity_summary(
        [fixed, full],
        [copy.deepcopy(fixed), copy.deepcopy(full)],
        cases,
        cases,
        positions,
        positions,
    )
    assert result["decision"] == "retain_fixed_default"
