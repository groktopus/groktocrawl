"""Immutable structural subset of ADR-0069, not a semantic verifier.

The prototype format deliberately has its own version. It is not the complete
Knowledge IR or a publication certificate. Bodies are inline fixture text; durable
references, retention, verification records and render audits are later slices.
"""

import hashlib
from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Identity = Annotated[str, Field(strict=True, min_length=1, max_length=200)]
Text = Annotated[str, Field(strict=True, min_length=1, max_length=100_000)]
Digest = Annotated[str, Field(strict=True, pattern=r"^[0-9a-f]{64}$")]
Offset = Annotated[int, Field(strict=True, ge=0)]


def text_digest(text: str) -> str:
    """Hash exact UTF-8 bytes; never normalize after offsets are assigned."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class Record(BaseModel):
    """Frozen nested records; revalidate even preconstructed model instances."""

    model_config = ConfigDict(
        frozen=True, extra="forbid", revalidate_instances="always"
    )


class SourceDate(Record):
    """A recorded source date and its provenance, not an authenticated assertion."""

    value: datetime
    provenance: Text

    @field_validator("value")
    @classmethod
    def utc_timestamp(cls, value: datetime) -> datetime:
        offset = value.utcoffset()
        if offset is None or offset.total_seconds() != 0:
            raise ValueError("source date must have an explicit UTC offset")
        return value


class Snapshot(Record):
    snapshot_id: Identity
    canonical_url: Annotated[str, Field(pattern=r"^https?://[^\s/]+(?:/[^\s]*)?$")]
    retrieved_at: datetime
    normalization_version: Identity
    media_type: Literal["text/plain", "text/markdown"]
    text: Text
    digest: Digest
    lineage_id: Identity | None = None
    origin_id: Identity | None = None
    published_at: SourceDate | None = None
    effective_at: SourceDate | None = None

    @field_validator("retrieved_at")
    @classmethod
    def utc_timestamp(cls, value: datetime) -> datetime:
        offset = value.utcoffset()
        if offset is None or offset.total_seconds() != 0:
            raise ValueError("retrieved_at must have an explicit UTC offset")
        return value

    @model_validator(mode="after")
    def exact_bytes(self) -> Self:
        if text_digest(self.text) != self.digest:
            raise ValueError("snapshot digest does not match exact UTF-8 bytes")
        return self


class Evidence(Record):
    evidence_id: Identity
    snapshot_id: Identity
    start: Offset
    end: Offset
    quote: Text
    quote_digest: Digest


class Claim(Record):
    claim_id: Identity
    text: Text
    kind: Literal["source_statement", "observation", "inference"]
    qualifiers: tuple[Text, ...] = Field(min_length=1, max_length=100)
    temporal_scope: Literal["current", "historical"] = "current"
    assessment: Literal[
        "unassessed", "supported", "contested", "insufficient", "refuted"
    ] = "unassessed"


class Relationship(Record):
    relationship_id: Identity
    kind: Literal["supports", "contradicts", "derived_from"]
    source_id: Identity
    target_id: Identity
    rationale: Text
    rule: Text | None = None
    assumptions: tuple[Text, ...] = Field(default=(), max_length=100)


class KnowledgeStructure(Record):
    """One self-contained, scoped revision; assessment never implies verification."""

    schema_version: Literal["knowledge-structure-prototype/1"]
    scope_id: Identity
    research_id: Identity
    revision_id: Identity
    as_of: datetime | None = None
    snapshots: tuple[Snapshot, ...] = Field(max_length=100)
    evidence: tuple[Evidence, ...] = Field(max_length=1000)
    claims: tuple[Claim, ...] = Field(max_length=1000)
    relationships: tuple[Relationship, ...] = Field(max_length=2000)

    @field_validator("as_of")
    @classmethod
    def utc_as_of(cls, value: datetime | None) -> datetime | None:
        if value is not None:
            offset = value.utcoffset()
            if offset is None or offset.total_seconds() != 0:
                raise ValueError("as_of must have an explicit UTC offset")
        return value

    @model_validator(mode="after")
    def structural_integrity(self) -> Self:
        groups = (
            tuple(item.snapshot_id for item in self.snapshots),
            tuple(item.evidence_id for item in self.evidence),
            tuple(item.claim_id for item in self.claims),
            tuple(item.relationship_id for item in self.relationships),
        )
        identities = tuple(identity for group in groups for identity in group)
        if len(set(identities)) != len(identities):
            raise ValueError("entity identities must be unique within this revision")
        snapshots = {item.snapshot_id: item for item in self.snapshots}
        evidence_ids = {item.evidence_id for item in self.evidence}
        claims = {item.claim_id: item for item in self.claims}
        for item in self.evidence:
            snapshot = snapshots.get(item.snapshot_id)
            if snapshot is None:
                raise ValueError("evidence references an unavailable snapshot")
            if not item.start < item.end <= len(snapshot.text):
                raise ValueError("evidence offsets must be a nonempty in-bounds span")
            if snapshot.text[item.start : item.end] != item.quote:
                raise ValueError("quote differs from the exact code-point span")
            if text_digest(item.quote) != item.quote_digest:
                raise ValueError("quote digest differs from exact UTF-8 bytes")
        dependencies: dict[str, set[str]] = {identity: set() for identity in claims}
        for edge in self.relationships:
            if edge.target_id not in claims:
                raise ValueError("relationship target must be a claim in this revision")
            if edge.kind == "derived_from":
                if edge.source_id not in claims:
                    raise ValueError(
                        "derivation source must be a claim in this revision"
                    )
                if claims[edge.source_id].kind != "inference" or edge.rule is None:
                    raise ValueError(
                        "derivation requires an inference and an explicit rule"
                    )
                dependencies[edge.source_id].add(edge.target_id)
            elif edge.source_id not in evidence_ids:
                raise ValueError("support/contradiction source must be local evidence")
            elif edge.rule is not None or edge.assumptions:
                raise ValueError("derivation metadata is only valid on derived_from")
        _check_acyclic(dependencies)
        return self


def _check_acyclic(dependencies: dict[str, set[str]]) -> None:
    """Iterative removal avoids recursion limits on adversarial derivation chains."""
    remaining = {key: set(values) for key, values in dependencies.items()}
    while remaining:
        leaves = {key for key, values in remaining.items() if not values}
        if not leaves:
            raise ValueError("claim derivations must be acyclic")
        remaining = {
            key: values - leaves
            for key, values in remaining.items()
            if key not in leaves
        }


def validate_structure(
    payload: object, *, scope_id: str, research_id: str, revision_id: str
) -> KnowledgeStructure:
    """Validate untrusted input against caller-established scope and revision.

    The caller supplies trusted context; this helper is not authentication. Do not
    use BaseModel.model_construct/model_copy as an ingestion or validation boundary.
    """
    result = KnowledgeStructure.model_validate(payload)
    if (result.scope_id, result.research_id, result.revision_id) != (
        scope_id,
        research_id,
        revision_id,
    ):
        raise ValueError(
            "structure does not match the expected scope/research/revision"
        )
    return result
