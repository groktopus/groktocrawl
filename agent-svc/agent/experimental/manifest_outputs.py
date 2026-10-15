"""Resolve pinned knowledge and report bytes without issuing publication authority."""

import hashlib
from dataclasses import dataclass
from typing import Protocol

from .canonical import CanonicalDocument, admit_canonical_json
from .checked_knowledge import CHECKED_SCHEMA, CheckedKnowledge, entities
from .knowledge_checks import Reviewer
from .knowledge_context import moment
from .render_manifest import (
    MANIFEST_SCHEMA,
    ManifestArtifact,
    ManifestCore,
    OutputReference,
    RenderManifest,
)


@dataclass(frozen=True)
class ResolvedOutput:
    reference: OutputReference
    body: bytes


class ManifestResolver(Protocol):
    async def resolve_revision(
        self, scope_id: str, research_id: str, revision_id: str
    ) -> bytes:
        """Authorize and return exact retained knowledge bytes, or raise.

        Caller owns validated ancestry, source/lifecycle authority, current versus
        explicit historical selection and consistent reads through later commit.
        """
        ...

    async def resolve_output(self, reference: OutputReference) -> ResolvedOutput:
        """Authorize and return exact immutable UTF-8 output; never fetch a URL."""
        ...


@dataclass(frozen=True)
class AdmittedManifest:
    document: CanonicalDocument
    manifest: RenderManifest
    knowledge: CheckedKnowledge


async def admit_render_manifest(
    raw: bytes,
    *,
    scope_id: str,
    research_id: str,
    revision_id: str,
    artifact_set_id: str,
    resolver: ManifestResolver,
    reviewers: tuple[Reviewer, ...],
) -> AdmittedManifest:
    """Validate exact references/spans; not audit execution or semantic eligibility.

    Bodies resolve sequentially and are not retained in the result. Caller owns
    deadlines and lifecycle rechecks. Negative audit records remain admissible
    for inspection; a later publication gate must reject them.
    """
    document = admit_canonical_json(raw, schema_version=MANIFEST_SCHEMA)
    manifest = RenderManifest.model_validate_json(document.data)
    if (
        manifest.scope_id,
        manifest.research_id,
        manifest.revision_id,
        manifest.artifact_set_id,
    ) != (scope_id, research_id, revision_id, artifact_set_id):
        raise ValueError("manifest differs from caller identity")
    if any(i.reviewer not in reviewers for i in manifest.audit_inputs):
        raise ValueError("render reviewer is not configured")
    knowledge = await resolve_manifest_knowledge(
        manifest.core(),
        scope_id=scope_id,
        research_id=research_id,
        revision_id=revision_id,
        artifact_set_id=artifact_set_id,
        resolver=resolver,
    )
    new_ids = {manifest.artifact_set_id}
    new_ids.update(a.artifact_id for a in manifest.artifacts)
    new_ids.update(i.input_id for i in manifest.audit_inputs)
    new_ids.update(a.audit_id for a in manifest.audits)
    if new_ids & set(entities(knowledge)):
        raise ValueError("manifest identities alias pinned knowledge entities")
    for artifact in manifest.artifacts:
        await resolve_manifest_output(artifact, resolver)
    return AdmittedManifest(document, manifest, knowledge)


async def resolve_manifest_knowledge(
    manifest: ManifestCore,
    *,
    scope_id: str,
    research_id: str,
    revision_id: str,
    artifact_set_id: str,
    resolver: ManifestResolver,
) -> CheckedKnowledge:
    """Validate a bounded core and its pinned revision before audit execution."""
    core_document = admit_canonical_json(
        manifest.model_dump_json().encode(), schema_version=MANIFEST_SCHEMA
    )
    manifest = ManifestCore.model_validate_json(core_document.data)
    if (
        manifest.scope_id,
        manifest.research_id,
        manifest.revision_id,
        manifest.artifact_set_id,
    ) != (
        scope_id,
        research_id,
        revision_id,
        artifact_set_id,
    ):
        raise ValueError("manifest differs from caller identity")
    raw_revision = await resolver.resolve_revision(scope_id, research_id, revision_id)
    revision = admit_canonical_json(raw_revision, schema_version=CHECKED_SCHEMA)
    knowledge = CheckedKnowledge.model_validate_json(revision.data)
    context = knowledge.context
    if (
        context.scope_id,
        context.research_id,
        context.revision_id,
        revision.digest,
    ) != (scope_id, research_id, revision_id, manifest.revision_digest):
        raise ValueError("resolved revision differs from manifest pin")
    earliest = max(
        [moment(context.created_at)]
        + [moment(r.checked_at) for r in knowledge.verifications]
        + [moment(a.checked_at) for a in knowledge.assessments]
    )
    if moment(manifest.created_at) < earliest:
        raise ValueError("manifest predates completed knowledge checks")
    if manifest.coverage != knowledge.coverage:
        raise ValueError("manifest coverage differs from pinned knowledge")
    if {manifest.artifact_set_id, *(a.artifact_id for a in manifest.artifacts)} & set(
        entities(knowledge)
    ):
        raise ValueError("manifest identities alias pinned knowledge entities")
    claims = {c.claim_id for c in context.claims}
    evidence = {e.evidence_id for e in context.evidence}
    questions = {q.question_id for q in context.questions}
    conflicts = {c.conflict_id for c in context.conflicts}
    for artifact in manifest.artifacts:
        if (
            set(artifact.question_ids) != questions
            or set(artifact.conflict_ids) != conflicts
        ):
            raise ValueError(
                "every output must declare complete question/conflict coverage"
            )
        mapped_claims = {c for s in artifact.statements for c in s.claim_ids}
        if not {q.report_claim_id for q in context.questions} <= mapped_claims:
            raise ValueError("output omits a question reporting claim")
        for statement in artifact.statements:
            if (
                not set(statement.claim_ids) <= claims
                or not set(statement.evidence_ids) <= evidence
            ):
                raise ValueError("statement references unavailable knowledge")
    return knowledge


async def resolve_manifest_output(
    artifact: ManifestArtifact, resolver: ManifestResolver
) -> bytes:
    """Return exact bounded UTF-8 bytes after descriptor and span validation."""
    output = await resolver.resolve_output(artifact.content_ref)
    if (
        output.reference != artifact.content_ref
        or not isinstance(output.body, bytes)
        or len(output.body) != artifact.content_bytes
        or hashlib.sha256(output.body).hexdigest() != artifact.content_digest
    ):
        raise ValueError("resolved output differs from pinned descriptor")
    text = output.body.decode("utf-8", errors="strict")
    for statement in artifact.statements:
        if (
            statement.end > len(text)
            or text[statement.start : statement.end] != statement.text
        ):
            raise ValueError("statement differs from exact output span")
    return output.body
