"""Hand-authored fixture verdicts, never authenticated semantic judgments."""

import json

from agent.experimental.knowledge import text_digest
from agent.experimental.publication import (
    FixtureRenderAudit,
    FixtureResearch,
    RenderInput,
)
from agent.experimental.publication_store import PublicationContext
from agent.experimental.verification import (
    FixtureAssessment,
    FixtureVerification,
    FixtureVerificationSet,
    FixtureVerifier,
    VerificationInput,
)

AT = "2026-09-05T00:00:00Z"
VERIFIER = FixtureVerifier(
    kind="fixture_expectation", identity="storage-fixture", version="1"
)
CONTEXT = PublicationContext(
    policy_version="storage-fixture/1",
    verifier=VERIFIER,
    renderer_version="fixture-render/1",
    auditor=VERIFIER,
)


CONTEXT_V2 = CONTEXT.model_copy(update={"renderer_version": "fixture-render/2"})


def supported_revision(raw):
    data = json.loads(raw)
    data["structure"]["as_of"] = AT
    data["structure"]["claims"][0].update(
        assessment="supported", temporal_scope="historical"
    )
    return json.dumps(data).encode()


def publication_payload(structure, publication, context=CONTEXT):
    claim = structure.claims[0]
    evidence = tuple(e.evidence_id for e in structure.evidence)
    records = []
    for check in ("semantic_support", "freshness", "conflict_coverage"):
        checked = VerificationInput(
            schema_version="fixture-verification-input/1",
            structure=structure,
            subject_id=claim.claim_id,
            check_type=check,
            policy_version=context.policy_version,
            verifier=context.verifier,
            evidence_ids=evidence,
            freshness={
                "policy_version": context.policy_version,
                "evaluated_at": AT,
                "as_of": AT,
                "sources": [
                    {
                        "snapshot_id": s.snapshot_id,
                        "basis": "historical_snapshot",
                        "max_age_seconds": 604800,
                        "reason": "Historical fixture only",
                    }
                    for s in structure.snapshots
                ],
            }
            if check == "freshness"
            else None,
        )
        records.append(
            FixtureVerification(
                verification_id=f"v-{check}",
                checked_input=checked,
                checked_input_digest=checked.input_digest(),
                verdict="pass",
                checked_at=AT,
                reason="Hand-authored fixture expectation only",
            )
        )
    checked = VerificationInput(
        schema_version="fixture-verification-input/1",
        structure=structure,
        subject_id=claim.claim_id,
        check_type="assessment",
        policy_version=context.policy_version,
        verifier=context.verifier,
        evidence_ids=evidence,
    )
    assessment = FixtureAssessment(
        assessment_id="a-1",
        checked_input=checked,
        checked_input_digest=checked.input_digest(),
        outcome="supported",
        checked_at=AT,
        reason="Hand-authored fixture assessment only",
    )
    verifications = FixtureVerificationSet(
        schema_version="fixture-verifications/1",
        structure=structure,
        policy_version=context.policy_version,
        verifier=context.verifier,
        records=records,
        assessments=[assessment],
        assessment_links=[{"claim_id": claim.claim_id, "assessment_ids": ["a-1"]}],
    )
    research = FixtureResearch(
        verifications=verifications,
        questions=[
            {
                "question_id": "q-1",
                "question": "What did the fixture say?",
                "status": "answered",
                "report_claim_id": claim.claim_id,
            }
        ],
        conflicts=[],
    )
    audits = []
    for layer in ("summary", "analysis", "dossier"):
        rendered = RenderInput(
            schema_version="fixture-render-input/1",
            research=research,
            artifact_set_id=str(publication),
            renderer_version=context.renderer_version,
            auditor=context.auditor,
            artifact={
                "artifact_id": layer,
                "layer": layer,
                "statements": [
                    {
                        "text": claim.text,
                        "claim_ids": [claim.claim_id],
                        "evidence_ids": evidence,
                    }
                ],
                "question_ids": ["q-1"],
                "conflict_ids": [],
            },
        )
        audits.append(
            FixtureRenderAudit(
                audit_id=f"audit-{layer}",
                checked_input=rendered,
                checked_input_digest=rendered.input_digest(),
                verdict="pass",
                checked_output_digest=text_digest(rendered.rendered_text()),
                checked_at=AT,
                reason="Hand-authored fixture render audit only",
            ).model_dump(mode="json")
        )
    return json.dumps(
        {
            "schema_version": "retained-fixture-publication/1",
            "revision_id": structure.revision_id,
            "research": research.model_dump(mode="json"),
            "publication": {
                "schema_version": "fixture-publication/1",
                "audits": audits,
            },
        }
    ).encode()
