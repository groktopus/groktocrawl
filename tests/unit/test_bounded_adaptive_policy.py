import importlib.util

import pytest
from agent.experimental.bounded_adaptive_policy import (
    CandidateAssessment,
    EvidenceGap,
    QueryProposal,
    WorkState,
    admit_candidate,
    gate_proposal,
    intent_similarity,
    replay_policy_trace,
    stop_reason,
)


def gap(*, closed: bool = False) -> EvidenceGap:
    return EvidenceGap("quality", 3, "Measured escaped-defect quality effect", closed)


def test_proposal_gate_requires_open_gap_novel_intent_and_topic_connection():
    proposal = QueryProposal(
        "escaped defect study coding agents",
        "quality",
        "A measured escaped-defect comparison",
        "missing_support",
    )
    assert gate_proposal(
        proposal,
        original_query="Do coding agents improve delivery quality?",
        gaps=(gap(),),
        prior_queries=("coding agent productivity evidence",),
    ).admitted
    assert not gate_proposal(
        proposal,
        original_query="Do coding agents improve delivery quality?",
        gaps=(gap(closed=True),),
        prior_queries=(),
    ).admitted


def test_proposal_gate_rejects_duplicate_and_drift():
    duplicate = QueryProposal(
        "coding agent productivity evidence",
        "quality",
        "comparison",
        "missing_support",
    )
    assert intent_similarity(duplicate.query, "coding agent productivity evidence") == 1
    assert (
        gate_proposal(
            duplicate,
            original_query="coding agents",
            gaps=(gap(),),
            prior_queries=("coding agent productivity evidence",),
        ).reason
        == "duplicate_intent"
    )
    drift = QueryProposal("weather forecast", "quality", "forecast", "missing_support")
    assert (
        gate_proposal(
            drift,
            original_query="coding agents",
            gaps=(gap(),),
            prior_queries=(),
        ).reason
        == "topic_drift"
    )


def test_candidate_admission_separates_availability_relevance_and_value():
    useful = CandidateAssessment(
        "source-1",
        ("quality",),
        (2, 2, 2, 2, 1),
        "canonical-1",
        "publisher-1",
        True,
        True,
    )
    assert (
        admit_candidate(
            useful,
            admitted_canonical_ids=frozenset(),
            admitted_publishers=frozenset(),
        ).reason
        == "admitted_claim_value"
    )
    assert (
        admit_candidate(
            useful,
            admitted_canonical_ids=frozenset({"canonical-1"}),
            admitted_publishers=frozenset(),
        ).reason
        == "canonical_duplicate"
    )
    derivative = CandidateAssessment(
        "source-2",
        ("quality",),
        (2, 2, 2, 2, 1),
        "canonical-2",
        "publisher-2",
        True,
        True,
        derivative=True,
    )
    assert (
        admit_candidate(
            derivative,
            admitted_canonical_ids=frozenset(),
            admitted_publishers=frozenset(),
        ).reason
        == "derivative_publisher"
    )


def test_stop_order_is_reproducible_from_recorded_state():
    open_gap = (gap(),)
    closed_gap = (gap(closed=True),)
    state = WorkState(2, 1, 2, 1_000, 0, False, False, False)
    assert stop_reason(closed_gap, state) == "all_gaps_closed"
    assert stop_reason(open_gap, state) == "no_marginal_gain"
    assert (
        stop_reason(open_gap, WorkState(3, 1, 2, 1_000, 1, False, False, False))
        == "search_limit"
    )


@pytest.mark.skipif(
    importlib.util.find_spec("langgraph") is None,
    reason="LangGraph is installed only in the optional W10 experiment lane",
    owner="repository-maintainer",
    issue="#314",
    classification="retained",
    environment="default test lane excludes the optional LangGraph dependency",
)  # type: ignore[call-arg]
def test_langgraph_trace_preserves_event_order_and_is_deterministic():
    events = (
        {"type": "gap", "gap_id": "quality"},
        {"type": "proposal", "admitted": True},
        {"type": "stop", "reason": "all_gaps_closed"},
    )
    first = replay_policy_trace(events)
    assert len(first) == 3
    assert first == replay_policy_trace(events)


@pytest.mark.skipif(
    importlib.util.find_spec("langgraph") is None,
    reason="LangGraph is installed only in the optional W10 experiment lane",
    owner="repository-maintainer",
    issue="#314",
    classification="retained",
    environment="default test lane excludes the optional LangGraph dependency",
)  # type: ignore[call-arg]
def test_langgraph_trace_scales_recursion_limit_to_a_complete_audit_trail():
    events = tuple({"type": "candidate", "candidate_id": str(i)} for i in range(40))
    assert len(replay_policy_trace(events)) == 40
