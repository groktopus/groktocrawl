"""Executed, eligible reports ready for a caller's fenced publication transaction."""

import asyncio
from dataclasses import dataclass

from .checked_knowledge import CheckedKnowledge, admit_checked_history
from .context_sources import ContextSourceResolver
from .knowledge_checks import HistoricalValidityError, Reviewer
from .knowledge_context import moment
from .knowledge_execution import KnowledgeExecutionLedger
from .manifest_outputs import AdmittedManifest, ManifestResolver, admit_render_manifest
from .render_execution import RenderExecutionLedger


@dataclass(frozen=True)
class PublicationCandidate:
    admitted: AdmittedManifest
    fixture_only: bool


class UnverifiedResearchError(ValueError):
    """Safe explanation when a required answer cannot pass existing checks."""

    def __init__(self, *, freshness: bool) -> None:
        self.reason = (
            "source_freshness_unverified" if freshness else "answer_support_unverified"
        )
        self.message = (
            "Source freshness could not be verified, so the answer was not published. "
            "No searches or model calls were repeated."
            if freshness
            else "The answer did not pass evidence verification, so it was not published. "
            "No searches or model calls were repeated."
        )
        super().__init__(self.message)


def claim_publication_failures(knowledge: CheckedKnowledge) -> dict[str, str]:
    """Use the same eligibility decision for rendering and final publication."""
    inputs = {i.input_id: i for i in knowledge.verification_inputs}
    states = {a.claim_id: a.state for a in knowledge.assessment_links}
    contested = {
        c for conflict in knowledge.context.conflicts for c in conflict.claim_ids
    }
    failures = {}
    for claim in knowledge.context.claims:
        checks = [
            r
            for r in knowledge.verifications
            if inputs[r.input_id].subject_id == claim.claim_id
        ]
        assessments = [
            a
            for a in knowledge.assessments
            if inputs[a.input_id].subject_id == claim.claim_id
        ]
        if (
            states.get(claim.claim_id) != "supported"
            or claim.claim_id in contested
            or not assessments
            or any(a.outcome != "supported" for a in assessments)
            or {inputs[r.input_id].check_type for r in checks}
            != {"semantic_support", "freshness"}
            or any(r.verdict != "pass" for r in checks)
        ):
            failures[claim.claim_id] = (
                "source_freshness_unverified"
                if any(
                    inputs[r.input_id].check_type == "freshness" and r.verdict != "pass"
                    for r in checks
                )
                else "answer_support_unverified"
            )
    return failures


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
    failures = claim_publication_failures(knowledge)
    for artifact in admitted.manifest.artifacts:
        for statement in artifact.statements:
            expected_evidence: set[str] = set()
            for claim_id in statement.claim_ids:
                checks = [
                    r
                    for r in knowledge.verifications
                    if inputs[r.input_id].subject_id == claim_id
                ]
                if claim_id in failures:
                    if (
                        knowledge.context.as_of is not None
                        and moment(knowledge.context.as_of)
                        < moment(knowledge.context.created_at)
                        and any(
                            inputs[r.input_id].check_type == "freshness"
                            and r.verdict != "pass"
                            for r in checks
                        )
                    ):
                        raise HistoricalValidityError(
                            "publication ineligible: historical validity is not established by retained evidence"
                        )
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
