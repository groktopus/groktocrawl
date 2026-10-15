"""Temporal metadata constrains fixture verdicts without claiming semantic truth."""

from copy import deepcopy

import pytest
from agent.experimental.knowledge import KnowledgeStructure
from agent.experimental.verification import FixtureVerification, VerificationInput

from tests.unit.test_fixture_publication import audited, journey, validate
from tests.unit.test_knowledge_structure import payload  # noqa: F401


@pytest.fixture
def freshness(request):
    research, _ = journey(request.getfixturevalue("payload"), "supported")
    return research.verifications.records[1].model_dump(mode="json")


def rehash(data):
    data["checked_input_digest"] = VerificationInput.model_validate(
        data["checked_input"]
    ).input_digest()
    return FixtureVerification.model_validate(data)


def test_unknown_dates_remain_null(freshness):
    snapshot = rehash(freshness).checked_input.structure.snapshots[0]
    assert snapshot.published_at is None
    assert snapshot.effective_at is None
    assert snapshot.origin_id is None
    assert snapshot.lineage_id is None


@pytest.mark.parametrize("basis", ["published_at", "effective_at"])
def test_dated_current_fixture_pass(freshness, basis):
    context = freshness["checked_input"]
    context["structure"]["claims"][0]["temporal_scope"] = "current"
    context["structure"]["snapshots"][0][basis] = {
        "value": "2026-09-04T12:00:00Z",
        "provenance": "Fixture source metadata",
    }
    context["freshness"]["sources"][0]["basis"] = basis
    assert rehash(freshness).verdict == "pass"


@pytest.mark.parametrize(
    "case",
    ["unknown", "absent", "stale", "future", "current_history", "future_capture"],
)
def test_invalid_temporal_basis_cannot_pass(freshness, case):
    context = freshness["checked_input"]
    source = context["freshness"]["sources"][0]
    if case == "unknown":
        source["basis"] = "unknown"
    elif case in {"absent", "stale", "future"}:
        source["basis"] = "effective_at"
        if case != "absent":
            context["structure"]["snapshots"][0]["effective_at"] = {
                "value": "2020-01-01T00:00:00Z"
                if case == "stale"
                else "2027-01-01T00:00:00Z",
                "provenance": "Recorded fixture date",
            }
    elif case == "current_history":
        context["structure"]["claims"][0]["temporal_scope"] = "current"
    else:
        context["structure"]["snapshots"][0]["retrieved_at"] = "2027-01-01T00:00:00Z"
    with pytest.raises(ValueError, match="cannot authorize"):
        rehash(freshness)
    for verdict in ["fail", "indeterminate"]:
        freshness["verdict"] = verdict
        assert rehash(freshness).verdict == verdict


@pytest.mark.parametrize(
    "case",
    [
        "missing",
        "policy",
        "as_of",
        "future_as_of",
        "uncovered",
        "duplicate",
        "foreign",
        "time",
        "wrong_check",
    ],
)
def test_inapplicable_freshness_rejected(freshness, case):
    context = freshness["checked_input"]
    details = context["freshness"]
    if case == "missing":
        context["freshness"] = None
    elif case == "policy":
        details["policy_version"] = "other"
    elif case == "as_of":
        details["as_of"] = "2026-09-04T00:00:00Z"
    elif case == "future_as_of":
        context["structure"]["as_of"] = details["as_of"] = "2027-01-01T00:00:00Z"
    elif case == "uncovered":
        context["evidence_ids"] = []
    elif case == "duplicate":
        details["sources"].append(deepcopy(details["sources"][0]))
    elif case == "foreign":
        details["sources"][0]["snapshot_id"] = "foreign"
    elif case == "time":
        freshness["checked_at"] = "2026-09-06T00:00:00Z"
    else:
        context["check_type"] = "semantic_support"
    with pytest.raises(ValueError):
        rehash(freshness)


@pytest.mark.parametrize("value", ["2026-09-04T00:00:00", "2026-09-04T00:00:00+01:00"])
def test_non_utc_dates_rejected(freshness, value):
    for field in ["as_of", "evaluated_at"]:
        changed = deepcopy(freshness)
        changed["checked_input"]["freshness"][field] = value
        with pytest.raises(ValueError, match="UTC"):
            rehash(changed)
    structure = deepcopy(freshness["checked_input"]["structure"])
    structure["as_of"] = value
    with pytest.raises(ValueError, match="UTC"):
        KnowledgeStructure.model_validate(structure)
    structure["as_of"] = None
    structure["snapshots"][0]["published_at"] = {
        "value": value,
        "provenance": "fixture",
    }
    with pytest.raises(ValueError, match="UTC"):
        KnowledgeStructure.model_validate(structure)


def test_date_requires_provenance(freshness):
    structure = freshness["checked_input"]["structure"]
    structure["snapshots"][0]["effective_at"] = {"value": "2026-09-04T00:00:00Z"}
    with pytest.raises(ValueError):
        KnowledgeStructure.model_validate(structure)


@pytest.mark.parametrize(
    "field,value",
    [
        ("origin_id", "publisher"),
        ("lineage_id", "copied"),
        ("published_at", {"value": "2026-09-04T00:00:00Z", "provenance": "fixture"}),
    ],
)
def test_metadata_changes_invalidate_verdict_and_render(
    request, freshness, field, value
):
    freshness["checked_input"]["structure"]["snapshots"][0][field] = value
    with pytest.raises(ValueError, match="digest"):
        FixtureVerification.model_validate(freshness)
    raw = request.getfixturevalue("payload")
    research, inputs = journey(raw, "supported")
    old_audits = audited(inputs)
    raw["snapshots"][0][field] = value
    changed, _ = journey(raw, "supported")
    with pytest.raises(ValueError):
        validate(changed, old_audits)
    assert research != changed


def test_copied_lineage_and_unknown_dates_survive_dossier(request):
    raw = request.getfixturevalue("payload")
    raw["snapshots"][0].update(origin_id="publisher", lineage_id="same-source")
    research, inputs = journey(raw, "conflicting")
    snapshots = research.verifications.structure.snapshots
    assert snapshots[0].canonical_url != snapshots[1].canonical_url
    assert {s.lineage_id for s in snapshots} == {"same-source"}
    assert {s.origin_id for s in snapshots} == {"publisher"}
    assert all(s.effective_at is None for s in snapshots)
    result = validate(research, audited(inputs))
    dossier = result.audits[2].checked_input.rendered_text()
    assert '"lineage_id":"same-source"' in dossier
    assert research.coverage() == "insufficient"


def test_unchecked_support_source_blocks_publication(request):
    from agent.experimental.publication import FixtureResearch, RenderInput

    research, inputs = journey(request.getfixturevalue("payload"), "conflicting")
    raw = research.model_dump(mode="json")
    record = raw["verifications"]["records"][1]
    record["checked_input"]["evidence_ids"] = ["e1"]
    record["checked_input"]["freshness"]["sources"] = record["checked_input"][
        "freshness"
    ]["sources"][:1]
    raw["verifications"]["records"][1] = rehash(record).model_dump(mode="json")
    changed = FixtureResearch.model_validate(raw)
    inputs = [
        RenderInput.model_validate({**i.model_dump(), "research": changed})
        for i in inputs
    ]
    with pytest.raises(ValueError, match="ineligible"):
        validate(changed, audited(inputs))


def test_changed_freshness_basis_invalidates_digest(freshness):
    freshness["checked_input"]["freshness"]["sources"][0]["max_age_seconds"] = 100
    with pytest.raises(ValueError, match="digest"):
        FixtureVerification.model_validate(freshness)
