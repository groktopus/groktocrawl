"""Complete revision canonical bytes and history admission, independent of storage."""

import json
from copy import deepcopy

import pytest
from agent.experimental.canonical import MAX_BYTES
from agent.experimental.research_revision import (
    RESEARCH_REVISION_SCHEMA,
    admit_research_revision,
)
from agent.experimental.revisions import FixtureRevision

from tests.unit.test_fixture_publication import journey
from tests.unit.test_fixture_revisions import revision
from tests.unit.test_knowledge_structure import payload  # noqa: F401


@pytest.fixture
def root(request):
    return revision(journey(request.getfixturevalue("payload"), "supported")[0])


def encoded(value):
    return json.dumps(
        {
            "schema_version": RESEARCH_REVISION_SCHEMA,
            "revision": value.model_dump(mode="json"),
        },
        ensure_ascii=False,
    ).encode()


def admit(raw, **kwargs):
    context = {
        "scope_id": "owner",
        "research_id": "research",
        "revision_id": "r1",
        "parent_revision_id": None,
    }
    context.update(kwargs)
    return admit_research_revision(raw, **context)


def test_complete_roundtrip_preserves_inner_prototype_hashes(root):
    raw = encoded(root)
    before = [r.checked_input_digest for r in root.research.verifications.records]
    result = admit(raw)
    assert result.revision == root
    assert admit(result.document.data) == result
    assert [
        r.checked_input_digest for r in result.revision.research.verifications.records
    ] == before
    assert encoded(root) == raw


def test_successor_uses_complete_prefix_without_mutating_it(root):
    first = encoded(root)
    second = revision(root.research, "r2", (root,))
    result = admit(
        encoded(second), revision_id="r2", parent_revision_id="r1", prior=(first,)
    )
    assert result.revision == second
    assert first == encoded(root)


@pytest.mark.parametrize(
    "field,value",
    [
        ("scope_id", "other"),
        ("research_id", "other"),
        ("revision_id", "other"),
        ("parent_revision_id", "other"),
    ],
)
def test_independent_identity_mismatch_rejected(root, field, value):
    with pytest.raises(ValueError):
        admit(encoded(root), **{field: value})


@pytest.mark.parametrize(
    "change", ["objective", "created_at", "introductions", "unknown", "schema"]
)
def test_missing_or_unknown_context_rejected(root, change):
    raw = json.loads(encoded(root))
    if change == "objective":
        raw["revision"]["research"].pop("objective")
    elif change in {"created_at", "introductions"}:
        raw["revision"].pop(change)
    elif change == "unknown":
        raw["extra"] = True
    else:
        raw["schema_version"] = "knowledge-ir/1"
    with pytest.raises(ValueError):
        admit(json.dumps(raw).encode())


def test_missing_parent_history_rejected(root):
    second = revision(root.research, "r2", (root,))
    with pytest.raises(ValueError):
        admit(encoded(second), revision_id="r2", parent_revision_id="r1")


def test_changed_verification_identity_rejected_across_complete_history(root):
    second = revision(root.research, "r2", (root,))
    raw = second.model_dump(mode="json")
    record = raw["research"]["verifications"]["records"][0]
    added = record["verification_id"]
    record["verification_id"] = root.research.verifications.records[0].verification_id
    raw["introductions"] = [i for i in raw["introductions"] if i["entity_id"] != added]
    candidate = FixtureRevision.model_validate(raw)
    with pytest.raises(ValueError, match="reassigned"):
        admit(
            encoded(candidate),
            revision_id="r2",
            parent_revision_id="r1",
            prior=(encoded(root),),
        )


def test_tampered_prefix_is_revalidated(root):
    second = revision(root.research, "r2", (root,))
    bad = deepcopy(json.loads(encoded(root)))
    bad["revision"]["research"]["verifications"]["records"][0][
        "checked_input_digest"
    ] = "0" * 64
    with pytest.raises(ValueError):
        admit(
            encoded(second),
            revision_id="r2",
            parent_revision_id="r1",
            prior=(json.dumps(bad).encode(),),
        )


def test_duplicate_json_and_oversize_rejected(root):
    raw = encoded(root)
    with pytest.raises(ValueError):
        admit(b'{"schema_version":"wrong",' + raw[1:])
    with pytest.raises(ValueError):
        admit(b" " * MAX_BYTES + raw)


def test_history_bound_rejects_before_decoding():
    with pytest.raises(ValueError, match="nineteen"):
        admit(b"not JSON", prior=(b"not JSON",) * 20)


def test_twenty_revision_complete_chain_is_admitted(root):
    chain = [root]
    for index in range(2, 21):
        chain.append(revision(root.research, f"r{index}", tuple(chain)))
    result = admit(
        encoded(chain[-1]),
        revision_id="r20",
        parent_revision_id="r19",
        prior=tuple(encoded(value) for value in chain[:-1]),
    )
    assert result.revision == chain[-1]
