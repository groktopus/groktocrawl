"""Bounded supplied-history validation; not storage or authenticated history."""

from datetime import datetime
from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from .knowledge import Identity, Record
from .publication import (
    FixturePublication,
    FixtureResearch,
    validate_fixture_publication,
)
from .verification import FixtureVerifier

EntityKind = Literal[
    "snapshot",
    "evidence",
    "claim",
    "relationship",
    "verification",
    "assessment",
    "question",
    "conflict",
]


class Introduction(Record):
    """Declare a new identity as novel or a replacement; no semantic matching."""

    kind: EntityKind
    entity_id: Identity
    predecessor_id: Identity | None = None


class FixtureRevision(Record):
    schema_version: Literal["fixture-revision/1"]
    parent_revision_id: Identity | None
    created_at: datetime
    research: FixtureResearch
    introductions: tuple[Introduction, ...] = Field(max_length=10_000)

    @field_validator("created_at")
    @classmethod
    def utc_timestamp(cls, value: datetime) -> datetime:
        offset = value.utcoffset()
        if offset is None or offset.total_seconds() != 0:
            raise ValueError("revision time must have an explicit UTC offset")
        return value

    @model_validator(mode="after")
    def recorded_chronology(self) -> Self:
        if self.research.objective is None:
            raise ValueError("revision requires an explicit objective")
        structure = self.research.verifications.structure
        times = [s.retrieved_at for s in structure.snapshots]
        times.extend(r.checked_at for r in self.research.verifications.records)
        times.extend(a.checked_at for a in self.research.verifications.assessments)
        if structure.as_of is not None:
            times.append(structure.as_of)
        if any(time > self.created_at for time in times):
            raise ValueError("revision predates its recorded inputs")
        return self


def _entities(revision: FixtureRevision) -> dict[str, tuple[EntityKind, Record]]:
    research = revision.research
    structure = research.verifications.structure
    groups: tuple[tuple[EntityKind, str, tuple[Record, ...]], ...] = (
        ("snapshot", "snapshot_id", structure.snapshots),
        ("evidence", "evidence_id", structure.evidence),
        ("claim", "claim_id", structure.claims),
        ("relationship", "relationship_id", structure.relationships),
        ("verification", "verification_id", research.verifications.records),
        ("assessment", "assessment_id", research.verifications.assessments),
        ("question", "question_id", research.questions),
        ("conflict", "conflict_id", research.conflicts),
    )
    result: dict[str, tuple[EntityKind, Record]] = {}
    for kind, field, records in groups:
        for record in records:
            identity = getattr(record, field)
            if identity in result:
                raise ValueError("revision entity IDs must not alias across kinds")
            result[identity] = (kind, record)
    return result


class FixtureHistory(Record):
    schema_version: Literal["fixture-history/1"]
    revisions: tuple[FixtureRevision, ...] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def immutable_chain(self) -> Self:
        root = self.revisions[0].research.verifications.structure
        known: dict[str, tuple[EntityKind, Record]] = {}
        revision_ids: set[str] = set()
        previous: FixtureRevision | None = None
        for revision in self.revisions:
            structure = revision.research.verifications.structure
            if (structure.scope_id, structure.research_id) != (
                root.scope_id,
                root.research_id,
            ):
                raise ValueError("history crosses scope or research identity")
            if structure.revision_id in revision_ids:
                raise ValueError("revision IDs must be unique")
            expected_parent = (
                previous.research.verifications.structure.revision_id
                if previous
                else None
            )
            if revision.parent_revision_id != expected_parent:
                raise ValueError(
                    "history must be a root followed by its direct children"
                )
            if previous and revision.created_at < previous.created_at:
                raise ValueError("revision chronology moves backwards")
            current = _entities(revision)
            additions = {identity for identity in current if identity not in known}
            declarations = {item.entity_id: item for item in revision.introductions}
            if (
                len(declarations) != len(revision.introductions)
                or set(declarations) != additions
            ):
                raise ValueError("declare each newly introduced identity exactly once")
            for identity, entity in current.items():
                if identity in known and known[identity] != entity:
                    raise ValueError("previously used entity identity was reassigned")
            for identity, declaration in declarations.items():
                if declaration.kind != current[identity][0]:
                    raise ValueError("introduction kind differs from current entity")
                if declaration.predecessor_id is not None:
                    predecessor = known.get(declaration.predecessor_id)
                    if predecessor is None or predecessor[0] != declaration.kind:
                        raise ValueError(
                            "predecessor must be an earlier entity of the same kind"
                        )
            known.update(current)
            revision_ids.add(structure.revision_id)
            previous = revision
        return self


def validate_history(
    payload: object, *, scope_id: str, research_id: str
) -> FixtureHistory:
    result = FixtureHistory.model_validate(payload)
    root = result.revisions[0].research.verifications.structure
    if (root.scope_id, root.research_id) != (scope_id, research_id):
        raise ValueError("history differs from caller's expected scope/research")
    return result


def append_revision(
    history: FixtureHistory, revision: FixtureRevision
) -> FixtureHistory:
    """Preserve the supplied prefix. Callers must establish its trusted provenance."""
    checked = FixtureHistory.model_validate(history)
    return FixtureHistory(
        schema_version="fixture-history/1", revisions=(*checked.revisions, revision)
    )


def validate_latest_publication(
    history: FixtureHistory,
    payload: object,
    *,
    artifact_set_id: str,
    renderer_version: str,
    auditor: FixtureVerifier,
) -> FixturePublication:
    """Historical passes never satisfy the latest revision's publication gate."""
    checked = FixtureHistory.model_validate(history)
    return validate_fixture_publication(
        payload,
        research=checked.revisions[-1].research,
        artifact_set_id=artifact_set_id,
        renderer_version=renderer_version,
        auditor=auditor,
    )
