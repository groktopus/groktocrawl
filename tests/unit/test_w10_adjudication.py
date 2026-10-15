import hashlib
import json

import pytest

from scripts.record_w10_adjudication import validate_and_build


def sha(value):
    raw = value if isinstance(value, bytes) else value.encode()
    return hashlib.sha256(raw).hexdigest()


def fixtures():
    items = [
        {
            "observation_id": "source-1",
            "case_id": "case-1",
            "candidate_id": "candidate-1",
            "item_type": "source_grade",
            "selection_reasons": ["seeded_10_percent_unique_source_sample"],
        },
        {
            "observation_id": "claim-1",
            "case_id": "case-1",
            "gap_id": "gap-1",
            "item_type": "claim_closure",
            "selection_reasons": ["high_importance_closure_disagreement"],
        },
    ]
    packet = {
        "schema_version": "enterprise-evaluation/w10-adjudication-private/1",
        "blind_to_policy_and_repetition": True,
        "items": items,
    }
    packet_bytes = json.dumps(packet).encode()
    manifest = {
        "schema_version": "enterprise-evaluation/w10-adjudication-manifest/1",
        "private_packet_sha256": sha(packet_bytes),
        "items": [
            {
                "observation_id": item["observation_id"],
                "case_id": item["case_id"],
                "item_type": item["item_type"],
                "selection_reasons": item["selection_reasons"],
                "private_item_sha256": sha(
                    json.dumps(item, ensure_ascii=False, sort_keys=True)
                ),
            }
            for item in items
        ],
    }
    responses = {
        "schema_version": "enterprise-evaluation/w10-adjudication-responses/1",
        "reviewer_kind": "agent",
        "reviewer_model": "general",
        "reviewer_transport": "openai_chat_completions",
        "blind_to_policy_and_repetition": True,
        "items": [
            {
                "observation_id": "source-1",
                "item_type": "source_grade",
                "currency": 2,
                "relevance": 2,
                "authority": 2,
                "accuracy": 2,
                "purpose": 2,
                "passage_support": "supports",
                "useful": True,
                "derivative_or_copied": False,
                "rationale": "The excerpt directly supports the declared claim.",
            },
            {
                "observation_id": "claim-1",
                "item_type": "claim_closure",
                "claim_status": "closed",
                "contradiction_handling": "not_applicable",
                "rationale": "The admitted source closes the claim.",
            },
        ],
    }
    return packet, manifest, responses, packet_bytes


def test_valid_adjudication_emits_only_public_verdicts_and_hashes():
    packet, manifest, responses, packet_bytes = fixtures()
    result = validate_and_build(packet, manifest, responses, packet_bytes=packet_bytes)
    assert result["counts"] == {
        "selected_observations": 2,
        "source_grades": 1,
        "claim_closures": 1,
    }
    encoded = json.dumps(result)
    assert "directly supports" not in encoded
    source = next(
        item for item in result["items"] if item["observation_id"] == "source-1"
    )
    assert source["verdict"]["passage_support"] == "supports"


@pytest.mark.parametrize("change", ["missing", "extra", "duplicate"])
def test_observation_set_must_be_complete_and_exact(change):
    packet, manifest, responses, packet_bytes = fixtures()
    if change == "missing":
        responses["items"].pop()
    elif change == "extra":
        responses["items"].append(
            {"observation_id": "foreign", "item_type": "source_grade"}
        )
    else:
        responses["items"].append(dict(responses["items"][0]))
    with pytest.raises(ValueError, match="response"):
        validate_and_build(packet, manifest, responses, packet_bytes=packet_bytes)


def test_packet_tampering_fails_before_verdict_publication():
    packet, manifest, responses, packet_bytes = fixtures()
    packet["items"][0]["candidate_id"] = "changed"
    with pytest.raises(ValueError, match="private item digest mismatch"):
        validate_and_build(packet, manifest, responses, packet_bytes=packet_bytes)


def test_agent_and_blinding_disclosures_are_required():
    packet, manifest, responses, packet_bytes = fixtures()
    responses["reviewer_kind"] = "human"
    with pytest.raises(ValueError, match="agent review"):
        validate_and_build(packet, manifest, responses, packet_bytes=packet_bytes)


def test_reviewer_model_disclosure_is_required():
    packet, manifest, responses, packet_bytes = fixtures()
    responses.pop("reviewer_model")
    with pytest.raises(ValueError, match="model route"):
        validate_and_build(packet, manifest, responses, packet_bytes=packet_bytes)


def test_reviewer_transport_disclosure_is_required():
    packet, manifest, responses, packet_bytes = fixtures()
    responses.pop("reviewer_transport")
    with pytest.raises(ValueError, match="request path"):
        validate_and_build(packet, manifest, responses, packet_bytes=packet_bytes)


def test_scores_reject_boolean_and_out_of_range_values():
    packet, manifest, responses, packet_bytes = fixtures()
    responses["items"][0]["authority"] = True
    with pytest.raises(ValueError, match="authority"):
        validate_and_build(packet, manifest, responses, packet_bytes=packet_bytes)
