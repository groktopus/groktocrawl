"""One bounded fixture journey using consolidated knowledge and report contracts."""

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

from pydantic import TypeAdapter

from .canonical import MAX_BYTES, admit_canonical_json
from .checked_knowledge import CHECKED_SCHEMA, CheckedKnowledge, entities
from .context_sources import ResolvedContextSource, admit_knowledge_context
from .knowledge import text_digest
from .knowledge_checks import CheckAssessment, KnowledgeCheckInput, Reviewer
from .knowledge_context import CONTEXT_SCHEMA, ContentReference, KnowledgeContext
from .knowledge_execution import CheckExecutor, ExecutedCheck, KnowledgeExecutionLedger
from .manifest_outputs import ResolvedOutput
from .publication_gate import PublicationCandidate, prepare_publication
from .render_execution import RenderExecutionLedger, RenderExecutor
from .render_manifest import (
    AUDIT_SCHEMA,
    MANIFEST_SCHEMA,
    ManifestArtifact,
    OutputReference,
    RenderAuditInput,
    Renderer,
    RenderManifest,
)


@dataclass(frozen=True)
class RenderedReport:
    artifact: ManifestArtifact
    body: bytes


@dataclass(frozen=True)
class JourneyResult:
    candidate: PublicationCandidate
    knowledge_bytes: bytes
    manifest_bytes: bytes
    sources: tuple[ResolvedContextSource, ...]
    reports: tuple[RenderedReport, ...]


Acquire = Callable[[], Awaitable[ResolvedContextSource]]
Render = Callable[[CheckedKnowledge], Awaitable[tuple[RenderedReport, ...]]]


class _Material:
    """Private per-run immutable material; no shared store or external authority."""

    def __init__(self, context: KnowledgeContext) -> None:
        self.context = context
        self.sources: dict[str, ResolvedContextSource] = {}
        self.outputs: dict[str, RenderedReport] = {}
        self.knowledge = b""

    async def resolve(self, reference: ContentReference) -> ResolvedContextSource:
        source = self.sources[reference.snapshot_id]
        if source.reference != reference:
            raise ValueError("journey source reference differs")
        return source

    async def resolve_revision(
        self, scope_id: str, research_id: str, revision_id: str
    ) -> bytes:
        if (scope_id, research_id, revision_id) != (
            self.context.scope_id,
            self.context.research_id,
            self.context.revision_id,
        ):
            raise ValueError("journey revision differs")
        return self.knowledge

    async def resolve_output(self, reference: OutputReference) -> ResolvedOutput:
        report = self.outputs[reference.artifact_id]
        if report.artifact.content_ref != reference:
            raise ValueError("journey output reference differs")
        return ResolvedOutput(reference, report.body)


def _knowledge(
    context: KnowledgeContext,
    checks: tuple[KnowledgeCheckInput, ...],
    results: list[ExecutedCheck],
) -> CheckedKnowledge:
    assessments = [r for r in results if isinstance(r, CheckAssessment)]
    inputs = {i.input_id: i for i in checks}
    links = []
    for claim in context.claims:
        applicable = [
            a for a in assessments if inputs[a.input_id].subject_id == claim.claim_id
        ]
        states = {a.outcome for a in applicable}
        if len(states) > 1:
            raise ValueError(
                "fixture assessments disagree; explicit adjudication required"
            )
        links.append(
            {
                "claim_id": claim.claim_id,
                "state": next(iter(states)) if states else "unassessed",
                "assessment_ids": [a.assessment_id for a in applicable],
            }
        )
    answered = sum(q.status == "answered" for q in context.questions)
    payload = {
        "schema_version": CHECKED_SCHEMA,
        "context": context.model_dump(mode="json"),
        "verification_inputs": [i.model_dump(mode="json") for i in checks],
        "verifications": [
            r.model_dump(mode="json")
            for r in results
            if not isinstance(r, CheckAssessment)
        ],
        "assessments": [a.model_dump(mode="json") for a in assessments],
        "assessment_links": links,
        "introductions": [],
        "coverage": "complete"
        if answered == len(context.questions)
        else "partial"
        if answered
        else "insufficient",
    }

    checked = CheckedKnowledge.model_validate_json(json.dumps(payload))
    payload["introductions"] = [
        {"kind": kind, "entity_id": identity, "predecessor_id": None}
        for identity, (kind, _) in entities(checked).items()
    ]
    return CheckedKnowledge.model_validate_json(json.dumps(payload))


class ConsolidatedJourney:
    """Trusted registered callbacks, root revision only, single use, no retries.

    Callbacks may perform only their caller-authorized work. This orchestration
    provides no sandbox or task recovery; caller cancellation and deadline propagate.
    """

    _require_fixture = False

    def __init__(
        self,
        *,
        context: KnowledgeContext,
        checks: tuple[KnowledgeCheckInput, ...],
        acquisitions: Mapping[str, Acquire],
        verifier: Reviewer,
        verify: CheckExecutor,
        renderer: Renderer,
        render: Render,
        auditor: Reviewer,
        audit: RenderExecutor,
        artifact_set_id: str,
        clock: Callable[[], datetime],
        timeout_seconds: int = 30,
        verification_registrations: tuple[tuple[Reviewer, CheckExecutor], ...]
        | None = None,
        commit: Callable[
            [JourneyResult, KnowledgeExecutionLedger, RenderExecutionLedger],
            Awaitable[None],
        ]
        | None = None,
    ) -> None:
        context_document = admit_canonical_json(
            json.dumps(
                {
                    "schema_version": CONTEXT_SCHEMA,
                    "context": context.model_dump(mode="json"),
                }
            ).encode(),
            schema_version=CONTEXT_SCHEMA,
        )
        self._context = KnowledgeContext.model_validate_json(
            json.dumps(json.loads(context_document.data)["context"])
        )
        if self._context.parent_revision_id is not None:
            raise ValueError("fixture journey initially constructs root revisions only")
        if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 120:
            raise ValueError("journey deadline must be 1-120 seconds")
        if (
            not 1 <= len(context.snapshots) <= 97
            or sum(s.content_bytes for s in context.snapshots) > 100 * MAX_BYTES
        ):
            raise ValueError("journey source count or root byte budget exceeded")
        if (
            not 1 <= len(checks) <= 64
            or sum(len(i.model_dump_json().encode()) for i in checks) > MAX_BYTES
        ):
            raise ValueError("journey check budget exceeded")
        self._checks = tuple(
            KnowledgeCheckInput.model_validate_json(i.model_dump_json()) for i in checks
        )
        self._verifier: Reviewer = TypeAdapter(Reviewer).validate_json(
            verifier.model_dump_json()
        )
        supplied_registrations = verification_registrations or ((verifier, verify),)
        self._verification_registrations: tuple[tuple[Reviewer, CheckExecutor], ...] = (
            tuple(
                (
                    TypeAdapter(Reviewer).validate_json(reviewer.model_dump_json()),
                    executor,
                )
                for reviewer, executor in supplied_registrations
            )
        )
        self._reviewers = tuple(
            reviewer for reviewer, _ in self._verification_registrations
        )
        self._auditor: Reviewer = TypeAdapter(Reviewer).validate_json(
            auditor.model_dump_json()
        )
        if self._require_fixture and (
            any(reviewer.kind != "fixture" for reviewer in self._reviewers)
            or self._auditor.kind != "fixture"
        ):
            raise ValueError("fixture journey requires fixture reviewers")
        if any(
            i.context != self._context or i.reviewer not in self._reviewers
            for i in self._checks
        ):
            raise ValueError(
                "journey checks must bind exact fixture context and reviewer"
            )
        self._acquire = dict(acquisitions)
        if set(self._acquire) != {s.snapshot_id for s in context.snapshots}:
            raise ValueError("journey callbacks must match source plan")
        self._renderer, self._render, self._verify, self._audit = (
            renderer,
            render,
            verify,
            audit,
        )
        self._artifact_set_id, self._clock, self._timeout = (
            artifact_set_id,
            clock,
            timeout_seconds,
        )
        self._commit = commit
        self._started = False
        self._deadline = 0.0

    def _live(self) -> None:
        task = asyncio.current_task()
        if task is not None and task.cancelling():
            raise asyncio.CancelledError
        if asyncio.get_running_loop().time() >= self._deadline:
            raise TimeoutError("fixture journey deadline exhausted")

    async def run(self) -> JourneyResult:
        if self._started:
            raise ValueError("fixture journey is single use")
        self._started = True
        self._deadline = asyncio.get_running_loop().time() + self._timeout
        knowledge_owner = KnowledgeExecutionLedger(
            self._verification_registrations, clock=self._clock
        )
        render_owner = RenderExecutionLedger(
            ((self._auditor, self._audit),), clock=self._clock
        )
        try:
            async with asyncio.timeout(self._timeout):
                return await self._run(knowledge_owner, render_owner)
        finally:
            knowledge_owner.close()
            render_owner.close()

    async def _run(
        self,
        knowledge_owner: KnowledgeExecutionLedger,
        render_owner: RenderExecutionLedger,
    ) -> JourneyResult:

        context = self._context
        material = _Material(context)
        for snapshot in context.snapshots:
            source = await asyncio.ensure_future(self._acquire[snapshot.snapshot_id]())
            self._live()
            if (
                source.reference != snapshot.content_ref
                or not isinstance(source.body, bytes)
                or len(source.body) != snapshot.content_bytes
            ):
                raise ValueError("acquisition differs from planned source")
            material.sources[snapshot.snapshot_id] = source
        target = {
            "scope_id": context.scope_id,
            "research_id": context.research_id,
            "revision_id": context.revision_id,
        }
        await admit_knowledge_context(
            json.dumps(
                {
                    "schema_version": CONTEXT_SCHEMA,
                    "context": context.model_dump(mode="json"),
                }
            ).encode(),
            **target,
            resolver=material,
        )
        self._live()
        results = []
        for checked in self._checks:
            results.append(await knowledge_owner.execute(checked))
            self._live()
        knowledge = _knowledge(context, self._checks, results)
        document = admit_canonical_json(
            knowledge.model_dump_json().encode(), schema_version=CHECKED_SCHEMA
        )
        material.knowledge = document.data
        reports = await asyncio.ensure_future(self._render(knowledge))
        self._live()
        if not isinstance(reports, tuple) or len(reports) != 3:
            raise ValueError("renderer must return exactly three reports")
        for report in reports:
            artifact = ManifestArtifact.model_validate_json(
                report.artifact.model_dump_json()
            )
            if (
                not isinstance(report.body, bytes)
                or len(report.body) != artifact.content_bytes
                or text_digest(report.body.decode("utf-8")) != artifact.content_digest
            ):
                raise ValueError("rendered bytes differ from descriptor")
            if artifact.artifact_id in material.outputs:
                raise ValueError("renderer repeated an artifact identity")
            material.outputs[artifact.artifact_id] = RenderedReport(
                artifact, report.body
            )
        core = {
            "schema_version": MANIFEST_SCHEMA,
            **target,
            "artifact_set_id": self._artifact_set_id,
            "revision_digest": document.digest,
            "created_at": self._clock()
            .isoformat(timespec="microseconds")
            .replace("+00:00", "Z"),
            "renderer": self._renderer.model_dump(mode="json"),
            "coverage": knowledge.coverage,
            "artifacts": [
                r.artifact.model_dump(mode="json") for r in material.outputs.values()
            ],
        }
        checked_audit = RenderAuditInput.model_validate_json(
            json.dumps(
                {
                    "schema_version": AUDIT_SCHEMA,
                    "input_id": str(uuid4()),
                    "reviewer": self._auditor.model_dump(mode="json"),
                    "manifest_core": core,
                }
            )
        )
        publication_target = {**target, "artifact_set_id": self._artifact_set_id}
        audit = await render_owner.execute(
            checked_audit, **publication_target, resolver=material
        )
        self._live()
        manifest = RenderManifest.model_validate_json(
            json.dumps(
                {
                    **core,
                    "audit_inputs": [checked_audit.model_dump(mode="json")],
                    "audits": [audit.model_dump(mode="json")],
                }
            )
        )
        retained_bytes = (
            sum(len(s.body) for s in material.sources.values())
            + len(document.data)
            + sum(len(r.body) for r in material.outputs.values())
        )
        if retained_bytes + len(manifest.model_dump_json().encode()) > 100 * MAX_BYTES:
            raise ValueError("journey material exceeds root byte budget")
        manifest_bytes = admit_canonical_json(
            manifest.model_dump_json().encode(), schema_version=MANIFEST_SCHEMA
        ).data
        candidate = await prepare_publication(
            manifest_bytes,
            **publication_target,
            prior=(),
            resolver=material,
            source_resolver=material,
            reviewers=self._reviewers,
            knowledge_execution=knowledge_owner,
            render_execution=render_owner,
        )
        self._live()
        if self._require_fixture and not candidate.fixture_only:
            raise ValueError("fixture journey cannot promote reviewer provenance")
        result = JourneyResult(
            candidate,
            document.data,
            manifest_bytes,
            tuple(material.sources.values()),
            tuple(material.outputs.values()),
        )

        if self._commit is not None:
            await self._commit(result, knowledge_owner, render_owner)
            self._live()
        return result


class ConsolidatedFixtureJourney(ConsolidatedJourney):
    """Compatibility entry point: live model/tool reviewers remain forbidden."""

    _require_fixture = True
