"""Fixture-only audited rendering gate. No persistence or semantic oracle."""

import json
from datetime import datetime
from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from .knowledge import Digest, Identity, Record, Text, text_digest
from .verification import FixtureVerificationSet, FixtureVerifier

Layer = Literal["summary", "analysis", "dossier"]


def fixture_json(value: Record) -> str:
    """Local fixture serialization; not a portable JCS contract."""
    return json.dumps(
        value.model_dump(mode="json"),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )


class QuestionOutcome(Record):
    question_id: Identity
    question: Text
    status: Literal["answered", "unresolved"]
    report_claim_id: Identity


class Conflict(Record):
    conflict_id: Identity
    claim_id: Identity
    question_id: Identity
    evidence_ids: tuple[Identity, ...] = Field(min_length=2, max_length=1000)
    reason: Text


class FixtureResearch(Record):
    objective: Text | None = None
    verifications: FixtureVerificationSet
    questions: tuple[QuestionOutcome, ...] = Field(min_length=1, max_length=100)
    conflicts: tuple[Conflict, ...] = Field(max_length=100)

    @model_validator(mode="after")
    def complete_context(self) -> Self:
        structure = self.verifications.structure
        claim_ids = {claim.claim_id for claim in structure.claims}
        evidence_ids = {item.evidence_id for item in structure.evidence}
        ids = [q.question_id for q in self.questions] + [
            c.conflict_id for c in self.conflicts
        ]
        if len(ids) != len(set(ids)):
            raise ValueError("question/conflict identities must be unique")
        if any(q.report_claim_id not in claim_ids for q in self.questions):
            raise ValueError("question report must reference a local claim")
        unresolved = {q.question_id for q in self.questions if q.status == "unresolved"}
        for conflict in self.conflicts:
            if conflict.question_id not in unresolved:
                raise ValueError("conflict must belong to an unresolved question")
            if (
                conflict.claim_id not in claim_ids
                or not set(conflict.evidence_ids) <= evidence_ids
            ):
                raise ValueError("conflict references unavailable claim/evidence")
            if len(set(conflict.evidence_ids)) != len(conflict.evidence_ids):
                raise ValueError("conflict evidence must be distinct")
        for edge in structure.relationships:
            if edge.kind == "contradicts" and not any(
                c.claim_id == edge.target_id and edge.source_id in c.evidence_ids
                for c in self.conflicts
            ):
                raise ValueError("contradictory evidence requires an explicit conflict")
        return self

    def coverage(self) -> Literal["complete", "partial", "insufficient"]:
        answered = sum(q.status == "answered" for q in self.questions)
        return (
            "complete"
            if answered == len(self.questions)
            else "partial"
            if answered
            else "insufficient"
        )


class Statement(Record):
    text: Text
    claim_ids: tuple[Identity, ...] = Field(min_length=1, max_length=100)
    evidence_ids: tuple[Identity, ...] = Field(max_length=1000)


class FixtureArtifact(Record):
    artifact_id: Identity
    layer: Layer
    statements: tuple[Statement, ...] = Field(min_length=1, max_length=100)
    question_ids: tuple[Identity, ...] = Field(min_length=1, max_length=100)
    conflict_ids: tuple[Identity, ...] = Field(max_length=100)


class RenderInput(Record):
    schema_version: Literal["fixture-render-input/1"]
    research: FixtureResearch
    artifact_set_id: Identity
    renderer_version: Identity
    artifact: FixtureArtifact
    auditor: FixtureVerifier

    def rendered_text(self) -> str:
        text = "\n".join(statement.text for statement in self.artifact.statements)
        if self.artifact.layer == "dossier":
            text += "\n\n" + fixture_json(self.research)
        return text

    def input_digest(self) -> str:
        checked = RenderInput.model_validate(self)
        return text_digest("fixture-render-input/1\0" + fixture_json(checked))


class FixtureRenderAudit(Record):
    audit_id: Identity
    checked_input: RenderInput
    checked_input_digest: Digest
    verdict: Literal["pass", "fail", "indeterminate"]
    checked_output_digest: Digest
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
    def bind_bytes(self) -> Self:
        if self.checked_input_digest != self.checked_input.input_digest():
            raise ValueError("render audit input digest mismatch")
        if self.checked_output_digest != text_digest(
            self.checked_input.rendered_text()
        ):
            raise ValueError("render audit output digest mismatch")
        return self


class FixturePublication(Record):
    """Validated ephemeral fixture output, not a durable publication receipt."""

    schema_version: Literal["fixture-publication/1"]
    audits: tuple[FixtureRenderAudit, ...] = Field(min_length=3, max_length=3)


def _eligible_claims(research: FixtureResearch) -> set[str]:
    eligible: set[str] = set()
    contested = {conflict.claim_id for conflict in research.conflicts}
    for claim in research.verifications.structure.claims:
        records = [
            r
            for r in research.verifications.records
            if r.checked_input.subject_id == claim.claim_id
        ]
        checks = {r.checked_input.check_type for r in records}
        support_evidence = {
            identity
            for record in records
            if record.checked_input.check_type == "semantic_support"
            for identity in record.checked_input.evidence_ids
        }
        fresh_evidence = {
            identity
            for record in records
            if record.checked_input.check_type == "freshness"
            for identity in record.checked_input.evidence_ids
        }
        if (
            claim.assessment == "supported"
            and claim.claim_id not in contested
            and checks == {"semantic_support", "freshness", "conflict_coverage"}
            and all(r.verdict == "pass" for r in records)
            and support_evidence <= fresh_evidence
        ):
            eligible.add(claim.claim_id)
    return eligible


def validate_fixture_publication(
    payload: object,
    *,
    research: FixtureResearch,
    artifact_set_id: str,
    renderer_version: str,
    auditor: FixtureVerifier,
) -> FixturePublication:
    """Compare against caller-established research; require all checks and layers.

    Fixture verdicts are forgeable test expectations. This is not suitable for
    accepting untrusted verifier output or certifying real-world factual accuracy.
    """
    expected = FixtureResearch.model_validate(research)
    result = FixturePublication.model_validate(payload)
    layers = [a.checked_input.artifact.layer for a in result.audits]
    if set(layers) != {"summary", "analysis", "dossier"}:
        raise ValueError("publication requires one artifact per layer")
    identities = [a.audit_id for a in result.audits] + [
        a.checked_input.artifact.artifact_id for a in result.audits
    ]
    if len(identities) != len(set(identities)):
        raise ValueError("audit/artifact identities must be unique")
    eligible = _eligible_claims(expected)
    evidence = {e.evidence_id for e in expected.verifications.structure.evidence}
    question_ids = {q.question_id for q in expected.questions}
    report_claims = {q.report_claim_id for q in expected.questions}
    conflict_ids = {c.conflict_id for c in expected.conflicts}
    for audit in result.audits:
        context = audit.checked_input
        if (
            context.research != expected
            or context.artifact_set_id != artifact_set_id
            or context.renderer_version != renderer_version
            or context.auditor != auditor
        ):
            raise ValueError("render audit differs from expected context")
        if audit.verdict != "pass":
            raise ValueError("every render audit must pass")
        artifact = context.artifact
        if (
            set(artifact.question_ids) != question_ids
            or set(artifact.conflict_ids) != conflict_ids
        ):
            raise ValueError("every layer must preserve all questions and conflicts")
        used_claims: set[str] = set()
        for statement in artifact.statements:
            if not set(statement.claim_ids) <= eligible:
                raise ValueError(
                    "statement references an ineligible or unavailable claim"
                )
            if not set(statement.evidence_ids) <= evidence:
                raise ValueError("statement cites unavailable evidence")
            expected_evidence = {
                identity
                for record in expected.verifications.records
                if record.checked_input.subject_id in statement.claim_ids
                and record.checked_input.check_type == "semantic_support"
                for identity in record.checked_input.evidence_ids
            }
            if set(statement.evidence_ids) != expected_evidence:
                raise ValueError(
                    "statement citations differ from checked support evidence"
                )
            used_claims.update(statement.claim_ids)
        if not report_claims <= used_claims:
            raise ValueError("layer omits a required question's report claim")
    return result
