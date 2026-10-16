"""Synthetic enterprise software-factory example; no real research findings."""

import json
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime

from .checked_knowledge import CheckedKnowledge
from .consolidated_journey import (
    Acquire,
    ConsolidatedFixtureJourney,
    JourneyResult,
    Render,
    RenderedReport,
)
from .context_sources import ResolvedContextSource
from .knowledge import text_digest
from .knowledge_checks import FixtureReviewer, KnowledgeCheckInput
from .knowledge_context import ContentReference, KnowledgeContext
from .knowledge_execution import (
    CheckExecutor,
    ExecutionDecision,
    KnowledgeExecutionLedger,
)
from .render_execution import RenderExecutionLedger, RenderExecutor, RenderInspection
from .render_manifest import ManifestArtifact, Renderer

BODIES = (
    "FICTIONAL PILOT NOTE: One team reduced median lead time from eight to five days during an agent-assisted pilot. The note does not establish causation.",
    "FICTIONAL GOVERNANCE NOTE: Security review still requires manual approval. No organization-wide productivity measurement is available.",
)
CLAIMS = (
    "A fictional team reports shorter lead time during its pilot; causation is unproven.",
    "Enterprise-wide productivity improvement remains unestablished, and security approval remains manual.",
)
REVIEWER = FixtureReviewer(
    kind="fixture", identity="enterprise-factory-example", version="1"
)
RENDERER = Renderer(
    identity="enterprise-factory-example",
    version="1",
    configuration_digest=text_digest("fixture renderer/1"),
)


def example_context(
    *,
    scope_id: str = "fixture",
    research_id: str = "enterprise-factory",
    objective: str = "Explore delivery and governance evidence for an enterprise agentic software factory using fictional notes.",
) -> KnowledgeContext:
    snapshots, evidence = [], []
    for index, body in enumerate(BODIES, 1):
        identity = f"source-{index}"
        snapshots.append(
            {
                "snapshot_id": identity,
                "canonical_url": f"https://example.test/fictional-factory/{index}",
                "retrieved_at": "2026-09-05T00:00:00Z",
                "normalization_version": "utf8-exact/1",
                "media_type": "text/plain",
                "content_ref": {
                    "scope_id": scope_id,
                    "research_id": research_id,
                    "snapshot_id": identity,
                },
                "content_digest": text_digest(body),
                "content_bytes": len(body.encode()),
                "published_at": None,
                "effective_at": None,
                "origin_id": None,
                "lineage_id": None,
            }
        )
        evidence.append(
            {
                "evidence_id": f"evidence-{index}",
                "snapshot_id": identity,
                "start": 0,
                "end": len(body),
                "quote": body,
                "quote_digest": text_digest(body),
            }
        )
    relationships: list[dict[str, object]] = [
        {
            "relationship_id": f"edge-{index}",
            "kind": "supports",
            "source_id": source,
            "target_id": claim,
            "rationale": "Authored fixture relationship",
            "rule": None,
            "assumptions": [],
        }
        for index, (source, claim) in enumerate(
            (
                ("evidence-1", "claim-1"),
                ("evidence-1", "claim-2"),
                ("evidence-2", "claim-2"),
            ),
            1,
        )
    ]
    return KnowledgeContext.model_validate_json(
        json.dumps(
            {
                "scope_id": scope_id,
                "research_id": research_id,
                "revision_id": "revision-1",
                "parent_revision_id": None,
                "parent_digest": None,
                "created_at": "2026-09-06T00:00:00Z",
                "objective": objective,
                "as_of": "2026-09-05T00:00:00Z",
                "policy_version": "fixture-policy/1",
                "snapshots": snapshots,
                "evidence": evidence,
                "claims": [
                    {
                        "claim_id": f"claim-{i}",
                        "text": text,
                        "kind": "source_statement" if i == 1 else "inference",
                        "qualifiers": [
                            "Fictional bounded pilot; not independent evidence"
                        ],
                        "temporal_scope": "historical",
                    }
                    for i, text in enumerate(CLAIMS, 1)
                ],
                "relationships": relationships,
                "questions": [
                    {
                        "question_id": "question-1",
                        "question": "What change did the fictional pilot report?",
                        "status": "answered",
                        "report_claim_id": "claim-1",
                    },
                    {
                        "question_id": "question-2",
                        "question": "Does this establish enterprise-wide productivity improvement?",
                        "status": "unresolved",
                        "report_claim_id": "claim-2",
                    },
                ],
                "conflicts": [],
            }
        )
    )


def example_checks(context: KnowledgeContext) -> tuple[KnowledgeCheckInput, ...]:
    checks = []
    for kind in (
        "structural",
        "conflict_coverage",
        "semantic_support",
        "freshness",
        "assessment",
    ):
        subjects = (
            [context.revision_id]
            if kind in {"structural", "conflict_coverage"}
            else [c.claim_id for c in context.claims]
        )
        for subject in subjects:
            evidence = [
                e.evidence_id
                for e in context.evidence
                if subject != "claim-1" or e.evidence_id == "evidence-1"
            ]
            sources = {
                e.snapshot_id for e in context.evidence if e.evidence_id in evidence
            }
            checks.append(
                KnowledgeCheckInput.model_validate_json(
                    json.dumps(
                        {
                            "schema_version": "knowledge-check-input-prototype/1",
                            "input_id": f"input-{kind}-{subject}",
                            "check_type": kind,
                            "subject_id": subject,
                            "policy_version": context.policy_version,
                            "reviewer": REVIEWER.model_dump(),
                            "context": context.model_dump(mode="json"),
                            "evidence_ids": evidence,
                            "freshness": {
                                "evaluated_at": context.created_at,
                                "sources": [
                                    {
                                        "snapshot_id": s.snapshot_id,
                                        "basis": "historical_snapshot",
                                        "max_age_seconds": 604800,
                                        "reason": "Fictional historical fixture",
                                    }
                                    for s in context.snapshots
                                    if s.snapshot_id in sources
                                ],
                            }
                            if kind == "freshness"
                            else None,
                        }
                    )
                )
            )
    return tuple(checks)


async def example_verifier(checked: KnowledgeCheckInput) -> ExecutionDecision:
    return ExecutionDecision(
        outcome="supported" if checked.check_type == "assessment" else "pass",
        reason="Authored fixture expectation, not a semantic-quality evaluation",
    )


async def example_renderer(
    knowledge: CheckedKnowledge, *, artifact_set_id: str = "reports-1"
) -> tuple[RenderedReport, ...]:
    reports = []
    for layer in ("summary", "analysis", "dossier"):
        text = f"# {layer.title()}\n\nEXPERIMENTAL FIXTURE — fictional sources and authored judgments.\n\nCoverage: partial. Enterprise-wide impact is unresolved.\n\n"
        statements = []
        for index, claim in enumerate(knowledge.context.claims, 1):
            start = len(text)
            text += claim.text
            statements.append(
                {
                    "start": start,
                    "end": len(text),
                    "text": claim.text,
                    "claim_ids": [claim.claim_id],
                    "evidence_ids": ["evidence-1"]
                    if index == 1
                    else ["evidence-1", "evidence-2"],
                }
            )
            text += "\n\n"
        if layer != "summary":
            text += "## Limits\n\nThe pilot note reports an association, not a causal result. Manual approval remains part of the fictional governance process.\n\n"
        if layer == "dossier":
            text += "## Fictional source records\n\n" + "\n\n".join(BODIES)
        artifact = ManifestArtifact.model_validate_json(
            json.dumps(
                {
                    "artifact_id": f"{artifact_set_id}-{layer}",
                    "layer": layer,
                    "content_ref": {
                        "scope_id": knowledge.context.scope_id,
                        "research_id": knowledge.context.research_id,
                        "artifact_set_id": artifact_set_id,
                        "artifact_id": f"{artifact_set_id}-{layer}",
                    },
                    "content_digest": text_digest(text),
                    "content_bytes": len(text.encode()),
                    "statements": statements,
                    "question_ids": [
                        q.question_id for q in knowledge.context.questions
                    ],
                    "conflict_ids": [],
                }
            )
        )
        reports.append(RenderedReport(artifact, text.encode()))
    return tuple(reports)


async def example_auditor(inspection: RenderInspection) -> ExecutionDecision:
    # Exact deterministic fixture expectation, including text outside mapped spans.
    expected = await example_renderer(
        inspection.knowledge,
        artifact_set_id=inspection.checked_input.manifest_core.artifact_set_id,
    )
    if inspection.outputs != tuple(r.body for r in expected):
        return ExecutionDecision(
            outcome="fail", reason="Fixture output differs, including caveats"
        )
    return ExecutionDecision(
        outcome="pass",
        reason="Exact authored fixture matched; no independent semantic review",
    )


def example_journey(
    *,
    verify: CheckExecutor = example_verifier,
    render: Render = example_renderer,
    audit: RenderExecutor = example_auditor,
    acquisitions: Mapping[str, Acquire] | None = None,
    timeout_seconds: int = 30,
    scope_id: str = "fixture",
    research_id: str = "enterprise-factory",
    objective: str = "Explore delivery and governance evidence for an enterprise agentic software factory using fictional notes.",
    artifact_set_id: str = "reports-1",
    commit: Callable[
        [JourneyResult, KnowledgeExecutionLedger, RenderExecutionLedger],
        Awaitable[None],
    ]
    | None = None,
) -> ConsolidatedFixtureJourney:
    context = example_context(
        scope_id=scope_id, research_id=research_id, objective=objective
    )
    callbacks = {}
    for snapshot, body in zip(context.snapshots, BODIES, strict=True):

        async def acquire(snapshot=snapshot, body=body):
            return ResolvedContextSource(
                ContentReference.model_validate(snapshot.content_ref),
                body.encode(),
                "utf8-exact/1",
                "text/plain",
            )

        callbacks[snapshot.snapshot_id] = acquire
    if render is example_renderer:

        async def render_with_identity(knowledge: CheckedKnowledge) -> tuple[RenderedReport, ...]:
            return await example_renderer(knowledge, artifact_set_id=artifact_set_id)

        render = render_with_identity

    if audit is example_auditor:

        async def audit_with_identity(inspection: RenderInspection) -> ExecutionDecision:
            expected = await example_renderer(
                inspection.knowledge,
                artifact_set_id=inspection.checked_input.manifest_core.artifact_set_id,
            )
            if inspection.outputs != tuple(r.body for r in expected):
                return ExecutionDecision(
                    outcome="fail", reason="Fixture output differs, including caveats"
                )
            return ExecutionDecision(
                outcome="pass",
                reason="Exact authored fixture matched; no independent semantic review",
            )

        audit = audit_with_identity

    return ConsolidatedFixtureJourney(
        context=context,
        checks=example_checks(context),
        acquisitions=callbacks if acquisitions is None else acquisitions,
        verifier=REVIEWER,
        verify=verify,
        renderer=RENDERER,
        render=render,
        auditor=REVIEWER,
        audit=audit,
        artifact_set_id=artifact_set_id,
        clock=lambda: datetime(2026, 9, 7, tzinfo=UTC),
        timeout_seconds=timeout_seconds,
        commit=commit,
    )
