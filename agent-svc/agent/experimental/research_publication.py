"""Bind fixture outputs to a complete revision; no storage or currentness authority."""

import json
from uuid import UUID

from .canonical import MAX_BYTES, admit_canonical_json
from .publication_store import (
    PUBLICATION_SCHEMA,
    PublicationContext,
    RetainedPublication,
    admit_publication,
)
from .research_revision import AdmittedResearchRevision, _decode

RESEARCH_PUBLICATION_SCHEMA = "retained-research-publication-prototype/1"


def admit_research_publication(
    raw: bytes,
    pinned: AdmittedResearchRevision,
    publication_id: UUID,
    context: PublicationContext,
) -> RetainedPublication:
    """Require exact retained research and its complete envelope digest.

    The store must supply an independently validated retained revision, including
    its ancestry/source closure, and recheck lifecycle and currentness at commit.
    Re-decoding here prevents inconsistent hand-constructed admission containers;
    it does not authenticate their origin or establish a valid predecessor chain.
    """
    checked = _decode(pinned.document.data)
    if checked != pinned:
        raise ValueError("inconsistent pinned complete revision")
    document = admit_canonical_json(raw, schema_version=RESEARCH_PUBLICATION_SCHEMA)
    fields = json.loads(document.data)
    expected = json.loads(checked.document.data)["revision"]["research"]
    if (
        set(fields)
        != {
            "schema_version",
            "revision_id",
            "revision_digest",
            "research",
            "publication",
        }
        or fields["revision_digest"] != checked.document.digest
        or fields["research"] != expected
    ):
        raise ValueError("publication differs from complete pinned research")
    # Reuse existing artifact/audit/context checks without changing legacy bytes
    # or their schema. Only the new outer representation is retained below.
    legacy = {key: value for key, value in fields.items() if key != "revision_digest"}
    legacy["schema_version"] = PUBLICATION_SCHEMA
    outputs = admit_publication(
        json.dumps(legacy, ensure_ascii=False, separators=(",", ":")).encode(),
        checked.revision.research.verifications.structure,
        publication_id,
        context,
    )
    result = RetainedPublication(
        document, outputs.summary, outputs.analysis, outputs.dossier
    )
    if result.size > MAX_BYTES:
        raise ValueError("publication total byte limit exceeded")
    return result
