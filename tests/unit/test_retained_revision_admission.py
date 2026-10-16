"""Representation/context validation before a revision database transaction."""

import json
from uuid import uuid4

import pytest
from agent.experimental.revision_store import admit_revision


def envelope(scope, root, revision):
    return {
        "schema_version": "retained-structure-prototype/1",
        "parent_revision_id": None,
        "structure": {
            "schema_version": "knowledge-structure-prototype/1",
            "scope_id": str(scope),
            "research_id": str(root),
            "revision_id": str(revision),
            "snapshots": [],
            "evidence": [],
            "claims": [],
            "relationships": [],
        },
    }


def test_canonical_envelope_binds_expected_context():
    scope, root, revision = uuid4(), uuid4(), uuid4()
    raw = json.dumps(envelope(scope, root, revision)).encode()
    result = admit_revision(raw, scope, root, revision)
    assert admit_revision(result.document.data, scope, root, revision) == result
    assert result.parent_id is None
    for expected in (
        (uuid4(), root, revision),
        (scope, uuid4(), revision),
        (scope, root, uuid4()),
    ):
        with pytest.raises(ValueError):
            admit_revision(raw, *expected)


@pytest.mark.parametrize("parent", [17, True, [], {}, "bad-id"])
def test_rejects_invalid_parent(parent):
    scope, root, revision = uuid4(), uuid4(), uuid4()
    value = envelope(scope, root, revision)
    value["parent_revision_id"] = parent
    with pytest.raises(ValueError):
        admit_revision(json.dumps(value).encode(), scope, root, revision)


def test_rejects_undeclared_envelope_fields():
    scope, root, revision = uuid4(), uuid4(), uuid4()
    value = envelope(scope, root, revision)
    value["human_approved"] = True
    with pytest.raises(ValueError):
        admit_revision(json.dumps(value).encode(), scope, root, revision)
