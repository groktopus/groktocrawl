"""Frozen interchange examples exercise actual readers, not only JSON Schema."""

import hashlib
import json
from pathlib import Path
from uuid import UUID

import pytest
from agent.experimental.publication import FixturePublication, FixtureResearch
from agent.experimental.publication_store import PublicationContext
from agent.experimental.research_publication import admit_research_publication
from agent.experimental.research_revision import admit_research_revision
from agent.experimental.revision_store import admit_revision
from agent.experimental.revisions import FixtureRevision

ROOT = Path(__file__).parents[1] / "contracts" / "research"
INVENTORY = json.loads((ROOT / "inventory.json").read_bytes())
SCOPE, RESEARCH, REVISION, PUBLICATION = (str(UUID(int=i)) for i in (1, 2, 3, 5))


def read(name):
    return (ROOT / name).read_bytes()


def admit(raw=None, *, successor=False):
    return admit_research_revision(
        read("successor.json" if successor else "revision.json")
        if raw is None
        else raw,
        scope_id=SCOPE,
        research_id=RESEARCH,
        revision_id=str(UUID(int=6)) if successor else REVISION,
        parent_revision_id=REVISION if successor else None,
        prior=(read("revision.json"),) if successor else (),
    )


def publish(raw=None):
    return admit_research_publication(
        read("publication.json") if raw is None else raw,
        admit(),
        UUID(PUBLICATION),
        PublicationContext.model_validate_json(read("context.json")),
    )


def test_frozen_bytes_and_independent_digest_pins():
    for name, digest in INVENTORY["pins"].items():
        assert hashlib.sha256(read(name)).hexdigest() == digest
    root, successor, publication = admit(), admit(successor=True), publish()
    assert root.document.digest == INVENTORY["expected"]["revision_digest"]
    assert publication.document.digest == INVENTORY["expected"]["publication_digest"]
    assert admit(root.document.data) == root
    assert admit(successor.document.data, successor=True) == successor
    assert publish(publication.document.data) == publication
    legacy = admit_revision(
        read("legacy-structure.json"), UUID(SCOPE), UUID(RESEARCH), UUID(REVISION)
    )
    assert legacy.document.digest == INVENTORY["expected"]["legacy_digest"]
    assert (
        admit_revision(
            legacy.document.data, UUID(SCOPE), UUID(RESEARCH), UUID(REVISION)
        )
        == legacy
    )


@pytest.mark.parametrize(
    "name,model",
    [
        ("revision-model.schema.json", FixtureRevision),
        ("research-model.schema.json", FixtureResearch),
        ("publication-model.schema.json", FixturePublication),
    ],
)
def test_nested_schema_inventory_matches_current_models(name, model):
    assert json.loads(read(name)) == model.model_json_schema()


@pytest.mark.parametrize("name", ["revision.json", "publication.json"])
@pytest.mark.parametrize("change", ["version", "outer_field", "nested_field"])
def test_readers_reject_unknown_versions_and_fields(name, change):
    data = json.loads(read(name))
    if change == "version":
        data["schema_version"] = "knowledge-ir/1"
    elif change == "outer_field":
        data["human_reviewed"] = True
    else:
        nested = data["revision"] if name == "revision.json" else data["research"]
        nested["human_reviewed"] = True
    with pytest.raises(ValueError):
        (admit if name == "revision.json" else publish)(json.dumps(data).encode())


@pytest.mark.parametrize("change", ["objective", "introductions", "snapshot"])
def test_context_and_reference_requirements_exceed_schema_shape(change):
    data = json.loads(read("revision.json"))
    revision = data["revision"]
    if change == "objective":
        # The nested research model permits this; complete revision admission does not.
        revision["research"].pop("objective")
        assert FixtureResearch.model_validate(revision["research"]).objective is None
    elif change == "introductions":
        revision["introductions"] = []
    else:
        revision["research"]["verifications"]["structure"]["evidence"][0][
            "snapshot_id"
        ] = "missing-snapshot"
    with pytest.raises(ValueError):
        admit(json.dumps(data).encode())


def test_default_projection_does_not_rewrite_authoritative_bytes():
    data = json.loads(read("revision.json"))
    snapshot = data["revision"]["research"]["verifications"]["structure"]["snapshots"][
        0
    ]
    assert snapshot.pop("published_at") is None
    admitted = admit(json.dumps(data).encode())
    stored = json.loads(admitted.document.data)
    assert (
        "published_at"
        not in stored["revision"]["research"]["verifications"]["structure"][
            "snapshots"
        ][0]
    )
    assert (
        admitted.revision.research.verifications.structure.snapshots[0].published_at
        is None
    )
    assert admitted.document.digest != INVENTORY["expected"]["revision_digest"]
    assert admit(admitted.document.data) == admitted
