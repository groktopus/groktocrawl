"""Offline bundle integrity and scope checks against a synthetic pinned publication."""

import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from agent.experimental.artifact_bundle import (
    BUNDLE_SCHEMA,
    admit_bundle,
    bundle_member,
)
from agent.experimental.canonical import admit_canonical_json
from agent.experimental.publication_store import admit_publication
from agent.experimental.revision_store import admit_revision
from agent.experimental.source_store import source_descriptor

from tests.storage.publication_fixture import (
    CONTEXT,
    publication_payload,
    supported_revision,
)
from tests.storage.test_revision_store_db import payload

NOW = datetime(2026, 9, 5, tzinfo=UTC)


@pytest.fixture
def bundle():
    scope, root, revision, snapshot, publication = (uuid4() for _ in range(5))
    body = b"Exact CR\r\nNUL\x00 retained fixture bytes."
    structure = admit_revision(
        supported_revision(payload(scope, root, revision, snapshot, body)),
        scope,
        root,
        revision,
    )
    published = admit_publication(
        publication_payload(structure.structure, publication),
        structure.structure,
        publication,
        CONTEXT,
    )
    members = {
        f"revisions/{revision}.json": bundle_member(structure.document.data),
        f"sources/{snapshot}.body": bundle_member(body),
        f"sources/{snapshot}.json": bundle_member(
            source_descriptor(body, "https://example.test/revision").data
        ),
        "publication.json": bundle_member(published.document.data),
    }
    members.update(
        {
            f"outputs/{layer}.md": bundle_member(getattr(published, layer))
            for layer in ("summary", "analysis", "dossier")
        }
    )
    raw = {
        "schema_version": BUNDLE_SCHEMA,
        "scope_id": str(scope),
        "root_id": str(root),
        "publication_id": str(publication),
        "revision_ids": [str(revision)],
        "snapshot_ids": [str(snapshot)],
        "retained_until": "2026-10-01T00:00:00Z",
        "members": members,
    }
    return raw, scope, root, publication


def validate(raw, scope, root, publication, **kwargs):
    document = admit_canonical_json(
        json.dumps(raw).encode(), schema_version=BUNDLE_SCHEMA
    )
    args = {
        "expected_digest": document.digest,
        "scope": scope,
        "root": root,
        "publication": publication,
        "context": CONTEXT,
        "now": NOW,
    }
    args.update(kwargs)
    return admit_bundle(document.data, **args)


def test_exact_bundle_without_external_io(bundle):
    raw, scope, root, publication = bundle
    result = validate(raw, scope, root, publication)
    assert result.scope_id == scope
    assert result.root_id == root
    assert len(result.revision_ids) == len(result.snapshot_ids) == 1


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "extra",
        "traversal",
        "digest",
        "encoding",
        "body",
        "output",
        "duplicate",
        "ancestry",
        "expiry",
        "naive",
        "schema",
        "identity",
    ],
)
def test_reject_invalid_bundles(bundle, mutation):
    import base64

    raw, scope, root, publication = bundle
    members = raw["members"]
    if mutation == "missing":
        members.pop("outputs/summary.md")
    elif mutation in ("extra", "traversal"):
        members["../secret" if mutation == "traversal" else "unexpected"] = (
            bundle_member(b"no")
        )
    elif mutation == "digest":
        members["outputs/summary.md"]["sha256"] = "0" * 64
    elif mutation == "encoding":
        members["outputs/summary.md"]["data"] = "%%%"
    elif mutation == "body":
        name = next(name for name in members if name.endswith(".body"))
        members[name] = bundle_member(
            b"changed body with correctly updated member hash"
        )
    elif mutation == "output":
        members["outputs/summary.md"] = bundle_member(b"invented output")
    elif mutation == "duplicate":
        raw["revision_ids"] *= 2
    elif mutation == "ancestry":
        name = next(name for name in members if name.startswith("revisions/"))
        data = json.loads(base64.b64decode(members[name]["data"]))
        data["parent_revision_id"] = str(uuid4())
        revised = admit_canonical_json(
            json.dumps(data).encode(), schema_version=data["schema_version"]
        )
        members[name] = bundle_member(revised.data)
    elif mutation == "expiry":
        raw["retained_until"] = "2026-09-01T00:00:00Z"
    elif mutation == "naive":
        raw["retained_until"] = "2026-10-01T00:00:00"
    elif mutation == "schema":
        raw["schema_version"] = "future/999"
    else:
        raw["scope_id"] = str(uuid4())
    with pytest.raises(ValueError):
        validate(raw, scope, root, publication)


@pytest.mark.parametrize(
    "parameter", ["scope", "root", "publication", "expected_digest", "context", "now"]
)
def test_independent_expected_context_required(bundle, parameter):
    raw, scope, root, publication = bundle
    value = uuid4()
    if parameter == "expected_digest":
        value = "0" * 64
    elif parameter == "context":
        value = CONTEXT.model_copy(update={"policy_version": "unknown"})
    elif parameter == "now":
        value = NOW.replace(tzinfo=None)
    # Call admission directly to allow overriding named origin parameters.
    doc = admit_canonical_json(json.dumps(raw).encode(), schema_version=BUNDLE_SCHEMA)
    args = {
        "expected_digest": doc.digest,
        "scope": scope,
        "root": root,
        "publication": publication,
        "context": CONTEXT,
        "now": NOW,
    }
    args[parameter] = value
    with pytest.raises(ValueError):
        admit_bundle(doc.data, **args)
