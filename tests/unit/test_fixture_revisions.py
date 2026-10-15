"""History identity checks remain separate from current publication eligibility."""

from copy import deepcopy

import pytest
from agent.experimental.publication import FixtureResearch, RenderInput
from agent.experimental.revisions import (
    FixtureHistory,
    FixtureRevision,
    append_revision,
    validate_history,
    validate_latest_publication,
)
from agent.experimental.verification import VerificationInput

from tests.unit.test_fixture_publication import audited, journey
from tests.unit.test_knowledge_structure import payload  # noqa: F401


def entities(research):
    structure = research.verifications.structure
    groups = [
        ("snapshot", "snapshot_id", structure.snapshots),
        ("evidence", "evidence_id", structure.evidence),
        ("claim", "claim_id", structure.claims),
        ("relationship", "relationship_id", structure.relationships),
        ("verification", "verification_id", research.verifications.records),
        ("assessment", "assessment_id", research.verifications.assessments),
        ("question", "question_id", research.questions),
        ("conflict", "conflict_id", research.conflicts),
    ]
    return {
        getattr(item, field): kind for kind, field, items in groups for item in items
    }


def revision(research, identity="r1", prior=(), rename=None):
    replacements = dict(rename or {})
    replacements[research.verifications.structure.revision_id] = identity
    for record in research.verifications.records:
        replacements[record.verification_id] = f"{record.verification_id}-{identity}"

    for assessment in research.verifications.assessments:
        replacements[assessment.assessment_id] = (
            f"{assessment.assessment_id}-{identity}"
        )

    def replace(value):
        if isinstance(value, dict):
            return {k: replace(v) for k, v in value.items()}
        if isinstance(value, list):
            return [replace(v) for v in value]
        return replacements.get(value, value) if isinstance(value, str) else value

    raw = replace(research.model_dump(mode="json"))
    raw["objective"] = "Inspect fixture evidence"
    for record in raw["verifications"]["records"] + raw["verifications"]["assessments"]:
        record["checked_input_digest"] = VerificationInput.model_validate(
            record["checked_input"]
        ).input_digest()
    changed = FixtureResearch.model_validate(raw)
    known = {key for item in prior for key in entities(item.research)}
    predecessors = {new: old for old, new in (rename or {}).items()}
    return FixtureRevision(
        schema_version="fixture-revision/1",
        parent_revision_id=prior[-1].research.verifications.structure.revision_id
        if prior
        else None,
        created_at="2026-09-05T01:00:00Z",
        research=changed,
        introductions=[
            {"kind": kind, "entity_id": key, "predecessor_id": predecessors.get(key)}
            for key, kind in entities(changed).items()
            if key not in known
        ],
    )


def history(*revisions):
    return FixtureHistory(schema_version="fixture-history/1", revisions=revisions)


@pytest.fixture
def root(request):
    research, _ = journey(request.getfixturevalue("payload"), "supported")
    return revision(research)


def test_reverification_preserves_entities_and_prior_verdicts(root):
    first = history(root)
    # A new revision requires new bound verification IDs, even with identical claims.
    second = revision(root.research, "r2", (root,))
    result = append_revision(first, second)
    assert result.revisions[0] == first.revisions[0]
    assert (
        result.revisions[0].research.verifications.structure.claims
        == result.revisions[1].research.verifications.structure.claims
    )
    assert len(second.introductions) == 4
    assert {item.kind for item in second.introductions} == {
        "verification",
        "assessment",
    }
    assert (
        validate_history(
            result.model_dump(mode="json"), scope_id="owner", research_id="research"
        )
        == result
    )


@pytest.mark.parametrize("change", ["claim", "source_metadata", "verification_verdict"])
def test_existing_identity_cannot_change(root, change):
    second = revision(root.research, "r2", (root,))
    raw = second.model_dump(mode="json")
    if change == "claim":
        # Rebuild every checked context around the changed, locally valid claim.
        for structure in [
            raw["research"]["verifications"]["structure"],
            *[
                r["checked_input"]["structure"]
                for r in raw["research"]["verifications"]["records"]
                + raw["research"]["verifications"]["assessments"]
            ],
        ]:
            structure["claims"][0]["text"] = "Changed meaning"
    elif change == "source_metadata":
        for structure in [
            raw["research"]["verifications"]["structure"],
            *[
                r["checked_input"]["structure"]
                for r in raw["research"]["verifications"]["records"]
                + raw["research"]["verifications"]["assessments"]
            ],
        ]:
            structure["snapshots"][0]["origin_id"] = "other"
    else:
        record = raw["research"]["verifications"]["records"][0]
        old = root.research.verifications.records[0].verification_id
        introduced = record["verification_id"]
        record["verification_id"] = old
        record["verdict"] = "fail"
        raw["introductions"] = [
            i for i in raw["introductions"] if i["entity_id"] != introduced
        ]
    for record in (
        raw["research"]["verifications"]["records"]
        + raw["research"]["verifications"]["assessments"]
    ):
        record["checked_input_digest"] = VerificationInput.model_validate(
            record["checked_input"]
        ).input_digest()
    with pytest.raises(ValueError, match="reassigned"):
        append_revision(history(root), FixtureRevision.model_validate(raw))


def test_corrected_evidence_uses_typed_predecessors(request, root):
    raw = request.getfixturevalue("payload")
    from agent.experimental.knowledge import text_digest

    raw["snapshots"][0]["text"] = raw["snapshots"][0]["text"].replace("$20", "$30")
    raw["snapshots"][0]["digest"] = text_digest(raw["snapshots"][0]["text"])
    raw["evidence"][0]["quote"] = raw["evidence"][0]["quote"].replace("$20", "$30")
    raw["evidence"][0]["quote_digest"] = text_digest(raw["evidence"][0]["quote"])
    raw["claims"][0]["text"] = raw["claims"][0]["text"].replace("$20", "$30")
    research, _ = journey(raw, "supported")
    renames = {"s1": "s2", "e1": "e2", "c1": "c2", "edge1": "edge2", "price": "price2"}
    corrected = revision(research, "r2", (root,), renames)
    result = append_revision(history(root), corrected)
    assert (
        result.revisions[0].research.verifications.structure.evidence[0].quote
        == "Price: $20 per month."
    )
    assert (
        result.revisions[1].research.verifications.structure.evidence[0].quote
        == "Price: $30 per month."
    )
    assert any(
        i.entity_id == "e2" and i.predecessor_id == "e1"
        for i in corrected.introductions
    )


@pytest.mark.parametrize(
    "change",
    [
        "parent",
        "root_parent",
        "duplicate_revision",
        "scope",
        "backwards",
        "missing_declaration",
        "duplicate_declaration",
        "wrong_kind",
        "missing_predecessor",
        "wrong_predecessor_kind",
    ],
)
def test_invalid_history_rejected(root, change):
    second = revision(root.research, "r2", (root,))
    raw = second.model_dump(mode="json")
    prefix = root
    if change == "parent":
        raw["parent_revision_id"] = "missing"
    elif change == "root_parent":
        prefix = FixtureRevision.model_validate(
            {**root.model_dump(), "parent_revision_id": "ghost"}
        )
    elif change == "duplicate_revision":
        raw = revision(root.research, "r1", (root,)).model_dump(mode="json")
    elif change == "scope":
        raw = revision(root.research, "r2", (root,), {"owner": "foreign"}).model_dump(
            mode="json"
        )
    elif change == "backwards":
        raw["created_at"] = "2026-09-05T00:30:00Z"
    elif change == "missing_declaration":
        raw["introductions"].pop()
    elif change == "duplicate_declaration":
        raw["introductions"].append(deepcopy(raw["introductions"][0]))
    elif change == "wrong_kind":
        raw["introductions"][0]["kind"] = "claim"
    elif change == "missing_predecessor":
        raw["introductions"][0]["predecessor_id"] = "missing"
    else:
        raw["introductions"][0]["predecessor_id"] = "c1"
    with pytest.raises(ValueError):
        history(prefix, FixtureRevision.model_validate(raw))


@pytest.mark.parametrize(
    "change", ["naive", "offset", "before_input", "no_objective", "alias"]
)
def test_invalid_revision_metadata(root, change):
    raw = root.model_dump(mode="json")
    if change == "naive":
        raw["created_at"] = "2026-09-05T01:00:00"
    elif change == "offset":
        raw["created_at"] = "2026-09-05T01:00:00+01:00"
    elif change == "before_input":
        raw["created_at"] = "2020-01-01T00:00:00Z"
    elif change == "no_objective":
        raw["research"]["objective"] = None
    else:
        raw["research"]["questions"][0]["question_id"] = "c1"
    with pytest.raises(ValueError):
        history(FixtureRevision.model_validate(raw))


@pytest.mark.parametrize("changed", [True, False])
def test_removed_identity_cannot_be_reassigned(request, changed):
    raw = request.getfixturevalue("payload")
    raw["claims"].append({**raw["claims"][0], "claim_id": "unused"})
    research, _ = journey(raw, "supported")
    first = revision(research)
    raw["claims"].pop()
    research, _ = journey(raw, "supported")
    second = revision(research, "r2", (first,))
    raw["claims"].append(
        {
            **raw["claims"][0],
            "claim_id": "unused",
            "text": "Reassigned" if changed else raw["claims"][0]["text"],
        }
    )
    research, _ = journey(raw, "supported")
    third = revision(research, "r3", (first, second))
    if changed:
        with pytest.raises(ValueError, match="reassigned"):
            history(first, second, third)
    else:
        assert len(history(first, second, third).revisions) == 3


def test_expected_scope_and_forged_models(root):
    valid = history(root)
    with pytest.raises(ValueError, match="expected"):
        validate_history(valid, scope_id="foreign", research_id="research")
    forged = root.model_copy(update={"parent_revision_id": "foreign"})
    with pytest.raises(ValueError):
        append_revision(valid, forged)


def test_history_cannot_authorize_old_publication(request, root):
    research, renders = journey(request.getfixturevalue("payload"), "supported")
    first_renders = [
        RenderInput.model_validate({**r.model_dump(), "research": root.research})
        for r in renders
    ]
    first = history(root)
    args = {
        "artifact_set_id": "set1",
        "renderer_version": "fixture-render/1",
        "auditor": research.verifications.verifier,
    }
    assert validate_latest_publication(first, audited(first_renders), **args)
    second = revision(root.research, "r2", (root,))
    latest = append_revision(first, second)
    with pytest.raises(ValueError, match="expected context"):
        validate_latest_publication(latest, audited(first_renders), **args)
    new_renders = [
        RenderInput.model_validate({**r.model_dump(), "research": second.research})
        for r in renders
    ]
    assert validate_latest_publication(latest, audited(new_renders), **args)
    assert first.revisions == (root,)


def test_old_verdict_cannot_be_applied_to_new_revision(root):
    second = revision(root.research, "r2", (root,))
    raw = second.research.model_dump(mode="json")
    raw["verifications"]["records"] = [
        r.model_dump(mode="json") for r in root.research.verifications.records
    ]
    with pytest.raises(ValueError, match="different structural context"):
        FixtureResearch.model_validate(raw)


def test_verification_predecessors_and_history_bound(root):
    second = revision(root.research, "r2", (root,))
    raw = second.model_dump(mode="json")
    for declaration, previous in zip(
        raw["introductions"],
        (
            *root.research.verifications.records,
            *root.research.verifications.assessments,
        ),
        strict=True,
    ):
        declaration["predecessor_id"] = (
            getattr(previous, "verification_id", None) or previous.assessment_id
        )
    assert (
        len(
            append_revision(
                history(root), FixtureRevision.model_validate(raw)
            ).revisions
        )
        == 2
    )
    with pytest.raises(ValueError):
        history(*([root] * 21))
