"""Assessment provenance is necessary context, never a verification verdict."""

from copy import deepcopy

import pytest
from agent.experimental.publication import FixtureResearch, RenderInput
from agent.experimental.revisions import FixtureRevision, append_revision
from agent.experimental.verification import (
    FixtureAssessment,
    FixtureVerification,
    FixtureVerificationSet,
    VerificationInput,
)

from tests.unit.test_fixture_publication import audited, journey, validate
from tests.unit.test_fixture_revisions import history, revision
from tests.unit.test_knowledge_structure import payload  # noqa: F401


@pytest.fixture
def assessed(request):
    research, _ = journey(request.getfixturevalue("payload"), "supported")
    return research.verifications.model_dump(mode="json")


def rebind(data):
    for record in data["records"] + data["assessments"]:
        record["checked_input"]["structure"] = deepcopy(data["structure"])
        record["checked_input_digest"] = VerificationInput.model_validate(
            record["checked_input"]
        ).input_digest()
    return FixtureVerificationSet.model_validate(data)


@pytest.mark.parametrize(
    "state", ["supported", "contested", "insufficient", "refuted", "unassessed"]
)
def test_assessment_states_remain_separate_from_verification(assessed, state):
    assessed["structure"]["claims"][0]["assessment"] = state
    if state == "unassessed":
        assessed["assessments"] = []
        assessed["assessment_links"] = []
    else:
        assessed["assessments"][0]["outcome"] = state
    result = rebind(assessed)
    assert all(record.verdict == "pass" for record in result.records)
    assert result.structure.claims[0].assessment == state
    assert (
        result.assessments[0].outcome == state
        if result.assessments
        else state == "unassessed"
    )


@pytest.mark.parametrize(
    "change",
    [
        "missing_link",
        "duplicate_link",
        "missing_record",
        "duplicate_record",
        "alias",
        "duplicate_reference",
        "foreign_link",
        "wrong_outcome",
        "orphan",
        "unassessed_link",
    ],
)
def test_invalid_assessment_links_fail_closed(assessed, change):
    if change == "missing_link":
        assessed["assessment_links"] = []
    elif change == "duplicate_link":
        assessed["assessment_links"].append(deepcopy(assessed["assessment_links"][0]))
    elif change == "missing_record":
        assessed["assessments"] = []
    elif change == "duplicate_record":
        assessed["assessments"].append(deepcopy(assessed["assessments"][0]))
    elif change == "alias":
        assessed["assessments"][0]["assessment_id"] = "c1"
    elif change == "duplicate_reference":
        assessed["assessment_links"][0]["assessment_ids"] *= 2
    elif change == "foreign_link":
        assessed["assessment_links"][0]["assessment_ids"] = ["foreign"]
    elif change == "wrong_outcome":
        assessed["assessments"][0]["outcome"] = "refuted"
    elif change == "orphan":
        extra = deepcopy(assessed["assessments"][0])
        extra["assessment_id"] = "unlinked"
        assessed["assessments"].append(extra)
    else:
        assessed["structure"]["claims"][0]["assessment"] = "unassessed"
    with pytest.raises(ValueError):
        rebind(assessed)


@pytest.mark.parametrize(
    "change", ["digest", "policy", "assessor", "kind", "naive", "human"]
)
def test_changed_or_invalid_assessment_input_rejected(assessed, change):
    record = assessed["assessments"][0]
    if change == "digest":
        record["checked_input"]["evidence_ids"] = []
    elif change == "policy":
        record["checked_input"]["policy_version"] = "foreign"
    elif change == "assessor":
        record["checked_input"]["verifier"]["identity"] = "foreign"
    elif change == "kind":
        record["checked_input"]["check_type"] = "semantic_support"
    elif change == "naive":
        record["checked_at"] = "2026-09-05T00:00:00"
    else:
        record["checked_input"]["verifier"]["kind"] = "human_reviewed"
    if change in {"policy", "assessor", "kind"}:
        record["checked_input_digest"] = VerificationInput.model_validate(
            record["checked_input"]
        ).input_digest()
    with pytest.raises(ValueError):
        FixtureVerificationSet.model_validate(assessed)


def test_assessment_input_cannot_masquerade_as_verification(assessed):
    source = assessed["assessments"][0]
    with pytest.raises(ValueError, match="not a verification"):
        FixtureVerification.model_validate(
            {
                "verification_id": "fake",
                "checked_input": source["checked_input"],
                "checked_input_digest": source["checked_input_digest"],
                "verdict": "pass",
                "checked_at": source["checked_at"],
                "reason": "fake",
            }
        )
    forged = FixtureAssessment.model_validate(source).model_copy(
        update={"checked_input_digest": "0" * 64}
    )
    with pytest.raises(ValueError, match="digest"):
        FixtureAssessment.model_validate(forged)


def test_supported_assessment_with_failed_verification_cannot_publish(request):
    research, renders = journey(request.getfixturevalue("payload"), "supported")
    raw = research.model_dump(mode="json")
    raw["verifications"]["records"][0]["verdict"] = "fail"
    research = FixtureResearch.model_validate(raw)
    assert research.verifications.assessments[0].outcome == "supported"
    renders = [
        RenderInput.model_validate({**r.model_dump(), "research": research})
        for r in renders
    ]
    with pytest.raises(ValueError, match="ineligible"):
        validate(research, audited(renders))


def test_assessment_changes_invalidate_render_input(request):
    research, renders = journey(request.getfixturevalue("payload"), "supported")
    old = audited(renders)
    raw = research.model_dump(mode="json")
    raw["verifications"]["assessments"][0]["reason"] = "Changed rationale"
    changed = FixtureResearch.model_validate(raw)
    with pytest.raises(ValueError, match="expected context"):
        validate(changed, old)
    assert '"assessment_links"' in renders[2].rendered_text()


def test_assessment_identity_is_immutable_across_revisions(request):
    research, _ = journey(request.getfixturevalue("payload"), "supported")
    first = revision(research)
    second = revision(first.research, "r2", (first,))
    raw = second.model_dump(mode="json")
    record = raw["research"]["verifications"]["assessments"][0]
    new_id = record["assessment_id"]
    old_id = first.research.verifications.assessments[0].assessment_id
    record["assessment_id"] = old_id
    raw["research"]["verifications"]["assessment_links"][0]["assessment_ids"] = [old_id]
    raw["introductions"] = [i for i in raw["introductions"] if i["entity_id"] != new_id]
    with pytest.raises(ValueError, match="reassigned"):
        append_revision(history(first), FixtureRevision.model_validate(raw))
