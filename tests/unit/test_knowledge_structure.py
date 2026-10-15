"""Adversarial structural fixtures; these tests do not establish semantic support."""

from copy import deepcopy

import pytest
from agent.experimental.knowledge import text_digest, validate_structure
from pydantic import ValidationError


@pytest.fixture
def payload():
    text = "📚 Acme\nPrice: $20 per month.\n"
    quote = "Price: $20 per month."
    start = text.index(quote)
    return {
        "schema_version": "knowledge-structure-prototype/1",
        "scope_id": "owner",
        "research_id": "research",
        "revision_id": "r1",
        "snapshots": [
            {
                "snapshot_id": "s1",
                "canonical_url": "https://example.test/pricing",
                "retrieved_at": "2026-09-05T00:00:00Z",
                "normalization_version": "fixture/1",
                "media_type": "text/markdown",
                "text": text,
                "digest": text_digest(text),
            }
        ],
        "evidence": [
            {
                "evidence_id": "e1",
                "snapshot_id": "s1",
                "start": start,
                "end": start + len(quote),
                "quote": quote,
                "quote_digest": text_digest(quote),
            }
        ],
        "claims": [
            {
                "claim_id": "c1",
                "text": "The captured page lists $20 per month.",
                "kind": "source_statement",
                "qualifiers": ["Captured document only"],
            }
        ],
        "relationships": [
            {
                "relationship_id": "edge1",
                "kind": "supports",
                "source_id": "e1",
                "target_id": "c1",
                "rationale": "Fixture-declared support, not verified",
            }
        ],
    }


def validate(payload):
    return validate_structure(
        payload, scope_id="owner", research_id="research", revision_id="r1"
    )


def test_unicode_round_trip_and_deep_immutability(payload):
    ir = validate(payload)
    assert ir.evidence[0].start == 7  # Unicode code points, not UTF-8 bytes.
    assert ir.claims[0].assessment == "unassessed"
    assert validate(ir.model_dump(mode="json")) == ir
    with pytest.raises(ValidationError):
        ir.snapshots[0].text = "modified"
    with pytest.raises(TypeError):
        ir.claims[0].qualifiers[0] = "changed"


@pytest.mark.parametrize(
    "field,value",
    [
        ("scope_id", "other"),
        ("research_id", "other"),
        ("revision_id", "r2"),
        ("schema_version", "knowledge-ir/1"),
    ],
)
def test_context_and_version_rejected(payload, field, value):
    payload[field] = value
    with pytest.raises(ValueError):
        validate(payload)


@pytest.mark.parametrize(
    "field,value",
    [
        ("text", "changed"),
        ("digest", "0" * 64),
        ("media_type", "application/pdf"),
        ("retrieved_at", "2026-09-05T00:00:00"),
        ("retrieved_at", "2026-09-05T00:00:00+01:00"),
    ],
)
def test_snapshot_corruption_and_unsupported_media(payload, field, value):
    payload["snapshots"][0][field] = value
    with pytest.raises(ValueError):
        validate(payload)


@pytest.mark.parametrize(
    "field,value",
    [
        ("snapshot_id", "outside"),
        ("start", -1),
        ("start", True),
        ("start", "7"),
        ("start", 27),
        ("end", 1000),
        ("quote", "Price: $30 per month."),
        ("quote_digest", "0" * 64),
    ],
)
def test_invalid_evidence(payload, field, value):
    payload["evidence"][0][field] = value
    with pytest.raises(ValueError):
        validate(payload)


def test_identity_collision_across_entity_types(payload):
    payload["claims"][0]["claim_id"] = "e1"
    with pytest.raises(ValueError, match="unique"):
        validate(payload)


@pytest.mark.parametrize(
    "field,value", [("source_id", "c1"), ("target_id", "foreign"), ("rule", "invented")]
)
def test_invalid_relationship_direction_or_metadata(payload, field, value):
    payload["relationships"][0][field] = value
    with pytest.raises(ValueError):
        validate(payload)


def test_derivation_cycle_rejected_and_dag_allowed(payload):
    claim = deepcopy(payload["claims"][0])
    claim.update(claim_id="c2", kind="inference")
    payload["claims"].append(claim)
    edge = {
        "relationship_id": "d1",
        "kind": "derived_from",
        "source_id": "c2",
        "target_id": "c1",
        "rule": "Recorded fixture rule",
        "rationale": "Fixture premise",
    }
    payload["relationships"].append(edge)
    assert len(validate(payload).claims) == 2
    payload["claims"][0]["kind"] = "inference"
    payload["relationships"].append(
        {**edge, "relationship_id": "d2", "source_id": "c1", "target_id": "c2"}
    )
    with pytest.raises(ValueError, match="acyclic"):
        validate(payload)


def test_preconstructed_model_is_revalidated(payload):
    ir = validate(payload)
    forged = ir.model_copy(
        update={"snapshots": (ir.snapshots[0].model_copy(update={"text": "forged"}),)}
    )
    with pytest.raises(ValueError):
        validate(forged)


def test_quote_match_does_not_imply_semantic_support(payload):
    payload["claims"][0]["text"] = "The plan costs $99 every year."
    ir = validate(payload)
    assert ir.claims[0].assessment == "unassessed"
    # Structural acceptance deliberately gives no semantic/publication verdict.
    assert "verified" not in ir.model_dump()


def test_unknown_fields_and_mutable_input_do_not_leak(payload):
    ir = validate(payload)
    payload["claims"][0]["qualifiers"].append("changed after validation")
    assert len(ir.claims[0].qualifiers) == 1
    payload["human_reviewed"] = True
    with pytest.raises(ValueError):
        validate(payload)
