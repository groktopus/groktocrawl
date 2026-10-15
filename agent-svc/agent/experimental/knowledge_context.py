"""Strict non-result knowledge context; not a complete IR or trust certificate."""

from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from .canonical import MAX_INTEGER
from .knowledge import Digest, Identity, Text, _check_acyclic


def moment(value: str) -> datetime:
    return datetime.fromisoformat(value)


def valid_timestamp(value: str) -> str:
    moment(value)
    return value


Timestamp = Annotated[
    str,
    Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$"),
    AfterValidator(valid_timestamp),
]
Count = Annotated[int, Field(ge=0, le=MAX_INTEGER)]
MediaType = Literal["text/plain", "text/markdown"]


class StrictRecord(BaseModel):
    model_config = ConfigDict(
        strict=True, frozen=True, extra="forbid", revalidate_instances="always"
    )


class RecordedDate(StrictRecord):
    value: Timestamp
    provenance: Text


class ContentReference(StrictRecord):
    scope_id: Identity
    research_id: Identity
    snapshot_id: Identity


class ReferencedSnapshot(StrictRecord):
    snapshot_id: Identity
    canonical_url: Annotated[
        str, Field(max_length=8192, pattern=r"^https?://[^\s/]+(?:/[^\s]*)?$")
    ]
    retrieved_at: Timestamp
    normalization_version: Identity
    media_type: MediaType
    content_ref: ContentReference
    content_digest: Digest
    content_bytes: Annotated[int, Field(ge=0, le=10 * 1024 * 1024)]
    published_at: RecordedDate | None
    effective_at: RecordedDate | None
    origin_id: Identity | None
    lineage_id: Identity | None


class LocatedEvidence(StrictRecord):
    evidence_id: Identity
    snapshot_id: Identity
    start: Count
    end: Count
    quote: Text
    quote_digest: Digest


class ScopedClaim(StrictRecord):
    claim_id: Identity
    text: Text
    kind: Literal["source_statement", "observation", "inference"]
    qualifiers: tuple[Text, ...] = Field(min_length=1, max_length=100)
    temporal_scope: Literal["current", "historical"]


class ContextRelationship(StrictRecord):
    relationship_id: Identity
    kind: Literal["supports", "contradicts", "derived_from"]
    source_id: Identity
    target_id: Identity
    rationale: Text
    rule: Text | None
    assumptions: tuple[Text, ...] = Field(max_length=100)


class ContextQuestion(StrictRecord):
    question_id: Identity
    question: Text
    status: Literal["answered", "unresolved"]
    report_claim_id: Identity


class ContextConflict(StrictRecord):
    conflict_id: Identity
    question_id: Identity
    claim_ids: tuple[Identity, ...] = Field(min_length=1, max_length=1000)
    evidence_ids: tuple[Identity, ...] = Field(min_length=2, max_length=1000)
    reason: Text


class KnowledgeContext(StrictRecord):
    scope_id: Identity
    research_id: Identity
    revision_id: Identity
    parent_revision_id: Identity | None
    parent_digest: Digest | None
    created_at: Timestamp
    objective: Text
    as_of: Timestamp | None
    policy_version: Identity
    snapshots: tuple[ReferencedSnapshot, ...] = Field(max_length=100)
    evidence: tuple[LocatedEvidence, ...] = Field(max_length=1000)
    claims: tuple[ScopedClaim, ...] = Field(max_length=1000)
    relationships: tuple[ContextRelationship, ...] = Field(max_length=2000)
    questions: tuple[ContextQuestion, ...] = Field(min_length=1, max_length=100)
    conflicts: tuple[ContextConflict, ...] = Field(max_length=100)

    @model_validator(mode="after")
    def references_and_chronology(self) -> Self:
        if (self.parent_revision_id is None) != (self.parent_digest is None):
            raise ValueError("parent identity and digest must be paired")
        if self.parent_revision_id == self.revision_id:
            raise ValueError("revision cannot be its own parent")
        created = moment(self.created_at)
        if self.as_of is not None and moment(self.as_of) > created:
            raise ValueError("context predates its as-of input")
        groups = (
            [s.snapshot_id for s in self.snapshots],
            [e.evidence_id for e in self.evidence],
            [c.claim_id for c in self.claims],
            [r.relationship_id for r in self.relationships],
            [q.question_id for q in self.questions],
            [c.conflict_id for c in self.conflicts],
        )
        identities = [identity for group in groups for identity in group]
        if len(set(identities)) != len(identities) or self.revision_id in identities:
            raise ValueError("context entity identities must be unique")
        snapshots = {s.snapshot_id for s in self.snapshots}
        for source in self.snapshots:
            if (
                source.content_ref.scope_id,
                source.content_ref.research_id,
                source.content_ref.snapshot_id,
            ) != (self.scope_id, self.research_id, source.snapshot_id):
                raise ValueError("source reference crosses logical context identity")
            if moment(source.retrieved_at) > created:
                raise ValueError("context predates acquisition")
        if any(e.snapshot_id not in snapshots for e in self.evidence):
            raise ValueError("evidence references an unavailable snapshot")
        self._check_relationships()
        self._check_questions()
        return self

    def _check_relationships(self) -> None:
        claims = {c.claim_id: c for c in self.claims}
        evidence = {e.evidence_id for e in self.evidence}
        dependencies: dict[str, set[str]] = {identity: set() for identity in claims}
        for edge in self.relationships:
            if edge.target_id not in claims:
                raise ValueError("relationship target must be a local claim")
            if edge.kind == "derived_from":
                source = claims.get(edge.source_id)
                if source is None or source.kind != "inference" or edge.rule is None:
                    raise ValueError("derivation requires an inference and rule")
                dependencies[edge.source_id].add(edge.target_id)
            elif edge.source_id not in evidence:
                raise ValueError("support/contradiction requires local evidence")
            elif edge.rule is not None or edge.assumptions:
                raise ValueError("only derivations can carry rules and assumptions")
        _check_acyclic(dependencies)

    def _check_questions(self) -> None:
        claims = {c.claim_id for c in self.claims}
        evidence = {e.evidence_id for e in self.evidence}
        questions = {q.question_id: q for q in self.questions}
        if any(q.report_claim_id not in claims for q in self.questions):
            raise ValueError("question report must reference a local claim")
        for conflict in self.conflicts:
            question = questions.get(conflict.question_id)
            if question is None or question.status != "unresolved":
                raise ValueError("conflict requires an unresolved local question")
            if (
                len(set(conflict.claim_ids)) != len(conflict.claim_ids)
                or len(set(conflict.evidence_ids)) != len(conflict.evidence_ids)
                or not set(conflict.claim_ids) <= claims
                or not set(conflict.evidence_ids) <= evidence
            ):
                raise ValueError("conflict requires distinct local claims/evidence")
        for edge in self.relationships:
            if edge.kind == "contradicts" and not any(
                edge.target_id in c.claim_ids and edge.source_id in c.evidence_ids
                for c in self.conflicts
            ):
                raise ValueError("contradiction requires a matching conflict group")


CONTEXT_SCHEMA = "knowledge-context-prototype/1"


class ContextEnvelope(StrictRecord):
    schema_version: Literal["knowledge-context-prototype/1"]
    context: KnowledgeContext
