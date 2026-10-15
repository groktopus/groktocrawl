"""Fixture verdict binding must fail closed on changed or substituted inputs."""

from copy import deepcopy

import pytest
from agent.experimental.knowledge import validate_structure
from agent.experimental.verification import (
    FixtureVerification,
    FixtureVerifier,
    VerificationInput,
    validate_fixture_verifications,
)

from tests.unit.test_knowledge_structure import payload  # noqa: F401


@pytest.fixture
def context(request):
    structure = validate_structure(
        request.getfixturevalue("payload"), scope_id="owner", research_id="research", revision_id="r1"
    )
    verifier = FixtureVerifier(
        kind="fixture_expectation", identity="hand-authored-rubric", version="1"
    )
    return VerificationInput(
        schema_version="fixture-verification-input/1",
        structure=structure,
        subject_id="c1",
        check_type="semantic_support",
        policy_version="fixture/1",
        verifier=verifier,
        evidence_ids=("e1",),
    )


def record(context, verdict="pass"):
    return FixtureVerification(
        verification_id="v1",
        checked_input=context,
        checked_input_digest=context.input_digest(),
        verdict=verdict,
        checked_at="2026-09-05T00:00:00Z",
        reason="Hand-authored fixture judgment",
    )


def envelope(context, records):
    return {
        "schema_version": "fixture-verifications/1",
        "structure": context.structure,
        "policy_version": context.policy_version,
        "verifier": context.verifier,
        "records": records,
    }


def validate(context, data):
    return validate_fixture_verifications(
        data,
        structure=context.structure,
        policy_version=context.policy_version,
        verifier=context.verifier,
    )


@pytest.mark.parametrize("verdict", ["pass", "fail", "indeterminate"])
def test_verdicts_remain_separate_from_assessment(context, verdict):
    result = validate(context, envelope(context, [record(context, verdict)]))
    assert result.records[0].verdict == verdict
    assert result.structure.claims[0].assessment == "unassessed"
    assert validate(context, result.model_dump(mode="json")) == result


@pytest.mark.parametrize(
    "field,value",
    [
        ("policy_version", "changed"),
        ("subject_id", "missing"),
        ("evidence_ids", ["foreign"]),
        ("evidence_ids", ["e1", "e1"]),
    ],
)
def test_changed_verification_input_rejected(context, field, value):
    data = record(context).model_dump(mode="json")
    data["checked_input"][field] = value
    with pytest.raises(ValueError):
        FixtureVerification.model_validate(data)


def test_changed_claim_invalidates_input_digest(context):
    data = record(context).model_dump(mode="json")
    data["checked_input"]["structure"]["claims"][0]["text"] = "Different meaning"
    with pytest.raises(ValueError, match="digest"):
        FixtureVerification.model_validate(data)


@pytest.mark.parametrize("change", ["scope", "revision", "policy", "verifier"])
def test_rehashed_foreign_context_still_rejected(context, change):
    data = context.model_dump(mode="json")
    if change in {"scope", "revision"}:
        data["structure"][f"{change}_id"] = "other"
    elif change == "policy":
        data["policy_version"] = "other"
    else:
        data["verifier"]["version"] = "other"
    foreign = VerificationInput.model_validate(data)
    with pytest.raises(ValueError):
        validate(context, envelope(context, [record(foreign)]))
    with pytest.raises(ValueError):
        validate(context, envelope(foreign, [record(foreign)]))


@pytest.mark.parametrize("kind", ["human_reviewed", "human", "model"])
def test_fixture_cannot_claim_human_or_model_verification(context, kind):
    data = context.model_dump(mode="json")
    data["verifier"]["kind"] = kind
    with pytest.raises(ValueError):
        VerificationInput.model_validate(data)


def test_duplicate_and_aliased_verification_ids(context):
    original = record(context)
    with pytest.raises(ValueError, match="unique"):
        validate(context, envelope(context, [original, original]))
    aliased = original.model_dump(mode="json")
    aliased["verification_id"] = "c1"
    with pytest.raises(ValueError, match="alias"):
        validate(context, envelope(context, [aliased]))


def test_forged_model_copy_revalidated(context):
    forged = record(context).model_copy(update={"checked_input_digest": "0" * 64})
    with pytest.raises(ValueError, match="digest"):
        validate(context, envelope(context, [forged]))


def test_unknown_approval_field_and_naive_time_rejected(context):
    original = record(context).model_dump(mode="json")
    for extra in [{"human_reviewed": True}, {"checked_at": "2026-09-05T00:00:00"}]:
        with pytest.raises(ValueError):
            FixtureVerification.model_validate({**deepcopy(original), **extra})
