"""Offline complete-history bundle checks include nonstructural immutable records."""

import base64
import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from agent.experimental.artifact_bundle import admit_bundle, bundle_member
from agent.experimental.canonical import MAX_BYTES, admit_canonical_json
from agent.experimental.research_bundle import (
    RESEARCH_BUNDLE_SCHEMA,
    admit_research_bundle,
)
from agent.experimental.research_publication import admit_research_publication
from agent.experimental.research_revision import _decode, admit_research_revision
from agent.experimental.source_store import source_descriptor

from tests.storage.publication_fixture import CONTEXT
from tests.storage.research_publication_fixture import research_publication_payload
from tests.unit.test_fixture_revisions import revision
from tests.unit.test_research_publication import pinned  # noqa: F401
from tests.unit.test_research_revision_admission import encoded

NOW = datetime(2026, 9, 6, tzinfo=UTC)


def publication_members(pin, identity):
    result = admit_research_publication(
        research_publication_payload(pin, identity, CONTEXT), pin, identity, CONTEXT
    )
    return {
        "publication.json": bundle_member(result.document.data),
        **{
            f"outputs/{layer}.md": bundle_member(getattr(result, layer))
            for layer in ("summary", "analysis", "dossier")
        },
    }


@pytest.fixture
def complete_bundle(request):
    parent = request.getfixturevalue("pinned")
    structure = parent.revision.research.verifications.structure
    child_id = uuid4()
    child = admit_research_revision(
        encoded(revision(parent.revision.research, str(child_id), (parent.revision,))),
        scope_id=structure.scope_id,
        research_id=structure.research_id,
        revision_id=str(child_id),
        parent_revision_id=structure.revision_id,
        prior=(parent.document.data,),
    )
    publication = uuid4()
    members = publication_members(child, publication)
    for pin in (parent, child):
        value = pin.revision.research.verifications.structure
        members[f"revisions/{value.revision_id}.json"] = bundle_member(
            pin.document.data
        )
        for snapshot in value.snapshots:
            body = snapshot.text.encode()
            members[f"sources/{snapshot.snapshot_id}.body"] = bundle_member(body)
            members[f"sources/{snapshot.snapshot_id}.json"] = bundle_member(
                source_descriptor(body, snapshot.canonical_url).data
            )
    return {
        "schema_version": RESEARCH_BUNDLE_SCHEMA,
        "scope_id": structure.scope_id,
        "root_id": structure.research_id,
        "publication_id": str(publication),
        "revision_ids": [structure.revision_id, str(child_id)],
        "snapshot_ids": sorted(s.snapshot_id for s in structure.snapshots),
        "retained_until": "2026-10-01T00:00:00Z",
        "members": members,
    }


def validate(fields, **overrides):
    document = admit_canonical_json(
        json.dumps(fields).encode(), schema_version=RESEARCH_BUNDLE_SCHEMA
    )
    args = {
        "expected_digest": document.digest,
        "scope": UUID(fields["scope_id"]),
        "root": UUID(fields["root_id"]),
        "publication": UUID(fields["publication_id"]),
        "context": CONTEXT,
        "now": NOW,
    }
    args.update(overrides)
    return admit_research_bundle(document.data, **args)


def test_complete_ancestry_and_exact_member_roundtrip(complete_bundle):
    result = validate(complete_bundle)
    assert list(map(str, result.revision_ids)) == complete_bundle["revision_ids"]
    assert validate(json.loads(result.document.data)) == result
    with pytest.raises(ValueError):
        admit_bundle(
            result.document.data,
            expected_digest=result.document.digest,
            scope=result.scope_id,
            root=result.root_id,
            publication=result.publication_id,
            context=CONTEXT,
            now=NOW,
        )


@pytest.mark.parametrize(
    "change",
    [
        "missing_parent",
        "order",
        "source",
        "output",
        "path",
        "encoding",
        "digest",
        "expiry",
        "scope",
        "extra",
        "unknown_version",
    ],
)
def test_invalid_bundle_denied(complete_bundle, change):
    data = complete_bundle
    if change == "missing_parent":
        parent = data["revision_ids"].pop(0)
        del data["members"][f"revisions/{parent}.json"]
    elif change == "order":
        data["revision_ids"].reverse()
    elif change == "source":
        data["members"][f"sources/{data['snapshot_ids'][0]}.body"] = bundle_member(
            b"Changed source"
        )
    elif change == "output":
        data["members"]["outputs/summary.md"] = bundle_member(b"Changed output")
    elif change == "path":
        data["members"]["../secret"] = bundle_member(b"unsafe")
    elif change == "encoding":
        data["members"]["publication.json"]["data"] = "!invalid!"
    elif change == "digest":
        data["members"]["publication.json"]["sha256"] = "0" * 64
    elif change == "expiry":
        data["retained_until"] = "2026-01-01T00:00:00Z"
    elif change == "scope":
        data["scope_id"] = str(uuid4())
    elif change == "extra":
        data["extra"] = "unknown"
    else:
        data["schema_version"] = "retained-research-bundle-prototype/99"
    with pytest.raises(ValueError):
        validate(data)


def test_changed_historical_question_rejected_with_rehashed_publication(
    complete_bundle,
):
    identity = complete_bundle["revision_ids"][-1]
    path = f"revisions/{identity}.json"
    data = json.loads(base64.b64decode(complete_bundle["members"][path]["data"]))
    data["revision"]["research"]["questions"][0]["question"] = (
        "Changed meaning under existing question ID"
    )
    changed = _decode(json.dumps(data).encode())
    complete_bundle["members"][path] = bundle_member(changed.document.data)
    complete_bundle["members"].update(
        publication_members(changed, UUID(complete_bundle["publication_id"]))
    )
    with pytest.raises(ValueError):
        validate(complete_bundle)


def test_wrong_external_digest_and_oversize_are_denied(complete_bundle):
    with pytest.raises(ValueError):
        validate(complete_bundle, expected_digest="0" * 64)
    complete_bundle["members"]["outputs/summary.md"] = bundle_member(b"x" * MAX_BYTES)
    with pytest.raises(ValueError):
        validate(complete_bundle)
