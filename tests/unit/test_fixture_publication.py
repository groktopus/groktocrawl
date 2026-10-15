"""Synthetic three-layer journeys and deliberately invalid publication attempts."""

from copy import deepcopy

import pytest
from agent.experimental.knowledge import text_digest, validate_structure
from agent.experimental.publication import (
    FixtureRenderAudit,
    FixtureResearch,
    RenderInput,
    validate_fixture_publication,
)
from agent.experimental.verification import (
    FixtureAssessment,
    FixtureVerification,
    FixtureVerificationSet,
    FixtureVerifier,
    VerificationInput,
)

from tests.unit.test_knowledge_structure import payload  # noqa: F401


def journey(raw, scenario):
    data = deepcopy(raw)
    data["claims"][0]["assessment"] = "supported"
    data["claims"][0]["temporal_scope"] = "historical"
    data["as_of"] = "2026-09-05T00:00:00Z"
    conflicts = []
    status = "answered"
    if scenario != "supported":
        status = "unresolved"
        data["claims"][0]["text"] = (
            "The captured evidence does not establish the current price."
        )
        data["claims"][0]["kind"] = "inference"
    if scenario == "conflicting":
        data["claims"].append(
            {
                **data["claims"][0],
                "claim_id": "current-price",
                "text": "Current price is $20.",
                "assessment": "contested",
            }
        )
        second = deepcopy(data["evidence"][0])
        second["evidence_id"] = "e2"
        second["snapshot_id"] = "s2"
        second["quote"] = second["quote"].replace("$20", "$30")
        second["quote_digest"] = text_digest(second["quote"])
        snapshot = deepcopy(data["snapshots"][0])
        snapshot.update(snapshot_id="s2", canonical_url="https://example.test/help")
        snapshot["text"] = snapshot["text"].replace("$20", "$30")
        snapshot["digest"] = text_digest(snapshot["text"])
        data["snapshots"].append(snapshot)
        data["claims"][0]["text"] = (
            "The captured pages disagree: $20 versus $30 per month; the current price is unresolved."
        )
        data["evidence"].append(second)
        data["relationships"].append(
            {
                "relationship_id": "contradiction",
                "kind": "contradicts",
                "source_id": "e2",
                "target_id": "current-price",
                "rationale": "Fixture contradiction",
            }
        )
        conflicts = [
            {
                "conflict_id": "conflict-price",
                "question_id": "price",
                "claim_id": "current-price",
                "evidence_ids": ["e1", "e2"],
                "reason": "Unresolved fixture disagreement",
            }
        ]
    structure = validate_structure(
        data, scope_id="owner", research_id="research", revision_id="r1"
    )
    verifier = FixtureVerifier(
        kind="fixture_expectation", identity="hand-authored", version="1"
    )
    records = []
    for check in ["semantic_support", "freshness", "conflict_coverage"]:
        context = VerificationInput(
            schema_version="fixture-verification-input/1",
            structure=structure,
            subject_id="c1",
            check_type=check,
            freshness={
                "policy_version": "fixture/1",
                "evaluated_at": "2026-09-05T00:00:00Z",
                "as_of": "2026-09-05T00:00:00Z",
                "sources": [
                    {
                        "snapshot_id": snapshot.snapshot_id,
                        "basis": "historical_snapshot",
                        "max_age_seconds": 86400,
                        "reason": "Report captured evidence only; current price is not established",
                    }
                    for snapshot in structure.snapshots
                ],
            }
            if check == "freshness"
            else None,
            policy_version="fixture/1",
            verifier=verifier,
            evidence_ids=("e1", "e2") if scenario == "conflicting" else ("e1",),
        )
        records.append(
            FixtureVerification(
                verification_id=f"v-{check}",
                checked_input=context,
                checked_input_digest=context.input_digest(),
                verdict="pass",
                checked_at="2026-09-05T00:00:00Z",
                reason="Fixture expectation, not semantic proof",
            )
        )
    assessments = []
    for claim in structure.claims:
        if claim.assessment == "unassessed":
            continue
        context = VerificationInput(
            schema_version="fixture-verification-input/1",
            structure=structure,
            subject_id=claim.claim_id,
            check_type="assessment",
            policy_version="fixture/1",
            verifier=verifier,
            evidence_ids=tuple(e.evidence_id for e in structure.evidence),
        )
        assessments.append(
            FixtureAssessment(
                assessment_id=f"assessment-{claim.claim_id}",
                checked_input=context,
                checked_input_digest=context.input_digest(),
                outcome=claim.assessment,
                checked_at="2026-09-05T00:00:00Z",
                reason="Explicit fixture assessment",
            )
        )
    verifications = FixtureVerificationSet(
        schema_version="fixture-verifications/1",
        structure=structure,
        policy_version="fixture/1",
        verifier=verifier,
        records=records,
        assessments=assessments,
        assessment_links=[
            {
                "claim_id": a.checked_input.subject_id,
                "assessment_ids": [a.assessment_id],
            }
            for a in assessments
        ],
    )
    research = FixtureResearch(
        verifications=verifications,
        questions=[
            {
                "question_id": "price",
                "question": "What is the current price?",
                "status": status,
                "report_claim_id": "c1",
            }
        ],
        conflicts=conflicts,
    )
    inputs = []
    for layer in ["summary", "analysis", "dossier"]:
        inputs.append(
            RenderInput(
                schema_version="fixture-render-input/1",
                research=research,
                artifact_set_id="set1",
                renderer_version="fixture-render/1",
                auditor=verifier,
                artifact={
                    "artifact_id": layer,
                    "layer": layer,
                    "statements": [
                        {
                            "text": structure.claims[0].text,
                            "claim_ids": ["c1"],
                            "evidence_ids": ["e1", "e2"]
                            if scenario == "conflicting"
                            else ["e1"],
                        }
                    ],
                    "question_ids": ["price"],
                    "conflict_ids": [c.conflict_id for c in research.conflicts],
                },
            )
        )
    return research, inputs


def audited(inputs):
    return {
        "schema_version": "fixture-publication/1",
        "audits": [
            FixtureRenderAudit(
                audit_id=f"audit-{item.artifact.layer}",
                checked_input=item,
                checked_input_digest=item.input_digest(),
                verdict="pass",
                checked_output_digest=text_digest(item.rendered_text()),
                checked_at="2026-09-05T00:00:00Z",
                reason="Hand-authored expected audit",
            )
            for item in inputs
        ],
    }


def validate(research, data):
    return validate_fixture_publication(
        data,
        research=research,
        artifact_set_id="set1",
        renderer_version="fixture-render/1",
        auditor=research.verifications.verifier,
    )


@pytest.mark.parametrize(
    "scenario,coverage",
    [
        ("supported", "complete"),
        ("conflicting", "insufficient"),
        ("insufficient", "insufficient"),
    ],
)
def test_three_layer_fixture_journey(request, scenario, coverage):
    research, inputs = journey(request.getfixturevalue("payload"), scenario)
    result = validate(research, audited(inputs))
    assert research.coverage() == coverage
    assert len(result.audits) == 3
    dossier = result.audits[2].checked_input.rendered_text()
    assert '"verifications"' in dossier
    assert research.verifications.structure.snapshots[0].text in dossier.replace(
        "\\n", "\n"
    )


@pytest.mark.parametrize(
    "mutation", ["text", "layer", "revision", "renderer", "verifier"]
)
def test_changed_render_invalidates_audit(request, mutation):
    research, inputs = journey(request.getfixturevalue("payload"), "supported")
    data = audited(inputs)
    first = data["audits"][0].model_dump(mode="json")
    target = first["checked_input"]
    if mutation == "text":
        target["artifact"]["statements"][0]["text"] = "Invented answer"
    elif mutation == "layer":
        target["artifact"]["layer"] = "dossier"
    elif mutation == "revision":
        target["research"]["verifications"]["structure"]["revision_id"] = "r2"
    elif mutation == "renderer":
        target["renderer_version"] = "other"
    else:
        target["auditor"]["version"] = "other"
    data["audits"][0] = first
    with pytest.raises(ValueError):
        validate(research, data)


@pytest.mark.parametrize(
    "mutation", ["question", "conflict", "claim", "citation", "context"]
)
def test_rehashed_invalid_render_rejected(request, mutation):
    research, inputs = journey(request.getfixturevalue("payload"), "conflicting")
    item = inputs[0].model_dump(mode="json")
    if mutation == "question":
        item["artifact"]["question_ids"] = ["other"]
    elif mutation == "conflict":
        item["artifact"]["conflict_ids"] = []
    elif mutation == "claim":
        item["artifact"]["statements"][0]["claim_ids"] = ["current-price"]
    elif mutation == "citation":
        item["artifact"]["statements"][0]["evidence_ids"] = ["e2"]
    else:
        item["artifact_set_id"] = "foreign-set"
    inputs[0] = RenderInput.model_validate(item)
    with pytest.raises(ValueError):
        validate(research, audited(inputs))


@pytest.mark.parametrize("verdict", ["fail", "indeterminate"])
def test_any_adverse_support_verdict_blocks_even_with_pass(request, verdict):
    research, inputs = journey(request.getfixturevalue("payload"), "supported")
    data = research.model_dump(mode="json")
    adverse = deepcopy(data["verifications"]["records"][0])
    adverse.update(verification_id="adverse", verdict=verdict)
    data["verifications"]["records"].append(adverse)
    research = FixtureResearch.model_validate(data)
    inputs = [
        RenderInput.model_validate({**i.model_dump(), "research": research})
        for i in inputs
    ]
    with pytest.raises(ValueError, match="ineligible"):
        validate(research, audited(inputs))


def test_hidden_conflict_and_missing_check_rejected(request):
    research, _ = journey(request.getfixturevalue("payload"), "conflicting")
    data = research.model_dump(mode="json")
    data["conflicts"] = []
    with pytest.raises(ValueError, match="explicit conflict"):
        FixtureResearch.model_validate(data)
    research, inputs = journey(request.getfixturevalue("payload"), "supported")
    data = research.model_dump(mode="json")
    data["verifications"]["records"].pop()
    research = FixtureResearch.model_validate(data)
    inputs = [
        RenderInput.model_validate({**i.model_dump(), "research": research})
        for i in inputs
    ]
    with pytest.raises(ValueError, match="ineligible"):
        validate(research, audited(inputs))


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "failed_audit"])
def test_all_layers_and_audits_required(request, mutation):
    research, inputs = journey(request.getfixturevalue("payload"), "supported")
    data = audited(inputs)
    if mutation == "missing":
        data["audits"].pop()
    elif mutation == "duplicate":
        data["audits"][1] = data["audits"][0]
    else:
        data["audits"][0] = data["audits"][0].model_copy(update={"verdict": "fail"})
    with pytest.raises(ValueError):
        validate(research, data)


def test_partial_coverage_preserves_unresolved_question(request):
    research, inputs = journey(request.getfixturevalue("payload"), "conflicting")
    data = research.model_dump(mode="json")
    data["questions"].append(
        {
            "question_id": "agreement",
            "question": "Do the captured pages agree?",
            "status": "answered",
            "report_claim_id": "c1",
        }
    )
    research = FixtureResearch.model_validate(data)
    updated = []
    for item in inputs:
        raw = item.model_dump(mode="json")
        raw["research"] = research.model_dump(mode="json")
        raw["artifact"]["question_ids"].append("agreement")
        updated.append(RenderInput.model_validate(raw))
    assert research.coverage() == "partial"
    assert len(validate(research, audited(updated)).audits) == 3


def test_conflict_cannot_claim_question_answered(request):
    research, _ = journey(request.getfixturevalue("payload"), "conflicting")
    data = research.model_dump(mode="json")
    data["questions"][0]["status"] = "answered"
    with pytest.raises(ValueError, match="unresolved"):
        FixtureResearch.model_validate(data)


def test_output_bytes_and_audit_time_are_validated(request):
    research, inputs = journey(request.getfixturevalue("payload"), "supported")
    for field, value in [
        ("checked_output_digest", "0" * 64),
        ("checked_at", "2026-09-05T00:00:00"),
    ]:
        data = audited(inputs)
        first = data["audits"][0].model_dump(mode="json")
        first[field] = value
        data["audits"][0] = first
        with pytest.raises(ValueError):
            validate(research, data)
