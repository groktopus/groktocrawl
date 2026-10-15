"""Typed W12.5 specialist handoffs and deterministic reconciliation."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, model_validator

from .knowledge import Identity, Record, Text


class SpecialistEvidence(Record):
    evidence_id: Identity
    source_id: Identity
    canonical_source_id: Identity
    publisher_id: Identity
    obligation_id: Identity
    stance: Literal["supports", "challenges"]
    span: Text
    derivative: bool = False


class SpecialistAssignment(Record):
    specialist_id: Identity
    question: Text
    obligation_ids: tuple[Identity, ...] = Field(min_length=1)
    max_sources: int = Field(strict=True, ge=1, le=8)


class SpecialistHandoff(Record):
    schema_version: Literal["specialist-evidence-handoff/1"]
    assignment: SpecialistAssignment
    attempted_source_ids: tuple[Identity, ...]
    acquired_source_ids: tuple[Identity, ...]
    rejected_sources: dict[Identity, Text]
    evidence: tuple[SpecialistEvidence, ...]
    supported_claims: tuple[Text, ...] = ()
    challenged_claims: tuple[Text, ...] = ()
    unresolved_obligation_ids: tuple[Identity, ...] = ()
    source_independence_note: Text
    freshness_note: Text
    limitations: tuple[Text, ...]
    completion_state: Literal["completed", "partial", "failed"]
    calls_used: int = Field(strict=True, ge=0)

    @model_validator(mode="after")
    def validate_authority_and_lineage(self) -> Self:
        assigned = set(self.assignment.obligation_ids)
        acquired = set(self.acquired_source_ids)
        if not acquired <= set(self.attempted_source_ids):
            raise ValueError("acquired sources must have been attempted")
        if not set(self.rejected_sources) <= set(self.attempted_source_ids):
            raise ValueError("rejected sources must have been attempted")
        if any(item.obligation_id not in assigned for item in self.evidence):
            raise ValueError("specialist evidence exceeds its assignment")
        if any(item.source_id not in acquired for item in self.evidence):
            raise ValueError("evidence must bind to an acquired source")
        identities = [item.evidence_id for item in self.evidence]
        if len(identities) != len(set(identities)):
            raise ValueError("evidence IDs must be unique within a handoff")
        if self.completion_state == "failed" and self.evidence:
            raise ValueError("failed handoffs cannot contribute evidence")
        return self


class ReconciledArtifact(Record):
    admitted_evidence: tuple[SpecialistEvidence, ...]
    covered_obligation_ids: tuple[Identity, ...]
    conflicting_obligation_ids: tuple[Identity, ...]
    rejected_evidence: dict[Identity, Text]
    handoff_failures: tuple[Identity, ...]
    total_calls: int


def reconcile_handoffs(
    handoffs: tuple[SpecialistHandoff, ...],
    *,
    allowed_obligation_ids: frozenset[str],
    call_budget: int,
) -> ReconciledArtifact:
    """Validate, deduplicate, and reconcile evidence without publication authority."""
    admitted: list[SpecialistEvidence] = []
    rejected: dict[str, str] = {}
    failures: list[str] = []
    canonical_ids: set[str] = set()
    calls = 0
    for handoff in handoffs:
        checked = SpecialistHandoff.model_validate(handoff)
        calls += checked.calls_used
        if calls > call_budget:
            failures.append(checked.assignment.specialist_id)
            continue
        if checked.completion_state == "failed":
            failures.append(checked.assignment.specialist_id)
            continue
        for item in checked.evidence:
            if item.obligation_id not in allowed_obligation_ids:
                rejected[item.evidence_id] = "unknown_obligation"
            elif item.derivative:
                rejected[item.evidence_id] = "derivative_source"
            elif item.canonical_source_id in canonical_ids:
                rejected[item.evidence_id] = "canonical_duplicate"
            else:
                admitted.append(item)
                canonical_ids.add(item.canonical_source_id)
    stances: dict[str, set[str]] = {item: set() for item in allowed_obligation_ids}
    for item in admitted:
        stances[item.obligation_id].add(item.stance)
    return ReconciledArtifact(
        admitted_evidence=tuple(admitted),
        covered_obligation_ids=tuple(
            sorted(item for item, values in stances.items() if values)
        ),
        conflicting_obligation_ids=tuple(
            sorted(item for item, values in stances.items() if len(values) > 1)
        ),
        rejected_evidence=rejected,
        handoff_failures=tuple(failures),
        total_calls=calls,
    )
