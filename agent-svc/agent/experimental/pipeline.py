"""Local acquisition-to-artifact fixtures, not an autonomous semantic researcher."""

from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime
from typing import Literal, Self

from pydantic import Field, model_validator

from .controller import (
    ControllerLimits,
    ControllerResult,
    OperationSpec,
    ResearchTarget,
    ScriptedController,
    ScriptResult,
    ScriptStep,
)
from .execution import Budget
from .knowledge import (
    Claim,
    Digest,
    Evidence,
    Identity,
    KnowledgeStructure,
    Offset,
    Record,
    Relationship,
    Snapshot,
    SourceDate,
    Text,
    text_digest,
)
from .publication import (
    Conflict,
    FixtureArtifact,
    FixturePublication,
    FixtureRenderAudit,
    FixtureResearch,
    RenderInput,
    fixture_json,
)
from .verification import (
    AssessmentLink,
    FixtureAssessment,
    FixtureVerification,
    FixtureVerificationSet,
    FixtureVerifier,
    FreshnessContext,
    VerificationInput,
)


class SourceSpec(Record):
    # SHA-256 of UTF-8 text after fixture-newlines/1 normalization, when pinned.
    expected_digest: Digest | None = None
    snapshot_id: Identity
    canonical_url: str = Field(pattern=r"^https?://[^\s/]+(?:/[^\s]*)?$")


class AcquiredText(Record):
    text: Text
    retrieved_at: datetime
    media_type: Literal["text/plain", "text/markdown"] = "text/markdown"
    published_at: SourceDate | None = None
    effective_at: SourceDate | None = None
    origin_id: Identity | None = None
    lineage_id: Identity | None = None


class EvidenceAnnotation(Record):
    evidence_id: Identity
    snapshot_id: Identity
    start: Offset
    end: Offset
    quote: Text


class VerificationRecipe(Record):
    verification_id: Identity
    subject_id: Identity
    check_type: Literal["semantic_support", "freshness", "conflict_coverage"]
    evidence_ids: tuple[Identity, ...] = Field(max_length=1000)
    freshness: FreshnessContext | None = None
    verdict: Literal["pass", "fail", "indeterminate"]
    reason: Text


class AssessmentRecipe(Record):
    assessment_id: Identity
    subject_id: Identity
    evidence_ids: tuple[Identity, ...] = Field(max_length=1000)
    outcome: Literal["supported", "contested", "insufficient", "refuted"]
    reason: Text


class RenderRecipe(Record):
    audit_id: Identity
    artifact: FixtureArtifact
    verdict: Literal["pass", "fail", "indeterminate"]
    reason: Text


class FixturePlan(Record):
    schema_version: Literal["fixture-journey-plan/1"]
    target: ResearchTarget
    sources: tuple[SourceSpec, ...] = Field(min_length=1, max_length=97)
    evidence: tuple[EvidenceAnnotation, ...] = Field(max_length=1000)
    claims: tuple[Claim, ...] = Field(max_length=1000)
    relationships: tuple[Relationship, ...] = Field(max_length=2000)
    conflicts: tuple[Conflict, ...] = Field(max_length=100)
    verifications: tuple[VerificationRecipe, ...] = Field(max_length=3000)
    assessments: tuple[AssessmentRecipe, ...] = Field(max_length=3000)
    assessment_links: tuple[AssessmentLink, ...] = Field(max_length=1000)
    renders: tuple[RenderRecipe, ...] = Field(min_length=3, max_length=3)
    verifier: FixtureVerifier
    evaluated_at: datetime
    artifact_set_id: Identity
    renderer_version: Identity
    budget: Budget
    limits: ControllerLimits

    @model_validator(mode="after")
    def finite_identities(self) -> Self:
        identities = [
            *(s.snapshot_id for s in self.sources),
            *(e.evidence_id for e in self.evidence),
            *(c.claim_id for c in self.claims),
            *(r.relationship_id for r in self.relationships),
            *(q.question_id for q in self.target.questions),
            *(c.conflict_id for c in self.conflicts),
            *(v.verification_id for v in self.verifications),
            *(a.assessment_id for a in self.assessments),
            *(r.audit_id for r in self.renders),
            *(r.artifact.artifact_id for r in self.renders),
        ]
        if len(set(identities)) != len(identities):
            raise ValueError("fixture entity identities must be unique")
        if {r.artifact.layer for r in self.renders} != {
            "summary",
            "analysis",
            "dossier",
        }:
            raise ValueError("fixture must declare all three render layers")
        for time in (self.target.as_of, self.evaluated_at):
            offset = time.utcoffset()
            if offset is None or offset.total_seconds() != 0:
                raise ValueError("fixture times must have explicit UTC offsets")
        if self.evaluated_at < self.target.as_of:
            raise ValueError("fixture evaluation cannot precede as-of")
        return self


def _construct(
    plan: FixturePlan, snapshots: Mapping[str, Snapshot]
) -> KnowledgeStructure:
    return KnowledgeStructure(
        schema_version="knowledge-structure-prototype/1",
        scope_id=plan.target.scope_id,
        research_id=plan.target.research_id,
        revision_id=plan.target.revision_id,
        as_of=plan.target.as_of,
        snapshots=tuple(snapshots[s.snapshot_id] for s in plan.sources),
        evidence=tuple(
            Evidence(**e.model_dump(), quote_digest=text_digest(e.quote))
            for e in plan.evidence
        ),
        claims=plan.claims,
        relationships=plan.relationships,
    )


def _verify(plan: FixturePlan, structure: KnowledgeStructure) -> FixtureResearch:
    records = []
    for recipe in plan.verifications:
        context = VerificationInput(
            schema_version="fixture-verification-input/1",
            structure=structure,
            subject_id=recipe.subject_id,
            check_type=recipe.check_type,
            policy_version=plan.target.policy_version,
            verifier=plan.verifier,
            evidence_ids=recipe.evidence_ids,
            freshness=recipe.freshness,
        )
        records.append(
            FixtureVerification(
                verification_id=recipe.verification_id,
                checked_input=context,
                checked_input_digest=context.input_digest(),
                verdict=recipe.verdict,
                checked_at=plan.evaluated_at,
                reason=recipe.reason,
            )
        )
    assessments = []
    for assessment in plan.assessments:
        context = VerificationInput(
            schema_version="fixture-verification-input/1",
            structure=structure,
            subject_id=assessment.subject_id,
            check_type="assessment",
            policy_version=plan.target.policy_version,
            verifier=plan.verifier,
            evidence_ids=assessment.evidence_ids,
        )
        assessments.append(
            FixtureAssessment(
                assessment_id=assessment.assessment_id,
                checked_input=context,
                checked_input_digest=context.input_digest(),
                outcome=assessment.outcome,
                checked_at=plan.evaluated_at,
                reason=assessment.reason,
            )
        )
    return FixtureResearch(
        objective=plan.target.objective,
        verifications=FixtureVerificationSet(
            schema_version="fixture-verifications/1",
            structure=structure,
            policy_version=plan.target.policy_version,
            verifier=plan.verifier,
            records=tuple(records),
            assessments=tuple(assessments),
            assessment_links=plan.assessment_links,
        ),
        questions=plan.target.questions,
        conflicts=plan.conflicts,
    )


def _render(plan: FixturePlan, research: FixtureResearch) -> FixturePublication:
    audits = []
    for recipe in plan.renders:
        context = RenderInput(
            schema_version="fixture-render-input/1",
            research=research,
            artifact_set_id=plan.artifact_set_id,
            renderer_version=plan.renderer_version,
            artifact=recipe.artifact,
            auditor=plan.verifier,
        )
        audits.append(
            FixtureRenderAudit(
                audit_id=recipe.audit_id,
                checked_input=context,
                checked_input_digest=context.input_digest(),
                verdict=recipe.verdict,
                checked_output_digest=text_digest(context.rendered_text()),
                checked_at=plan.evaluated_at,
                reason=recipe.reason,
            )
        )
    return FixturePublication(
        schema_version="fixture-publication/1", audits=tuple(audits)
    )


class FixtureJourney:
    """Trusted injected local callbacks; no network, retry or second task owner.

    Text is normalized once with fixture-newlines/1 before hashing/locating spans.
    Fixture recipes provide judgments explicitly; generating their bound receipts
    does not evaluate semantic truth or authenticate reviewer approval.
    """

    def __init__(
        self,
        *,
        run_id: str,
        plan: FixturePlan,
        acquisitions: Mapping[str, Callable[[], Awaitable[AcquiredText]]],
    ) -> None:
        plan = FixturePlan.model_validate(plan)
        callbacks = dict(acquisitions)
        if set(callbacks) != {s.snapshot_id for s in plan.sources}:
            raise ValueError("acquisition callbacks must match planned sources exactly")
        snapshots: dict[str, Snapshot] = {}
        plan_digest = text_digest("fixture-journey-plan/1\0" + fixture_json(plan))

        def step(
            index: int,
            execute: Callable[[], Awaitable[ScriptResult]],
            reservation: Budget,
        ) -> ScriptStep:
            return ScriptStep(
                OperationSpec(
                    operation_id=f"step-{index}",
                    output_id=f"result-{index}",
                    input_digest=text_digest(f"{plan_digest}:{index}"),
                    reservation=reservation,
                ),
                execute,
            )

        def acquire(
            index: int, spec: SourceSpec
        ) -> Callable[[], Awaitable[ScriptResult]]:
            async def run() -> ScriptResult:
                response = AcquiredText.model_validate(
                    await callbacks[spec.snapshot_id]()
                )
                normalized = response.text.replace("\r\n", "\n").replace("\r", "\n")
                digest = text_digest(normalized)
                if spec.expected_digest is not None and digest != spec.expected_digest:
                    raise ValueError("acquired snapshot differs from pinned digest")
                snapshots[spec.snapshot_id] = Snapshot(
                    **response.model_dump(exclude={"text"}),
                    text=normalized,
                    snapshot_id=spec.snapshot_id,
                    canonical_url=spec.canonical_url,
                    normalization_version="fixture-newlines/1",
                    digest=digest,
                )
                return ScriptResult(
                    output_id=f"result-{index}", actual=Budget(sources=1)
                )

            return run

        count = len(plan.sources)

        async def construct() -> ScriptResult:
            return ScriptResult(
                output_id=f"result-{count}",
                actual=Budget(),
                structure=_construct(plan, snapshots),
            )

        async def verify() -> ScriptResult:
            structure = self._controller.structure
            if structure is None:
                raise ValueError("construction has not committed")
            return ScriptResult(
                output_id=f"result-{count + 1}",
                actual=Budget(),
                research=_verify(plan, structure),
            )

        async def render() -> ScriptResult:
            research = self._controller.research
            if research is None:
                raise ValueError("verification has not committed")
            return ScriptResult(
                output_id=f"result-{count + 2}",
                actual=Budget(),
                publication=_render(plan, research),
            )

        steps = [
            step(i, acquire(i, s), Budget(sources=1))
            for i, s in enumerate(plan.sources)
        ]
        steps.extend(
            [
                step(count, construct, Budget()),
                step(count + 1, verify, Budget()),
                step(count + 2, render, Budget()),
            ]
        )
        self._controller = ScriptedController(
            run_id=run_id,
            steps=tuple(steps),
            budget=plan.budget,
            limits=plan.limits,
            research=plan.target,
            artifact_set_id=plan.artifact_set_id,
            renderer_version=plan.renderer_version,
            auditor=plan.verifier,
        )

    @property
    def result(self) -> ControllerResult | None:
        return self._controller.result

    def cancel(self) -> None:
        self._controller.cancel()

    async def run(self) -> ControllerResult:
        return await self._controller.run()
