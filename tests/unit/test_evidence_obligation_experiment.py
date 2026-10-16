import pytest
from agent.experimental.evidence_obligation_experiment import (
    EvidenceObligation,
    ObligationReplayCase,
    ReplayCandidate,
    ReplayQuery,
    execute_policy,
    score_outcome,
)
from pydantic import ValidationError


def case() -> ObligationReplayCase:
    return ObligationReplayCase(
        case_id="case-1",
        stratum="missing_primary",
        question="What does the primary record establish?",
        obligations=(
            EvidenceObligation(
                obligation_id="primary",
                kind="primary_source",
                importance=3,
                closure_rule="Acquire the primary record.",
                required_candidate_ids=("primary-record",),
            ),
        ),
        queries=(
            ReplayQuery(
                query_id="initial", purpose="initial", candidate_ids=("summary",)
            ),
            ReplayQuery(
                query_id="generic", purpose="generic", candidate_ids=("duplicate",)
            ),
            ReplayQuery(
                query_id="primary-query",
                purpose="obligation",
                obligation_id="primary",
                candidate_ids=("primary-record",),
            ),
        ),
        candidates=(
            ReplayCandidate(
                candidate_id="summary",
                query_id="initial",
                canonical_id="summary",
                publisher_id="press",
            ),
            ReplayCandidate(
                candidate_id="duplicate",
                query_id="generic",
                canonical_id="duplicate",
                publisher_id="press",
                derivative=True,
            ),
            ReplayCandidate(
                candidate_id="primary-record",
                query_id="primary-query",
                canonical_id="record",
                publisher_id="authority",
                closes_obligation_ids=("primary",),
            ),
        ),
    )


def test_obligation_policy_closes_named_gap_without_generic_work() -> None:
    outcome = execute_policy(case(), "obligation")
    assert outcome.closed_obligation_ids == ("primary",)
    assert outcome.executed_query_ids == ("initial", "primary-query")
    assert outcome.stop_reason == "all_obligations_closed"
    assert score_outcome(case(), outcome)["weighted_closure"] == 1


def test_fixed_and_generic_diagnostic_leave_primary_gap_open() -> None:
    assert execute_policy(case(), "fixed").closed_obligation_ids == ()
    diagnostic = execute_policy(case(), "w10_diagnostic")
    assert diagnostic.closed_obligation_ids == ()
    assert diagnostic.continuation_after_last_gain == 1


def test_zero_gain_stops_obligation_policy() -> None:
    payload = case().model_dump(mode="json")
    payload["queries"][2]["candidate_ids"] = ["duplicate"]
    payload["candidates"][1]["query_id"] = "primary-query"
    payload["obligations"][0]["required_candidate_ids"] = ["duplicate"]
    broken = ObligationReplayCase.model_validate(payload)
    outcome = execute_policy(broken, "obligation")
    assert outcome.stop_reason == "no_material_gain"
    assert outcome.continuation_after_last_gain == 1


def test_replay_graph_rejects_unknown_candidate() -> None:
    payload = case().model_dump(mode="json")
    payload["queries"][0]["candidate_ids"] = ["missing"]
    with pytest.raises(ValidationError, match="unknown candidate"):
        ObligationReplayCase.model_validate(payload)
