"""Strict manifest/input consistency; passing payloads do not grant publication."""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from .canonical import MAX_BYTES, admit_canonical_json
from .knowledge import Digest, Identity, Text
from .knowledge_checks import Reviewer
from .knowledge_context import Count, StrictRecord, Timestamp, moment

MANIFEST_SCHEMA = "render-manifest-prototype/1"
AUDIT_SCHEMA = "render-audit-input-prototype/1"


class Renderer(StrictRecord):
    identity: Identity
    version: Identity
    configuration_digest: Digest


class OutputReference(StrictRecord):
    scope_id: Identity
    research_id: Identity
    artifact_set_id: Identity
    artifact_id: Identity


class StatementMapping(StrictRecord):
    start: Count
    end: Count
    text: Text
    claim_ids: tuple[Identity, ...] = Field(min_length=1, max_length=100)
    evidence_ids: tuple[Identity, ...] = Field(max_length=1000)

    @model_validator(mode="after")
    def span_and_ids(self) -> Self:
        if self.end - self.start != len(self.text):
            raise ValueError("statement span must match its code-point length")
        if len(set(self.claim_ids)) != len(self.claim_ids) or len(
            set(self.evidence_ids)
        ) != len(self.evidence_ids):
            raise ValueError("statement references must be distinct")
        return self


class ManifestArtifact(StrictRecord):
    artifact_id: Identity
    layer: Literal["summary", "analysis", "dossier"]
    content_ref: OutputReference
    content_digest: Digest
    content_bytes: Annotated[int, Field(ge=1, le=MAX_BYTES)]
    statements: tuple[StatementMapping, ...] = Field(min_length=1, max_length=100)
    question_ids: tuple[Identity, ...] = Field(min_length=1, max_length=100)
    conflict_ids: tuple[Identity, ...] = Field(max_length=100)

    @model_validator(mode="after")
    def distinct_mappings(self) -> Self:
        previous_end = 0
        for statement in self.statements:
            if statement.start < previous_end:
                raise ValueError(
                    "statement mappings must be ordered and nonoverlapping"
                )
            previous_end = statement.end
        if len(set(self.question_ids)) != len(self.question_ids) or len(
            set(self.conflict_ids)
        ) != len(self.conflict_ids):
            raise ValueError("artifact references must be distinct")
        return self


class ManifestCore(StrictRecord):
    schema_version: Literal["render-manifest-prototype/1"]
    scope_id: Identity
    research_id: Identity
    artifact_set_id: Identity
    revision_id: Identity
    revision_digest: Digest
    created_at: Timestamp
    renderer: Renderer
    coverage: Literal["complete", "partial", "insufficient"]
    artifacts: tuple[ManifestArtifact, ...] = Field(min_length=3, max_length=3)

    @model_validator(mode="after")
    def complete_set(self) -> Self:
        if {a.layer for a in self.artifacts} != {"summary", "analysis", "dossier"}:
            raise ValueError("manifest requires exactly three different layers")
        identities = [self.artifact_set_id, self.revision_id]
        for artifact in self.artifacts:
            identities.append(artifact.artifact_id)
            reference = artifact.content_ref
            if (
                reference.scope_id,
                reference.research_id,
                reference.artifact_set_id,
                reference.artifact_id,
            ) != (
                self.scope_id,
                self.research_id,
                self.artifact_set_id,
                artifact.artifact_id,
            ):
                raise ValueError("output reference differs from manifest identity")
        if len(set(identities)) != len(identities):
            raise ValueError("manifest identities must be distinct")
        return self


class RenderAuditInput(StrictRecord):
    schema_version: Literal["render-audit-input-prototype/1"]
    input_id: Identity
    reviewer: Reviewer
    manifest_core: ManifestCore

    def input_digest(self) -> str:
        return admit_canonical_json(
            self.model_dump_json().encode(), schema_version=AUDIT_SCHEMA
        ).digest


class RenderAudit(StrictRecord):
    audit_id: Identity
    input_id: Identity
    input_digest: Digest
    verdict: Literal["pass", "fail", "indeterminate"]
    checked_at: Timestamp
    reason: Text


class RenderManifest(ManifestCore):
    audit_inputs: tuple[RenderAuditInput, ...] = Field(min_length=1, max_length=32)
    audits: tuple[RenderAudit, ...] = Field(min_length=1, max_length=32)

    def core(self) -> ManifestCore:
        return ManifestCore.model_validate_json(
            self.model_dump_json(exclude={"audit_inputs", "audits"})
        )

    @model_validator(mode="after")
    def bind_audits(self) -> Self:
        core = self.core()
        identities = [self.artifact_set_id, self.revision_id]
        identities.extend(a.artifact_id for a in self.artifacts)
        identities.extend(i.input_id for i in self.audit_inputs)
        identities.extend(a.audit_id for a in self.audits)
        if len(set(identities)) != len(identities):
            raise ValueError("audit identities must be distinct from manifest entities")
        inputs = {i.input_id: i for i in self.audit_inputs}
        used: set[str] = set()
        for declared in self.audit_inputs:
            if declared.manifest_core != core:
                raise ValueError("audit input differs from exact manifest core")
        for audit in self.audits:
            checked = inputs.get(audit.input_id)
            if checked is None or audit.input_digest != checked.input_digest():
                raise ValueError("audit differs from exact input digest")
            if audit.input_id in used:
                raise ValueError("one audit result per input")
            if moment(audit.checked_at) < moment(self.created_at):
                raise ValueError("audit predates rendered outputs")
            used.add(audit.input_id)
        if used != set(inputs):
            raise ValueError("every audit input requires a result")
        return self
