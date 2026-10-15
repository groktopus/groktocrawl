"""Deterministic W12.4 evidence-obligation continuation boundary."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, model_validator

from .knowledge import Identity, Record, Text

ObligationKind = Literal[
    "support",
    "primary_source",
    "freshness",
    "independence",
    "contradiction",
    "entity_identity",
]


class EvidenceObligation(Record):
    obligation_id: Identity
    kind: ObligationKind
    importance: int = Field(strict=True, ge=1, le=3)
    closure_rule: Text
    required_candidate_ids: tuple[Identity, ...] = Field(min_length=1)


class ReplayCandidate(Record):
    candidate_id: Identity
    query_id: Identity
    canonical_id: Identity
    publisher_id: Identity
    closes_obligation_ids: tuple[Identity, ...] = ()
    acquired: bool = True
    derivative: bool = False
    material: bool = True


class ReplayQuery(Record):
    query_id: Identity
    purpose: Literal["initial", "generic", "obligation"]
    obligation_id: Identity | None = None
    candidate_ids: tuple[Identity, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def bind_obligation_query(self) -> Self:
        if (self.purpose == "obligation") != (self.obligation_id is not None):
            raise ValueError("only obligation queries name an obligation")
        return self


class ObligationReplayCase(Record):
    case_id: Identity
    stratum: Literal[
        "easy_stop",
        "missing_primary",
        "stale",
        "source_duplication",
        "contradiction",
        "entity_identity",
        "low_rank",
        "unanswerable",
    ]
    question: Text
    obligations: tuple[EvidenceObligation, ...] = Field(min_length=1)
    queries: tuple[ReplayQuery, ...] = Field(min_length=1)
    candidates: tuple[ReplayCandidate, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_replay_graph(self) -> Self:
        obligation_ids = {item.obligation_id for item in self.obligations}
        query_ids = {item.query_id for item in self.queries}
        candidate_ids = {item.candidate_id for item in self.candidates}
        if len(obligation_ids) != len(self.obligations):
            raise ValueError("obligation IDs must be unique")
        if len(query_ids) != len(self.queries):
            raise ValueError("query IDs must be unique")
        if len(candidate_ids) != len(self.candidates):
            raise ValueError("candidate IDs must be unique")
        if sum(query.purpose == "initial" for query in self.queries) != 1:
            raise ValueError("case requires exactly one initial query")
        for query in self.queries:
            if query.obligation_id and query.obligation_id not in obligation_ids:
                raise ValueError("query names an unknown obligation")
            if not set(query.candidate_ids) <= candidate_ids:
                raise ValueError("query names an unknown candidate")
        for candidate in self.candidates:
            if candidate.query_id not in query_ids:
                raise ValueError("candidate names an unknown query")
            if not set(candidate.closes_obligation_ids) <= obligation_ids:
                raise ValueError("candidate closes an unknown obligation")
        for obligation in self.obligations:
            if not set(obligation.required_candidate_ids) <= candidate_ids:
                raise ValueError("closure rule names an unknown candidate")
        return self


class PolicyOutcome(Record):
    policy: Literal["fixed", "w10_diagnostic", "obligation"]
    executed_query_ids: tuple[Identity, ...]
    admitted_candidate_ids: tuple[Identity, ...]
    closed_obligation_ids: tuple[Identity, ...]
    continuation_after_last_gain: int
    stop_reason: Literal[
        "fixed_complete", "all_obligations_closed", "no_material_gain", "budget"
    ]


def _admit(
    candidate: ReplayCandidate,
    admitted_canonical: set[str],
    admitted_publishers: set[str],
) -> bool:
    if not candidate.acquired or candidate.derivative or not candidate.material:
        return False
    if candidate.canonical_id in admitted_canonical:
        return False
    # A second item from one publisher is admissible only when it closes a named gap.
    return (
        bool(candidate.closes_obligation_ids)
        or candidate.publisher_id not in admitted_publishers
    )


def execute_policy(
    case: ObligationReplayCase,
    policy: Literal["fixed", "w10_diagnostic", "obligation"],
    *,
    max_queries: int = 3,
) -> PolicyOutcome:
    """Replay identical candidates while varying only continuation policy."""
    candidates = {item.candidate_id: item for item in case.candidates}
    initial = next(item for item in case.queries if item.purpose == "initial")
    executed = [initial.query_id]
    admitted: list[str] = []
    closed: set[str] = set()
    admitted_canonical: set[str] = set()
    admitted_publishers: set[str] = set()
    after_last_gain = 0

    def run_query(query: ReplayQuery) -> int:
        before = set(closed)
        for candidate_id in query.candidate_ids:
            candidate = candidates[candidate_id]
            if _admit(candidate, admitted_canonical, admitted_publishers):
                admitted.append(candidate_id)
                admitted_canonical.add(candidate.canonical_id)
                admitted_publishers.add(candidate.publisher_id)
                for obligation in case.obligations:
                    if set(obligation.required_candidate_ids) <= set(admitted):
                        closed.add(obligation.obligation_id)
        return len(closed - before)

    run_query(initial)
    stop: Literal[
        "fixed_complete", "all_obligations_closed", "no_material_gain", "budget"
    ]
    if policy == "fixed":
        stop = "fixed_complete"
    elif policy == "w10_diagnostic":
        stop = "budget"
        for query in (item for item in case.queries if item.purpose == "generic"):
            if len(executed) >= max_queries:
                break
            executed.append(query.query_id)
            gain = run_query(query)
            if gain == 0:
                after_last_gain += 1
        if len(closed) == len(case.obligations):
            stop = "all_obligations_closed"
    else:
        stop = "budget"
        obligation_queries = {
            item.obligation_id: item
            for item in case.queries
            if item.purpose == "obligation"
        }
        while len(executed) < max_queries:
            open_items = sorted(
                (item for item in case.obligations if item.obligation_id not in closed),
                key=lambda item: (-item.importance, item.obligation_id),
            )
            if not open_items:
                stop = "all_obligations_closed"
                break
            obligation_query = obligation_queries.get(open_items[0].obligation_id)
            if obligation_query is None or obligation_query.query_id in executed:
                stop = "no_material_gain"
                break
            executed.append(obligation_query.query_id)
            gain = run_query(obligation_query)
            if gain == 0:
                after_last_gain += 1
                stop = "no_material_gain"
                break
        if len(closed) == len(case.obligations):
            stop = "all_obligations_closed"
    return PolicyOutcome(
        policy=policy,
        executed_query_ids=tuple(executed),
        admitted_candidate_ids=tuple(admitted),
        closed_obligation_ids=tuple(sorted(closed)),
        continuation_after_last_gain=after_last_gain,
        stop_reason=stop,
    )


def score_outcome(
    case: ObligationReplayCase, outcome: PolicyOutcome
) -> dict[str, float | int]:
    weights = {item.obligation_id: item.importance for item in case.obligations}
    total = sum(weights.values())
    closed_weight = sum(weights[item] for item in outcome.closed_obligation_ids)
    admitted = {item.candidate_id: item for item in case.candidates}
    useful = sum(
        bool(admitted[item].closes_obligation_ids)
        for item in outcome.admitted_candidate_ids
    )
    return {
        "weighted_closure": closed_weight / total,
        "admitted_precision": useful / len(outcome.admitted_candidate_ids)
        if outcome.admitted_candidate_ids
        else 1.0,
        "queries": len(outcome.executed_query_ids),
        "post_gain_queries": outcome.continuation_after_last_gain,
        "unsupported_high_importance": sum(
            item.importance == 3
            and item.obligation_id not in outcome.closed_obligation_ids
            for item in case.obligations
        ),
    }
