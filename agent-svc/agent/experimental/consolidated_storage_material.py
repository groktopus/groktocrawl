"""Resolve consolidated material against exact staged sources in one physical root."""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from .canonical import admit_canonical_json
from .checked_knowledge import CHECKED_SCHEMA, CheckedKnowledge
from .consolidated_journey import RenderedReport
from .context_sources import ResolvedContextSource
from .knowledge_context import CONTEXT_SCHEMA, ContentReference, KnowledgeContext
from .manifest_outputs import ResolvedOutput
from .render_manifest import OutputReference
from .source_store import Connection, SourceStore, StorageConflictError


def context_digest(context: KnowledgeContext) -> str:
    return admit_canonical_json(
        json.dumps(
            {
                "schema_version": CONTEXT_SCHEMA,
                "context": context.model_dump(mode="json"),
            }
        ).encode(),
        schema_version=CONTEXT_SCHEMA,
    ).digest


class Material(Protocol):
    @property
    def knowledge_bytes(self) -> bytes: ...
    @property
    def manifest_bytes(self) -> bytes: ...
    @property
    def sources(self) -> tuple[ResolvedContextSource, ...]: ...
    @property
    def reports(self) -> tuple[RenderedReport, ...]: ...


@dataclass(frozen=True)
class RetainedConsolidated:
    knowledge_bytes: bytes
    manifest_bytes: bytes
    sources: tuple[ResolvedContextSource, ...]
    reports: tuple[RenderedReport, ...]
    fixture_only: bool
    receipt_digest: str


class StoredMaterial:
    def __init__(
        self,
        store: SourceStore,
        scope: UUID,
        root: UUID,
        result: Material,
        bindings: Mapping[str, UUID],
        conn: Connection | None = None,
    ) -> None:
        self.store, self.scope, self.root, self.conn = store, scope, root, conn
        self.result = result
        document = admit_canonical_json(
            result.knowledge_bytes, schema_version=CHECKED_SCHEMA
        )
        self.knowledge = CheckedKnowledge.model_validate_json(document.data)
        self.context = self.knowledge.context
        self.bindings = dict(bindings)
        if set(self.bindings) != {s.snapshot_id for s in self.context.snapshots}:
            raise StorageConflictError("consolidated source bindings incomplete")
        self.sources = {s.reference.snapshot_id: s for s in result.sources}
        if set(self.sources) != set(self.bindings) or len(self.sources) != len(
            result.sources
        ):
            raise StorageConflictError("consolidated supplied sources incomplete")
        self.outputs = {r.artifact.artifact_id: r for r in result.reports}
        if len(self.outputs) != 3 or len(result.reports) != 3:
            raise StorageConflictError("consolidated reports incomplete")

    async def resolve_revision(
        self, scope_id: str, research_id: str, revision_id: str
    ) -> bytes:
        if (scope_id, research_id, revision_id) != (
            self.context.scope_id,
            self.context.research_id,
            self.context.revision_id,
        ):
            raise StorageConflictError("consolidated revision identity differs")
        return self.result.knowledge_bytes

    async def resolve_output(self, reference: OutputReference) -> ResolvedOutput:
        report = self.outputs[reference.artifact_id]
        if report.artifact.content_ref != reference:
            raise StorageConflictError("consolidated output identity differs")
        return ResolvedOutput(reference, report.body)

    async def resolve(self, reference: ContentReference) -> ResolvedContextSource:
        supplied = self.sources[reference.snapshot_id]
        if supplied.reference != reference:
            raise StorageConflictError("consolidated source identity differs")
        snapshot_id = self.bindings[reference.snapshot_id]
        source = (
            await self.store.read_source(self.scope, self.root, snapshot_id)
            if self.conn is None
            else await self.store._read_source(
                self.conn, self.scope, self.root, snapshot_id
            )
        )
        descriptor = json.loads(source.descriptor.data)
        expected = next(
            s for s in self.context.snapshots if s.snapshot_id == reference.snapshot_id
        )
        if (
            source.body != supplied.body
            or descriptor["url"] != expected.canonical_url
            or descriptor["normalization"] != expected.normalization_version
            or supplied.normalization_version != expected.normalization_version
            or supplied.media_type != expected.media_type
        ):
            raise StorageConflictError("consolidated staged source differs")
        return supplied
