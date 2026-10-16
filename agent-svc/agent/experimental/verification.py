"""Input-bound fixture verdicts, not a semantic oracle or publication gate.

Only fixture expectations are accepted here. Human/model verifier authentication,
append-only storage and render audit are deliberately not simulated by this format.
"""

import json
from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import Field, field_validator, model_validator

from .knowledge import Digest, Identity, KnowledgeStructure, Record, Text, text_digest


class FixtureVerifier(Record):
    kind: Literal["fixture_expectation"]
    identity: Identity
    version: Identity


class FreshnessBasis(Record):
    snapshot_id: Identity
    basis: Literal["published_at", "effective_at", "historical_snapshot", "unknown"]
    max_age_seconds: Annotated[int, Field(strict=True, ge=0, le=315_576_000)]
    reason: Text


class FreshnessContext(Record):
    policy_version: Identity
    evaluated_at: datetime
    as_of: datetime
    sources: tuple[FreshnessBasis, ...] = Field(min_length=1, max_length=100)

    @field_validator("evaluated_at", "as_of")
    @classmethod
    def utc_timestamp(cls, value: datetime) -> datetime:
        offset = value.utcoffset()
        if offset is None or offset.total_seconds() != 0:
            raise ValueError("freshness times must have explicit UTC offsets")
        return value


class VerificationInput(Record):
    """Full structural context prevents a quote-only verdict from hiding context."""

    schema_version: Literal["fixture-verification-input/1"]
    structure: KnowledgeStructure
    subject_id: Identity
    check_type: Literal[
        "semantic_support", "freshness", "conflict_coverage", "assessment"
    ]
    policy_version: Identity
    verifier: FixtureVerifier
    evidence_ids: tuple[Identity, ...] = Field(max_length=1000)
    freshness: FreshnessContext | None = None

    @model_validator(mode="after")
    def local_references(self) -> Self:
        if self.subject_id not in {claim.claim_id for claim in self.structure.claims}:
            raise ValueError("verification subject must be a claim in this revision")
        available = {item.evidence_id for item in self.structure.evidence}
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("verification evidence references must be unique")
        if not set(self.evidence_ids) <= available:
            raise ValueError("verification references unavailable evidence")
        if self.check_type == "freshness":
            context = self.freshness
            if context is None:
                raise ValueError("freshness check requires typed context")
            if context.policy_version != self.policy_version:
                raise ValueError("freshness policy differs from verification policy")
            if context.as_of != self.structure.as_of:
                raise ValueError("freshness as-of differs from research constraint")
            if context.evaluated_at < context.as_of:
                raise ValueError("freshness cannot evaluate a future as-of constraint")
            snapshots = {
                item.snapshot_id
                for item in self.structure.evidence
                if item.evidence_id in self.evidence_ids
            }
            references = [item.snapshot_id for item in context.sources]
            if len(set(references)) != len(references) or set(references) != snapshots:
                raise ValueError(
                    "freshness must cover exactly the referenced snapshots"
                )
        elif self.freshness is not None:
            raise ValueError("freshness context only belongs on freshness checks")
        return self

    def freshness_allows_pass(self) -> bool:
        """Necessary temporal constraints only; never establishes semantic truth."""
        if self.freshness is None:
            return True
        snapshots = {item.snapshot_id: item for item in self.structure.snapshots}
        claim = next(c for c in self.structure.claims if c.claim_id == self.subject_id)
        for source in self.freshness.sources:
            snapshot = snapshots[source.snapshot_id]
            if snapshot.retrieved_at > self.freshness.evaluated_at:
                return False
            if source.basis == "unknown":
                return False
            if source.basis == "historical_snapshot":
                if claim.temporal_scope != "historical":
                    return False
                date = snapshot.retrieved_at
            else:
                recorded = getattr(snapshot, source.basis)
                if recorded is None:
                    return False
                date = recorded.value
            age = (self.freshness.as_of - date).total_seconds()
            if not 0 <= age <= source.max_age_seconds:
                return False
        return True

    def input_digest(self) -> str:
        """Prototype-specific JSON digest; NOT JCS or a whole-IR interchange hash."""
        checked = VerificationInput.model_validate(self)
        encoded = json.dumps(
            checked.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        return text_digest("fixture-verification-input/1\0" + encoded)


class FixtureVerification(Record):
    verification_id: Identity
    checked_input: VerificationInput
    checked_input_digest: Digest
    verdict: Literal["pass", "fail", "indeterminate"]
    checked_at: datetime
    reason: Text

    @field_validator("checked_at")
    @classmethod
    def utc_timestamp(cls, value: datetime) -> datetime:
        offset = value.utcoffset()
        if offset is None or offset.total_seconds() != 0:
            raise ValueError("checked_at must have an explicit UTC offset")
        return value

    @model_validator(mode="after")
    def exact_input(self) -> Self:
        if self.checked_input.check_type == "assessment":
            raise ValueError("assessment is not a verification verdict")
        if self.checked_input_digest != self.checked_input.input_digest():
            raise ValueError("verification input digest mismatch")
        context = self.checked_input.freshness
        if context is not None and context.evaluated_at != self.checked_at:
            raise ValueError("freshness evaluated time differs from checked time")
        if self.verdict == "pass" and not self.checked_input.freshness_allows_pass():
            raise ValueError("freshness basis cannot authorize a passing verdict")
        return self


class FixtureAssessment(Record):
    assessment_id: Identity
    checked_input: VerificationInput
    checked_input_digest: Digest
    outcome: Literal["supported", "contested", "insufficient", "refuted"]
    checked_at: datetime
    reason: Text

    @field_validator("checked_at")
    @classmethod
    def utc_timestamp(cls, value: datetime) -> datetime:
        offset = value.utcoffset()
        if offset is None or offset.total_seconds() != 0:
            raise ValueError("assessment time must have an explicit UTC offset")
        return value

    @model_validator(mode="after")
    def exact_assessment(self) -> Self:
        if self.checked_input.check_type != "assessment":
            raise ValueError("assessment requires an assessment input")
        if self.checked_input_digest != self.checked_input.input_digest():
            raise ValueError("assessment input digest mismatch")
        return self


class AssessmentLink(Record):
    claim_id: Identity
    assessment_ids: tuple[Identity, ...] = Field(min_length=1, max_length=100)


class FixtureVerificationSet(Record):
    """Binds recorded judgments to trusted expected context, never auto-approves."""

    schema_version: Literal["fixture-verifications/1"]
    structure: KnowledgeStructure
    policy_version: Identity
    verifier: FixtureVerifier
    records: tuple[FixtureVerification, ...] = Field(max_length=3000)
    assessments: tuple[FixtureAssessment, ...] = Field(default=(), max_length=3000)
    assessment_links: tuple[AssessmentLink, ...] = Field(default=(), max_length=1000)

    @model_validator(mode="after")
    def bind_records(self) -> Self:
        identities = [record.verification_id for record in self.records]
        if len(set(identities)) != len(identities):
            raise ValueError("verification IDs must be unique")
        entity_ids = {
            *(item.snapshot_id for item in self.structure.snapshots),
            *(item.evidence_id for item in self.structure.evidence),
            *(item.claim_id for item in self.structure.claims),
            *(item.relationship_id for item in self.structure.relationships),
        }
        if entity_ids.intersection(identities):
            raise ValueError("verification IDs must not alias structural entities")
        for record in self.records:
            context = record.checked_input
            if context.structure != self.structure:
                raise ValueError("verification uses different structural context")
            if context.policy_version != self.policy_version:
                raise ValueError("verification uses different policy")
            if context.verifier != self.verifier:
                raise ValueError("verification uses different verifier")
        self._bind_assessments(entity_ids | set(identities))
        return self

    def _bind_assessments(self, occupied: set[str]) -> None:
        assessments = {a.assessment_id: a for a in self.assessments}
        if len(assessments) != len(self.assessments) or occupied.intersection(
            assessments
        ):
            raise ValueError(
                "assessment IDs must be unique and not alias other entities"
            )
        for assessment in self.assessments:
            context = assessment.checked_input
            if (
                context.structure != self.structure
                or context.policy_version != self.policy_version
                or context.verifier != self.verifier
            ):
                raise ValueError("assessment differs from expected context")
        links = {link.claim_id: link for link in self.assessment_links}
        assessed = {
            c.claim_id: c for c in self.structure.claims if c.assessment != "unassessed"
        }
        if len(links) != len(self.assessment_links) or set(links) != set(assessed):
            raise ValueError(
                "each assessed claim requires exactly one assessment link; unassessed claims have none"
            )
        used: set[str] = set()
        for identity, link in links.items():
            if len(set(link.assessment_ids)) != len(link.assessment_ids):
                raise ValueError("assessment references must be unique")
            for record_id in link.assessment_ids:
                record = assessments.get(record_id)
                if record is None or record.checked_input.subject_id != identity:
                    raise ValueError(
                        "assessment link references missing or foreign assessment"
                    )
                if record.outcome != assessed[identity].assessment:
                    raise ValueError("assessment outcome differs from claim status")
                used.add(record_id)
        if used != set(assessments):
            raise ValueError("assessment records must all be linked")


def validate_fixture_verifications(
    payload: object,
    *,
    structure: KnowledgeStructure,
    policy_version: str,
    verifier: FixtureVerifier,
) -> FixtureVerificationSet:
    """Require caller-established input and verifier identity, not payload authority.

    The caller must first validate the structure against trusted scope with
    validate_structure. These fixture verdicts are deliberately forgeable test data,
    not authenticated human approvals or signed semantic verification evidence.
    """
    result = FixtureVerificationSet.model_validate(payload)
    if result.structure != KnowledgeStructure.model_validate(structure):
        raise ValueError("verification set differs from expected structure")
    if result.policy_version != policy_version or result.verifier != verifier:
        raise ValueError("verification set differs from expected policy/verifier")
    return result
