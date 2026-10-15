"""Deterministic boundaries for the W10 adaptive-query experiment."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal, TypedDict, cast

ProposalPurpose = Literal[
    "missing_support",
    "contradiction",
    "freshness",
    "primary_source",
    "publisher_independence",
    "entity_identity",
]
StopReason = Literal[
    "all_gaps_closed",
    "no_marginal_gain",
    "search_limit",
    "model_limit",
    "source_limit",
    "time_limit",
    "continue",
    "fixed_query_complete",
    "planner_claimed_complete",
    "no_followup_proposed",
    "no_admitted_proposal",
    "proposal_exhausted",
]

_WORDS = re.compile(r"[a-z0-9][a-z0-9_-]+")
_STOP_WORDS = {
    "about",
    "after",
    "against",
    "from",
    "into",
    "that",
    "their",
    "these",
    "this",
    "what",
    "when",
    "where",
    "which",
    "with",
}


def terms(value: str) -> frozenset[str]:
    return frozenset(
        word for word in _WORDS.findall(value.casefold()) if word not in _STOP_WORDS
    )


def intent_similarity(left: str, right: str) -> float:
    left_terms, right_terms = terms(left), terms(right)
    union = left_terms | right_terms
    return len(left_terms & right_terms) / len(union) if union else 1.0


@dataclass(frozen=True)
class EvidenceGap:
    gap_id: str
    importance: int
    closure_rule: str
    closed: bool = False

    def __post_init__(self) -> None:
        if not self.gap_id or not 1 <= self.importance <= 3:
            raise ValueError("gap must have an ID and importance from 1 to 3")
        if not self.closure_rule.strip():
            raise ValueError("gap must have a closure rule")


@dataclass(frozen=True)
class QueryProposal:
    query: str
    gap_id: str
    predicted_evidence: str
    purpose: ProposalPurpose


@dataclass(frozen=True)
class ProposalDecision:
    admitted: bool
    reason: Literal[
        "admitted",
        "unknown_gap",
        "gap_already_closed",
        "empty_prediction",
        "duplicate_intent",
        "topic_drift",
    ]


@dataclass(frozen=True)
class CandidateAssessment:
    candidate_id: str
    relevant_gap_ids: tuple[str, ...]
    source_quality: tuple[int, int, int, int, int]
    canonical_id: str
    publisher_id: str
    acquired: bool
    supports_or_challenges: bool
    derivative: bool = False
    improves_currency: bool = False
    improves_authority: bool = False
    resolves_contradiction: bool = False

    def __post_init__(self) -> None:
        if len(self.source_quality) != 5 or any(
            not 0 <= value <= 2 for value in self.source_quality
        ):
            raise ValueError("source quality requires five scores from 0 to 2")


@dataclass(frozen=True)
class CandidateDecision:
    admitted: bool
    reason: Literal[
        "admitted_claim_value",
        "admitted_quality_gain",
        "admitted_independent_publisher",
        "unavailable",
        "irrelevant",
        "canonical_duplicate",
        "derivative_publisher",
        "no_marginal_value",
    ]


@dataclass(frozen=True)
class WorkState:
    searches: int
    model_calls: int
    admitted_sources: int
    elapsed_ms: int
    newly_closed_weight: int
    quality_gain: bool
    contradiction_gain: bool
    publisher_gain: bool


def gate_proposal(
    proposal: QueryProposal,
    *,
    original_query: str,
    gaps: tuple[EvidenceGap, ...],
    prior_queries: tuple[str, ...],
) -> ProposalDecision:
    gap = next((item for item in gaps if item.gap_id == proposal.gap_id), None)
    if gap is None:
        return ProposalDecision(False, "unknown_gap")
    if gap.closed:
        return ProposalDecision(False, "gap_already_closed")
    if not proposal.predicted_evidence.strip():
        return ProposalDecision(False, "empty_prediction")
    if any(intent_similarity(proposal.query, prior) >= 0.8 for prior in prior_queries):
        return ProposalDecision(False, "duplicate_intent")
    allowed = terms(original_query) | terms(gap.closure_rule)
    if not terms(proposal.query) & allowed:
        return ProposalDecision(False, "topic_drift")
    return ProposalDecision(True, "admitted")


def admit_candidate(
    candidate: CandidateAssessment,
    *,
    admitted_canonical_ids: frozenset[str],
    admitted_publishers: frozenset[str],
) -> CandidateDecision:
    if not candidate.acquired:
        return CandidateDecision(False, "unavailable")
    if candidate.canonical_id in admitted_canonical_ids:
        return CandidateDecision(False, "canonical_duplicate")
    if not candidate.relevant_gap_ids:
        return CandidateDecision(False, "irrelevant")
    if candidate.derivative:
        return CandidateDecision(False, "derivative_publisher")
    if candidate.supports_or_challenges:
        return CandidateDecision(True, "admitted_claim_value")
    if (
        candidate.improves_currency
        or candidate.improves_authority
        or candidate.resolves_contradiction
    ):
        return CandidateDecision(True, "admitted_quality_gain")
    if candidate.publisher_id not in admitted_publishers:
        return CandidateDecision(True, "admitted_independent_publisher")
    if candidate.publisher_id in admitted_publishers:
        return CandidateDecision(False, "derivative_publisher")
    return CandidateDecision(False, "no_marginal_value")


def stop_reason(
    gaps: tuple[EvidenceGap, ...],
    state: WorkState,
    *,
    max_searches: int = 3,
    max_model_calls: int = 2,
    max_sources: int = 8,
    max_elapsed_ms: int = 90_000,
) -> StopReason:
    if gaps and all(gap.closed for gap in gaps):
        return "all_gaps_closed"
    if state.elapsed_ms >= max_elapsed_ms:
        return "time_limit"
    if state.searches >= max_searches:
        return "search_limit"
    if state.model_calls >= max_model_calls:
        return "model_limit"
    if state.admitted_sources >= max_sources:
        return "source_limit"
    if (
        state.searches > 1
        and state.newly_closed_weight == 0
        and not state.quality_gain
        and not state.contradiction_gain
        and not state.publisher_gain
    ):
        return "no_marginal_gain"
    return "continue"


class _TraceState(TypedDict):
    events: tuple[dict[str, Any], ...]
    cursor: int
    event_digests: tuple[str, ...]


def replay_policy_trace(events: tuple[dict[str, Any], ...]) -> tuple[str, ...]:
    """Carry an immutable policy-event trace through the LangGraph runtime."""
    import hashlib
    import json

    try:
        from langgraph.graph import END, START, StateGraph
    except ImportError as error:
        raise RuntimeError("LangGraph is required for the W10 policy trace") from error

    graph = StateGraph(_TraceState)

    def record(state: _TraceState) -> dict[str, object]:
        event = state["events"][state["cursor"]]
        encoded = json.dumps(event, sort_keys=True, separators=(",", ":")).encode()
        return {
            "cursor": state["cursor"] + 1,
            "event_digests": (
                *state["event_digests"],
                hashlib.sha256(encoded).hexdigest(),
            ),
        }

    def route(state: _TraceState) -> str:
        return "done" if state["cursor"] >= len(state["events"]) else "record"

    graph.add_node("record", record)
    graph.add_edge(START, "record")
    graph.add_conditional_edges("record", route, {"record": "record", "done": END})
    result = graph.compile().invoke(
        {"events": events, "cursor": 0, "event_digests": ()},
        config={"recursion_limit": max(25, len(events) + 5)},
    )
    return cast(tuple[str, ...], result["event_digests"])
