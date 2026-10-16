"""Complete supplied-history consistency with exact sources; not publication authority."""

import json
from dataclasses import dataclass
from typing import Literal, Self

from pydantic import Field, model_validator

from .canonical import CanonicalDocument, admit_canonical_json
from .context_sources import ContextSourceResolver, admit_knowledge_context
from .knowledge import Identity
from .knowledge_checks import (
    CheckAssessment,
    CheckResult,
    ClaimAssessmentLink,
    KnowledgeCheckInput,
    Reviewer,
)
from .knowledge_context import (
    CONTEXT_SCHEMA,
    KnowledgeContext,
    StrictRecord,
    moment,
)

CHECKED_SCHEMA = "checked-knowledge-prototype/1"
EntityKind = Literal[
    "snapshot",
    "evidence",
    "claim",
    "relationship",
    "question",
    "conflict",
    "input",
    "verification",
    "assessment",
]


class KnowledgeIntroduction(StrictRecord):
    kind: EntityKind
    entity_id: Identity
    predecessor_id: Identity | None


class CheckedKnowledge(StrictRecord):
    schema_version: Literal["checked-knowledge-prototype/1"]
    context: KnowledgeContext
    verification_inputs: tuple[KnowledgeCheckInput, ...] = Field(max_length=6000)
    verifications: tuple[CheckResult, ...] = Field(max_length=3000)
    assessments: tuple[CheckAssessment, ...] = Field(max_length=3000)
    assessment_links: tuple[ClaimAssessmentLink, ...] = Field(max_length=1000)
    introductions: tuple[KnowledgeIntroduction, ...] = Field(max_length=10_000)
    coverage: Literal["complete", "partial", "insufficient"]

    @model_validator(mode="after")
    def check_bindings(self) -> Self:
        inputs = {i.input_id: i for i in self.verification_inputs}
        entities(self)
        for check_input in self.verification_inputs:
            if check_input.context != self.context:
                raise ValueError("check input differs from full frozen context")
        used: set[str] = set()
        results: tuple[CheckResult | CheckAssessment, ...] = (
            *self.verifications,
            *self.assessments,
        )
        for result in results:
            checked = inputs.get(result.input_id)
            if checked is None or result.input_digest != checked.input_digest():
                raise ValueError("result differs from exact check input")
            if isinstance(result, CheckAssessment) != (
                checked.check_type == "assessment"
            ):
                raise ValueError("result kind differs from check type")
            if result.input_id in used:
                raise ValueError("one immutable result per check input")
            used.add(result.input_id)
            if moment(result.checked_at) < moment(self.context.created_at):
                raise ValueError("result predates frozen context")
            if checked.freshness is not None and moment(result.checked_at) < moment(
                checked.freshness.evaluated_at
            ):
                raise ValueError("result predates freshness evaluation")
            if (
                isinstance(result, CheckResult)
                and result.verdict == "pass"
                and not checked.freshness_allows_pass()
            ):
                raise ValueError("freshness basis cannot authorize a pass")
        if used != set(inputs):
            raise ValueError("every retained check input requires a result")
        self._assessment_mapping(inputs)
        answered = sum(q.status == "answered" for q in self.context.questions)
        expected = (
            "complete"
            if answered == len(self.context.questions)
            else "partial"
            if answered
            else "insufficient"
        )
        if self.coverage != expected:
            raise ValueError("coverage differs from required question outcomes")
        return self

    def _assessment_mapping(self, inputs: dict[str, KnowledgeCheckInput]) -> None:
        claim_ids = {c.claim_id for c in self.context.claims}
        if (
            len(self.assessment_links) != len(claim_ids)
            or {a.claim_id for a in self.assessment_links} != claim_ids
        ):
            raise ValueError("exactly one assessment mapping per claim is required")
        assessments = {a.assessment_id: a for a in self.assessments}
        for link in self.assessment_links:
            if len(set(link.assessment_ids)) != len(link.assessment_ids):
                raise ValueError("assessment references must be distinct")
            if (link.state == "unassessed") != (not link.assessment_ids):
                raise ValueError("assessment state and references disagree")
            for identity in link.assessment_ids:
                record = assessments.get(identity)
                if (
                    record is None
                    or record.outcome != link.state
                    or inputs[record.input_id].subject_id != link.claim_id
                ):
                    raise ValueError("assessment mapping differs from scoped result")


def entities(value: CheckedKnowledge) -> dict[str, tuple[EntityKind, StrictRecord]]:
    groups: tuple[tuple[EntityKind, str, tuple[StrictRecord, ...]], ...] = (
        ("snapshot", "snapshot_id", value.context.snapshots),
        ("evidence", "evidence_id", value.context.evidence),
        ("claim", "claim_id", value.context.claims),
        ("relationship", "relationship_id", value.context.relationships),
        ("question", "question_id", value.context.questions),
        ("conflict", "conflict_id", value.context.conflicts),
        ("input", "input_id", value.verification_inputs),
        ("verification", "verification_id", value.verifications),
        ("assessment", "assessment_id", value.assessments),
    )
    result: dict[str, tuple[EntityKind, StrictRecord]] = {}
    for kind, field, records in groups:
        for record in records:
            identity = getattr(record, field)
            if identity in result or identity == value.context.revision_id:
                raise ValueError("knowledge entity identities must not alias")
            result[identity] = (kind, record)
    return result


@dataclass(frozen=True)
class AdmittedKnowledge:
    document: CanonicalDocument
    knowledge: CheckedKnowledge


def _read(raw: bytes) -> AdmittedKnowledge:
    document = admit_canonical_json(raw, schema_version=CHECKED_SCHEMA)
    return AdmittedKnowledge(
        document, CheckedKnowledge.model_validate_json(document.data)
    )


async def admit_checked_history(
    raw: bytes,
    *,
    prior: tuple[bytes, ...],
    scope_id: str,
    research_id: str,
    revision_id: str,
    resolver: ContextSourceResolver,
    reviewers: tuple[Reviewer, ...],
) -> AdmittedKnowledge:
    """Caller supplies trusted prefix and reviewer catalogue, not an execution proof.

    All retained contexts resolve again. No database current-parent/commit authority
    or positive semantic/publication eligibility is established by this function.
    """
    if not isinstance(prior, tuple) or len(prior) >= 20:
        raise ValueError("history requires at most nineteen prior revisions")
    chain = tuple(_read(value) for value in (*prior, raw))
    known: dict[str, tuple[EntityKind, StrictRecord]] = {}
    revisions: set[str] = set()
    previous: AdmittedKnowledge | None = None
    for item in chain:
        value = item.knowledge
        context = value.context
        if (context.scope_id, context.research_id) != (scope_id, research_id):
            raise ValueError("history crosses caller scope or research identity")
        if context.revision_id in revisions or context.revision_id in known:
            raise ValueError("revision identity was reused")
        if (context.parent_revision_id, context.parent_digest) != (
            previous.knowledge.context.revision_id if previous else None,
            previous.document.digest if previous else None,
        ):
            raise ValueError("history differs from exact parent identity and digest")
        if previous:
            old = previous.knowledge
            times = [moment(old.context.created_at)] + [
                moment(r.checked_at) for r in old.verifications
            ]
            times.extend(moment(a.checked_at) for a in old.assessments)
            if moment(context.created_at) < max(times):
                raise ValueError("successor predates completed parent knowledge")
        current = entities(value)
        if set(current) & revisions:
            raise ValueError("entity aliases an earlier revision")
        declarations = {d.entity_id: d for d in value.introductions}
        if len(declarations) != len(value.introductions) or set(declarations) != set(
            current
        ) - set(known):
            raise ValueError("declare each new identity exactly once")
        for identity, entity in current.items():
            if identity in known and known[identity] != entity:
                raise ValueError("previous identity was reassigned")
        for identity, declaration in declarations.items():
            if declaration.kind != current[identity][0]:
                raise ValueError("introduction kind differs from entity")
            if declaration.predecessor_id is not None:
                predecessor = known.get(declaration.predecessor_id)
                if predecessor is None or predecessor[0] != declaration.kind:
                    raise ValueError("predecessor must be an earlier same-kind entity")
        if any(i.reviewer not in reviewers for i in value.verification_inputs):
            raise ValueError("reviewer differs from configured catalogue")
        known.update(current)
        revisions.add(context.revision_id)
        previous = item
    if chain[-1].knowledge.context.revision_id != revision_id:
        raise ValueError("candidate differs from caller revision identity")
    # Complete structural/history rejection happens before accessing any sources.
    for item in chain:
        context = item.knowledge.context
        await admit_knowledge_context(
            json.dumps(
                {
                    "schema_version": CONTEXT_SCHEMA,
                    "context": context.model_dump(mode="json"),
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode(),
            scope_id=scope_id,
            research_id=research_id,
            revision_id=context.revision_id,
            resolver=resolver,
        )
    return chain[-1]
