"""Strict input-bound check records; consistency is not proof of execution."""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from .canonical import admit_canonical_json
from .knowledge import Digest, Identity, Text
from .knowledge_context import KnowledgeContext, StrictRecord, Timestamp, moment


class FixtureReviewer(StrictRecord):
    kind: Literal["fixture"]
    identity: Identity
    version: Identity


class ToolReviewer(StrictRecord):
    kind: Literal["tool"]
    identity: Identity
    version: Identity
    configuration_digest: Digest


class ModelReviewer(StrictRecord):
    kind: Literal["model"]
    identity: Identity
    version: Identity
    provider: Identity
    requested_model: Identity
    resolved_model: Identity | None
    prompt_digest: Digest
    generation_configuration_digest: Digest


Reviewer = Annotated[
    FixtureReviewer | ToolReviewer | ModelReviewer, Field(discriminator="kind")
]


class CheckFreshnessBasis(StrictRecord):
    snapshot_id: Identity
    basis: Literal["published_at", "effective_at", "historical_snapshot", "unknown"]
    max_age_seconds: Annotated[int, Field(ge=0, le=315_576_000)]
    reason: Text


class CheckFreshness(StrictRecord):
    evaluated_at: Timestamp
    sources: tuple[CheckFreshnessBasis, ...] = Field(max_length=100)


class KnowledgeCheckInput(StrictRecord):
    schema_version: Literal["knowledge-check-input-prototype/1"]
    input_id: Identity
    check_type: Literal[
        "structural", "semantic_support", "freshness", "conflict_coverage", "assessment"
    ]
    subject_id: Identity
    policy_version: Identity
    reviewer: Reviewer
    context: KnowledgeContext
    evidence_ids: tuple[Identity, ...] = Field(max_length=1000)
    freshness: CheckFreshness | None

    @model_validator(mode="after")
    def exact_projection(self) -> Self:
        context = self.context
        if self.policy_version != context.policy_version:
            raise ValueError("check policy differs from context")
        if self.check_type in {"structural", "conflict_coverage"}:
            if self.subject_id != context.revision_id:
                raise ValueError("whole-context check requires revision subject")
            expected = tuple(e.evidence_id for e in context.evidence)
        else:
            if self.subject_id not in {c.claim_id for c in context.claims}:
                raise ValueError("check requires a local claim subject")
            closure = {self.subject_id}
            while True:
                extended = closure | {
                    r.target_id
                    for r in context.relationships
                    if r.kind == "derived_from" and r.source_id in closure
                }
                if extended == closure:
                    break
                closure = extended
            evidence = {
                r.source_id
                for r in context.relationships
                if r.kind in {"supports", "contradicts"} and r.target_id in closure
            }
            expected = tuple(
                e.evidence_id for e in context.evidence if e.evidence_id in evidence
            )
        if self.evidence_ids != expected:
            raise ValueError("check evidence must match ordered complete closure")
        if self.check_type != "freshness":
            if self.freshness is not None:
                raise ValueError("freshness context belongs only on freshness checks")
        elif self.freshness is None:
            raise ValueError("freshness check requires explicit basis")
        else:
            snapshots = {
                e.snapshot_id for e in context.evidence if e.evidence_id in expected
            }
            order = tuple(
                s.snapshot_id for s in context.snapshots if s.snapshot_id in snapshots
            )
            if tuple(s.snapshot_id for s in self.freshness.sources) != order:
                raise ValueError(
                    "freshness sources must match ordered evidence closure"
                )
            if moment(self.freshness.evaluated_at) < moment(context.created_at):
                raise ValueError("freshness evaluation predates frozen context")
        return self

    def input_digest(self) -> str:
        return admit_canonical_json(
            self.model_dump_json().encode(), schema_version=self.schema_version
        ).digest

    def freshness_allows_pass(self) -> bool:
        if self.freshness is None:
            return True
        if self.context.as_of is None or not self.freshness.sources:
            return False
        as_of = moment(self.context.as_of)
        if as_of > moment(self.freshness.evaluated_at):
            return False
        claim = next(c for c in self.context.claims if c.claim_id == self.subject_id)
        snapshots = {s.snapshot_id: s for s in self.context.snapshots}
        for basis in self.freshness.sources:
            source = snapshots[basis.snapshot_id]
            if basis.basis == "unknown":
                return False
            if basis.basis == "historical_snapshot":
                if claim.temporal_scope != "historical":
                    return False
                date = moment(source.retrieved_at)
            else:
                recorded = getattr(source, basis.basis)
                if recorded is None:
                    return False
                date = moment(recorded.value)
            age = (as_of - date).total_seconds()
            if age < 0 or age > basis.max_age_seconds:
                return False
        return True


class CheckResult(StrictRecord):
    verification_id: Identity
    input_id: Identity
    input_digest: Digest
    verdict: Literal["pass", "fail", "indeterminate"]
    checked_at: Timestamp
    reason: Text


class CheckAssessment(StrictRecord):
    assessment_id: Identity
    input_id: Identity
    input_digest: Digest
    outcome: Literal["supported", "contested", "insufficient", "refuted"]
    checked_at: Timestamp
    reason: Text


class ClaimAssessmentLink(StrictRecord):
    claim_id: Identity
    state: Literal["unassessed", "supported", "contested", "insufficient", "refuted"]
    assessment_ids: tuple[Identity, ...] = Field(max_length=100)
