"""Synthetic complete-publication render audits, not independent truth judgments."""

import json

from agent.experimental.knowledge import text_digest
from agent.experimental.publication import FixtureRenderAudit, RenderInput
from agent.experimental.research_publication import RESEARCH_PUBLICATION_SCHEMA


def research_publication_payload(pinned, publication, context):
    research = pinned.revision.research
    structure = research.verifications.structure
    claim = structure.claims[0]
    audits = []
    for layer in ("summary", "analysis", "dossier"):
        checked = RenderInput(
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
                        "evidence_ids": [e.evidence_id for e in structure.evidence],
                    }
                ],
                "question_ids": [q.question_id for q in research.questions],
                "conflict_ids": [c.conflict_id for c in research.conflicts],
            },
        )
        audits.append(
            FixtureRenderAudit(
                audit_id=f"audit-{layer}",
                checked_input=checked,
                checked_input_digest=checked.input_digest(),
                verdict="pass",
                checked_output_digest=text_digest(checked.rendered_text()),
                checked_at="2026-09-05T02:00:00Z",
                reason="Synthetic fixture render audit",
            ).model_dump(mode="json")
        )
    return json.dumps(
        {
            "schema_version": RESEARCH_PUBLICATION_SCHEMA,
            "revision_id": structure.revision_id,
            "revision_digest": pinned.document.digest,
            "research": research.model_dump(mode="json"),
            "publication": {
                "schema_version": "fixture-publication/1",
                "audits": audits,
            },
        }
    ).encode()
