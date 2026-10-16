"""Executed, eligible reports ready for a caller's fenced publication transaction."""

import asyncio
from dataclasses import dataclass

from .checked_knowledge import admit_checked_history
from .context_sources import ContextSourceResolver
from .knowledge_checks import Reviewer
from .knowledge_execution import KnowledgeExecutionLedger
from .manifest_outputs import AdmittedManifest, ManifestResolver, admit_render_manifest
from .render_execution import RenderExecutionLedger


@dataclass(frozen=True)
class PublicationCandidate:
    admitted: AdmittedManifest
    fixture_only: bool


def _check_eligibility(admitted: AdmittedManifest) -> None:
    knowledge = admitted.knowledge
    inputs = {i.input_id: i for i in knowledge.verification_inputs}
    global_checks = [
        r
        for r in knowledge.verifications
        if inputs[r.input_id].check_type in {"structural", "conflict_coverage"}
    ]
    if {inputs[r.input_id].check_type for r in global_checks} != {
        "structural",
        "conflict_coverage",
    } or any(r.verdict != "pass" for r in global_checks):
        raise ValueError(
            "publication requires passing structural and conflict/coverage checks"
        )
    states = {a.claim_id: a.state for a in knowledge.assessment_links}
    contested = {
        c for conflict in knowledge.context.conflicts for c in conflict.claim_ids
    }
    for artifact in admitted.manifest.artifacts:
        for statement in artifact.statements:
            expected_evidence: set[str] = set()
            for claim_id in statement.claim_ids:
                checks = [
                    r
                    for r in knowledge.verifications
                    if inputs[r.input_id].subject_id == claim_id
                ]
                assessments = [
                    a
                    for a in knowledge.assessments
                    if inputs[a.input_id].subject_id == claim_id
                ]
                if (
                    states.get(claim_id) != "supported"
                    or claim_id in contested
                    or not assessments
                    or any(a.outcome != "supported" for a in assessments)
                    or {inputs[r.input_id].check_type for r in checks}
                    != {"semantic_support", "freshness"}
                    or any(r.verdict != "pass" for r in checks)
                ):
                    raise ValueError(
                        "publication statement contains an ineligible claim"
                    )
                for result in checks:
                    if inputs[result.input_id].check_type == "semantic_support":
                        expected_evidence.update(inputs[result.input_id].evidence_ids)
            if set(statement.evidence_ids) != expected_evidence:
                raise ValueError(
                    "publication citations differ from checked support closure"
                )
    if any(a.verdict != "pass" for a in admitted.manifest.audits):
        raise ValueError("publication requires all render audits to pass")


async def prepare_publication(
    raw: bytes,
    *,
    scope_id: str,
    research_id: str,
    revision_id: str,
    artifact_set_id: str,
    prior: tuple[bytes, ...],
    resolver: ManifestResolver,
    source_resolver: ContextSourceResolver,
    reviewers: tuple[Reviewer, ...],
    knowledge_execution: KnowledgeExecutionLedger,
    render_execution: RenderExecutionLedger,
) -> PublicationCandidate:
    """Revalidate exact data and execution; caller still owns atomic commit fencing.

    This is an ephemeral candidate, not a durable receipt. Current/historical
    selection, retained-prefix authority and transaction liveness remain caller
    responsibilities. No endpoint or database writer consumes it yet.
    """
    admitted = await admit_render_manifest(
        raw,
        scope_id=scope_id,
        research_id=research_id,
        revision_id=revision_id,
        artifact_set_id=artifact_set_id,
        resolver=resolver,
        reviewers=render_execution.reviewers,
    )
    raw_knowledge = await resolver.resolve_revision(scope_id, research_id, revision_id)
    history = await admit_checked_history(
        raw_knowledge,
        prior=prior,
        scope_id=scope_id,
        research_id=research_id,
        revision_id=revision_id,
        resolver=source_resolver,
        reviewers=reviewers,
    )
    if history.document.digest != admitted.manifest.revision_digest:
        raise ValueError("publication revision changed during validation")
    owner = asyncio.current_task()
    if owner is not None and owner.cancelling():
        raise asyncio.CancelledError
    _check_eligibility(admitted)
    # Both checks run after the last await, so closure during I/O cannot leave a valid candidate.
    fixture_knowledge = knowledge_execution.check_bindings(admitted.knowledge)
    fixture_render = render_execution.check_bindings(admitted.manifest)
    return PublicationCandidate(admitted, fixture_knowledge or fixture_render)
