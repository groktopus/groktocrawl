"""Canonical complete fixture revisions; no database or semantic trust authority."""

import json
from dataclasses import dataclass

from .canonical import CanonicalDocument, admit_canonical_json
from .revisions import FixtureRevision, validate_history

RESEARCH_REVISION_SCHEMA = "retained-research-revision-prototype/1"
MAX_RESEARCH_REVISIONS = 20


@dataclass(frozen=True)
class AdmittedResearchRevision:
    document: CanonicalDocument
    revision: FixtureRevision


def _decode(raw: bytes) -> AdmittedResearchRevision:
    document = admit_canonical_json(raw, schema_version=RESEARCH_REVISION_SCHEMA)
    fields = json.loads(document.data)
    if set(fields) != {"schema_version", "revision"}:
        raise ValueError("unexpected complete revision fields")
    revision = FixtureRevision.model_validate(fields["revision"])
    return AdmittedResearchRevision(document, revision)


def admit_research_revision(
    raw: bytes,
    *,
    scope_id: str,
    research_id: str,
    revision_id: str,
    parent_revision_id: str | None,
    prior: tuple[bytes, ...] = (),
) -> AdmittedResearchRevision:
    """Validate a complete bounded prefix plus candidate against caller identities.

    The caller must obtain the prefix from its trusted retained authority. This
    pure function proves supplied-chain consistency, not provenance or currentness.
    Each raw envelope has the canonical 1 MiB bound; at most twenty are admitted.
    """
    if not isinstance(prior, tuple) or len(prior) >= MAX_RESEARCH_REVISIONS:
        raise ValueError("complete history requires at most nineteen prior revisions")
    candidate = _decode(raw)
    structure = candidate.revision.research.verifications.structure
    if (
        structure.scope_id != scope_id
        or structure.research_id != research_id
        or structure.revision_id != revision_id
        or candidate.revision.parent_revision_id != parent_revision_id
    ):
        raise ValueError("complete revision differs from expected identity or parent")
    previous = tuple(_decode(value).revision for value in prior)
    validate_history(
        {
            "schema_version": "fixture-history/1",
            "revisions": (*previous, candidate.revision),
        },
        scope_id=scope_id,
        research_id=research_id,
    )
    return candidate
