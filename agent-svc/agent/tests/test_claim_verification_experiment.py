from copy import deepcopy

import pytest
from agent.experimental.claim_verification_experiment import (
    ClaimVerificationPacket,
    IndependentClaimVerification,
    IndependentVerifier,
    load_claim_verification_corpus,
    validate_independent_verification,
)


@pytest.fixture
def packet():
    return ClaimVerificationPacket(
        schema_version="claim-verification-input/1",
        case_id="case-1",
        claim_id="claim-1",
        claim="The service retains build logs for 30 days.",
        risk="low",
        evidence_obligation="A current first-party retention statement.",
        evidence=[
            {
                "span_id": "span-1",
                "source_id": "source-1",
                "source_kind": "primary",
                "temporal_status": "current",
                "text": "Build logs are retained for 30 days.",
            }
        ],
    )


@pytest.fixture
def verifier():
    return IndependentVerifier(route="hermes", model="luna", prompt_version="w12.3/1")


def result(packet, verifier, **changes):
    data = {
        "schema_version": "independent-claim-verification/1",
        "verification_id": "verification-1",
        "verifier": verifier,
        "checked_input": packet,
        "checked_input_digest": packet.input_digest(),
        "verdict": "supported",
        "evidence_span_ids": ["span-1"],
        "contradiction_span_ids": [],
        "confidence": 90,
        "publish_recommendation": True,
        "reason": "The current primary span directly entails the claim.",
    }
    data.update(changes)
    return data


def test_exact_supported_verification_round_trips(packet, verifier):
    checked = validate_independent_verification(
        result(packet, verifier), packet=packet, verifier=verifier
    )
    assert checked.verdict == "supported"
    assert (
        IndependentClaimVerification.model_validate(checked.model_dump(mode="json"))
        == checked
    )


def test_changed_input_is_rejected_even_when_rehashed(packet, verifier):
    changed = packet.model_copy(update={"claim": "A different claim"})
    data = result(changed, verifier, checked_input_digest=changed.input_digest())
    with pytest.raises(ValueError, match="expected input"):
        validate_independent_verification(data, packet=packet, verifier=verifier)


def test_changed_verifier_is_rejected(packet, verifier):
    other = verifier.model_copy(update={"model": "other"})
    with pytest.raises(ValueError, match="expected verifier"):
        validate_independent_verification(
            result(packet, other), packet=packet, verifier=verifier
        )


@pytest.mark.parametrize(
    "changes,match",
    [
        ({"evidence_span_ids": ["missing"]}, "unavailable"),
        ({"evidence_span_ids": ["span-1", "span-1"]}, "unique"),
        (
            {"verdict": "supported", "evidence_span_ids": []},
            "requires supporting",
        ),
        (
            {"verdict": "contradicted", "contradiction_span_ids": []},
            "requires contradictory",
        ),
        (
            {
                "verdict": "insufficient",
                "publish_recommendation": True,
            },
            "exceeds verifier verdict",
        ),
        ({"confidence": 69}, "exceeds verifier verdict"),
    ],
)
def test_unsafe_or_untraceable_verdicts_fail_closed(packet, verifier, changes, match):
    with pytest.raises(ValueError, match=match):
        IndependentClaimVerification.model_validate(result(packet, verifier, **changes))


def test_unknown_authority_fields_are_rejected(packet, verifier):
    data = deepcopy(result(packet, verifier))
    data["human_approved"] = True
    with pytest.raises(ValueError):
        IndependentClaimVerification.model_validate(data)


def test_contradiction_spans_are_a_subset_of_cited_evidence(packet, verifier):
    checked = IndependentClaimVerification.model_validate(
        result(
            packet,
            verifier,
            verdict="contradicted",
            contradiction_span_ids=["span-1"],
            publish_recommendation=False,
        )
    )
    assert checked.contradiction_span_ids == ("span-1",)

    with pytest.raises(ValueError, match="included in cited evidence"):
        IndependentClaimVerification.model_validate(
            result(
                packet,
                verifier,
                verdict="contradicted",
                evidence_span_ids=[],
                contradiction_span_ids=["span-1"],
                publish_recommendation=False,
            )
        )


def test_frozen_corpus_satisfies_design_guards():
    corpus = load_claim_verification_corpus(
        "docs/experiments/claim-verification/w12.3-cases.json"
    )
    assert len(corpus.cases) == 12
    assert sum(case.reference.publish for case in corpus.cases) == 4
    assert (
        sum(
            case.packet.risk == "high"
            and case.control_publish
            and not case.reference.publish
            for case in corpus.cases
        )
        == 5
    )
