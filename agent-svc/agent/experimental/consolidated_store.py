"""Atomic root-only consolidated publication in the isolated database."""

import asyncio
import base64
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from .canonical import MAX_BYTES, CanonicalDocument, admit_canonical_json
from .checked_knowledge import CHECKED_SCHEMA, CheckedKnowledge, admit_checked_history
from .consolidated_bundle import (
    CONSOLIDATED_BUNDLE_SCHEMA,
    admit_consolidated_bundle,
    build_consolidated_bundle,
)
from .consolidated_journey import JourneyResult, RenderedReport
from .consolidated_storage_material import (
    RetainedConsolidated,
    StoredMaterial,
    context_digest,
)
from .context_sources import ResolvedContextSource
from .knowledge_context import KnowledgeContext
from .knowledge_execution import KnowledgeExecutionLedger
from .manifest_outputs import admit_render_manifest
from .publication_gate import _check_eligibility, prepare_publication
from .render_execution import RenderExecutionLedger
from .render_manifest import MANIFEST_SCHEMA, RenderManifest
from .research_import_store import ResearchImportStore
from .source_store import ROOT_QUOTA, Connection, StorageConflictError


@dataclass(frozen=True)
class ImportedConsolidated:
    recipient_scope: UUID
    recipient_root: UUID
    retained_until: datetime
    bundle: CanonicalDocument


class ConsolidatedStore(ResearchImportStore):
    async def migrate_consolidated(self) -> None:
        migration = (
            Path(__file__).with_name("migrations") / "010_consolidated_journey.sql"
        )
        async with self._transaction(bootstrap=True) as conn:
            await conn.execute(
                "LOCK TABLE research_staging.schema_version IN ACCESS EXCLUSIVE MODE"
            )
            version = await (
                await conn.execute(
                    "SELECT version FROM research_staging.schema_version"
                )
            ).fetchall()
            if version != [{"version": 9}]:
                raise StorageConflictError("consolidated migration requires schema 9")
            await conn.execute(migration.read_text(), prepare=False)

    async def migrate_consolidated_model(self) -> None:
        """Opt in to schema 11, which retains an explicit provenance boolean."""
        migration = (
            Path(__file__).with_name("migrations")
            / "011_consolidated_model_publications.sql"
        )
        async with self._transaction(bootstrap=True) as conn:
            await conn.execute(
                "LOCK TABLE research_staging.schema_version IN ACCESS EXCLUSIVE MODE"
            )
            version = await (
                await conn.execute(
                    "SELECT version FROM research_staging.schema_version"
                )
            ).fetchall()
            if version != [{"version": 10}]:
                raise StorageConflictError("model publication migration requires schema 10")
            await conn.execute(migration.read_text(), prepare=False)

    async def migrate_consolidated_imports(self) -> None:
        """Opt in to trusted-server imports of consolidated bundles."""
        migration = (
            Path(__file__).with_name("migrations")
            / "012_consolidated_imports.sql"
        )
        async with self._transaction(bootstrap=True) as conn:
            await conn.execute(
                "LOCK TABLE research_staging.schema_version IN ACCESS EXCLUSIVE MODE"
            )
            version = await (
                await conn.execute(
                    "SELECT version FROM research_staging.schema_version"
                )
            ).fetchall()
            if version != [{"version": 11}]:
                raise StorageConflictError(
                    "consolidated import migration requires schema 11"
                )
            await conn.execute(migration.read_text(), prepare=False)

    @staticmethod
    async def _require_consolidated(conn: Connection) -> None:
        version = await (
            await conn.execute("SELECT version FROM research_staging.schema_version")
        ).fetchall()
        if version not in ([{"version": 10}], [{"version": 11}], [{"version": 12}]):
            raise StorageConflictError("consolidated schema unavailable")

    async def create_consolidated_root(
        self, scope: UUID, quota: int = ROOT_QUOTA
    ) -> UUID:
        if type(quota) is not int or not 0 < quota <= ROOT_QUOTA:
            raise ValueError("invalid consolidated root quota")
        async with self._transaction() as conn:
            await self._require_consolidated(conn)
            root = await self._insert_root(conn, scope, quota)
            await conn.execute(
                "UPDATE research_staging.roots SET revision_format='consolidated' WHERE scope_id=%s AND root_id=%s",
                (scope, root),
            )
            return root

    async def reserve_consolidated(
        self,
        scope: UUID,
        root: UUID,
        generation: int,
        size: int,
        context: KnowledgeContext,
    ) -> UUID:
        context = KnowledgeContext.model_validate_json(context.model_dump_json())
        if (
            context.parent_revision_id is not None
            or type(size) is not int
            or not 0 < size <= 5 * MAX_BYTES
        ):
            raise ValueError("invalid consolidated root reservation")
        digest = context_digest(context)
        async with self._transaction() as conn:
            await self._require_consolidated(conn)
            row = await self._lock(conn, scope, root)
            self._active(row, generation)
            if (
                row["revision_format"] != "consolidated"
                or row["current_consolidated"] is not None
            ):
                raise StorageConflictError("consolidated root already has a revision")
            if size > min(row["scope_free"], row["quota"] - row["charged"]):
                raise StorageConflictError("consolidated quota exhausted")
            await self._charge(conn, scope, root, size)
            operation = await (
                await conn.execute(
                    "INSERT INTO research_staging.consolidated_operations(scope_id,root_id,generation,context_digest,reserved) VALUES (%s,%s,%s,%s,%s) RETURNING operation_id",
                    (scope, root, generation, digest, size),
                )
            ).fetchone()
            await self._renew_staging(conn, scope, root)
            assert operation is not None
            return operation["operation_id"]

    async def commit_consolidated(
        self,
        scope: UUID,
        root: UUID,
        generation: int,
        operation: UUID,
        result: JourneyResult,
        bindings: Mapping[str, UUID],
        knowledge_owner: KnowledgeExecutionLedger,
        render_owner: RenderExecutionLedger,
    ) -> UUID:
        # Revalidate against physical staged sources, not the supplied candidate flag.
        material = StoredMaterial(self, scope, root, result, bindings)
        context = material.context
        candidate = await prepare_publication(
            result.manifest_bytes,
            scope_id=context.scope_id,
            research_id=context.research_id,
            revision_id=context.revision_id,
            artifact_set_id=result.candidate.admitted.manifest.artifact_set_id,
            prior=(),
            resolver=material,
            source_resolver=material,
            reviewers=tuple(
                {i.reviewer for i in material.knowledge.verification_inputs}
            ),
            knowledge_execution=knowledge_owner,
            render_execution=render_owner,
        )
        if context.parent_revision_id is not None:
            raise StorageConflictError("consolidated publications require root revisions")
        admitted = candidate.admitted
        outputs = {
            a.layer: material.outputs[a.artifact_id].body
            for a in admitted.manifest.artifacts
        }
        size = (
            len(result.knowledge_bytes)
            + len(admitted.document.data)
            + sum(len(b) for b in outputs.values())
        )
        digest = admitted.document.digest
        async with self._transaction() as conn:
            await self._require_consolidated(conn)
            row = await self._lock(conn, scope, root)
            self._active(row, generation)
            op = await (
                await conn.execute(
                    "SELECT * FROM research_staging.consolidated_operations WHERE scope_id=%s AND root_id=%s AND operation_id=%s",
                    (scope, root, operation),
                )
            ).fetchone()
            if (
                row["revision_format"] != "consolidated"
                or op is None
                or op["generation"] != generation
                or op["state"] == "cancelled"
                or op["context_digest"] != context_digest(context)
            ):
                raise StorageConflictError("consolidated operation unavailable")
            pinned = StoredMaterial(self, scope, root, result, bindings, conn)
            for snapshot in context.snapshots:
                await pinned.resolve(snapshot.content_ref)
            if op["state"] == "committed":
                if op["input_digest"] != digest:
                    raise StorageConflictError("consolidated replay changed")
                return operation
            if row["current_consolidated"] is not None or size > op["reserved"]:
                raise StorageConflictError("consolidated parent or reservation changed")
            await conn.execute(
                "INSERT INTO research_staging.consolidated_publications(scope_id,root_id,operation_id,knowledge,knowledge_digest,manifest,manifest_digest,summary,analysis,dossier,fixture_only) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    scope,
                    root,
                    operation,
                    result.knowledge_bytes,
                    admitted.manifest.revision_digest,
                    admitted.document.data,
                    digest,
                    outputs["summary"],
                    outputs["analysis"],
                    outputs["dossier"],
                    candidate.fixture_only,
                ),
            )
            for logical_id, snapshot_id in material.bindings.items():
                await conn.execute(
                    "INSERT INTO research_staging.consolidated_sources(scope_id,root_id,operation_id,logical_id,snapshot_id) VALUES (%s,%s,%s,%s,%s)",
                    (scope, root, operation, logical_id, snapshot_id),
                )
            await self._charge(conn, scope, root, size - op["reserved"])
            await conn.execute(
                "UPDATE research_staging.consolidated_operations SET state='committed',input_digest=%s WHERE scope_id=%s AND root_id=%s AND operation_id=%s",
                (digest, scope, root, operation),
            )
            await conn.execute(
                "UPDATE research_staging.roots SET current_consolidated=%s,published_at=now(),expires_at=now()+interval '30 days' WHERE scope_id=%s AND root_id=%s",
                (operation, scope, root),
            )
            task = asyncio.current_task()
            if task is not None and task.cancelling():
                raise asyncio.CancelledError
            knowledge_owner.check_bindings(admitted.knowledge)
            render_owner.check_bindings(admitted.manifest)
        return operation

    async def consolidated_receipt(
        self, scope: UUID, root: UUID, operation: UUID
    ) -> str | None:
        async with self._transaction(read=True) as conn:
            await self._require_consolidated(conn)
            row = await (
                await conn.execute(
                    "SELECT input_digest FROM research_staging.consolidated_operations WHERE scope_id=%s AND root_id=%s AND operation_id=%s AND state='committed'",
                    (scope, root, operation),
                )
            ).fetchone()
            return row["input_digest"] if row else None

    async def cancel_consolidated(
        self, scope: UUID, root: UUID, operation: UUID
    ) -> None:
        async with self._transaction() as conn:
            await self._require_consolidated(conn)
            await self._lock(conn, scope, root)
            row = await (
                await conn.execute(
                    "UPDATE research_staging.consolidated_operations SET state='cancelled' WHERE scope_id=%s AND root_id=%s AND operation_id=%s AND state='pending' RETURNING reserved",
                    (scope, root, operation),
                )
            ).fetchone()
            if row:
                await self._charge(conn, scope, root, -row["reserved"])

    async def read_consolidated(
        self, scope: UUID, root: UUID, operation: UUID
    ) -> RetainedConsolidated:
        async with self._transaction(read=True) as conn:
            await self._require_consolidated(conn)
            row = await (
                await conn.execute(
                    "SELECT p.*,o.context_digest FROM research_staging.consolidated_publications p JOIN research_staging.consolidated_operations o USING(scope_id,root_id,operation_id) JOIN research_staging.roots r USING(scope_id,root_id) WHERE p.scope_id=%s AND p.root_id=%s AND p.operation_id=%s AND NOT r.deleted AND r.expires_at>now() AND r.revision_format='consolidated' AND r.current_consolidated=p.operation_id AND o.state='committed' AND o.input_digest=p.manifest_digest",
                    (scope, root, operation),
                )
            ).fetchone()
            if row is None:
                raise StorageConflictError("consolidated publication unavailable")
            knowledge_document = admit_canonical_json(
                row["knowledge"], schema_version=CHECKED_SCHEMA
            )
            knowledge = CheckedKnowledge.model_validate_json(knowledge_document.data)
            manifest_document = admit_canonical_json(
                row["manifest"], schema_version=MANIFEST_SCHEMA
            )
            manifest = RenderManifest.model_validate_json(manifest_document.data)
            if (
                knowledge_document.data != row["knowledge"]
                or manifest_document.data != row["manifest"]
                or knowledge_document.digest != row["knowledge_digest"]
                or manifest_document.digest != row["manifest_digest"]
                or context_digest(knowledge.context) != row["context_digest"]
                or type(row["fixture_only"]) is not bool
            ):
                raise StorageConflictError("consolidated document integrity mismatch")
            links = await (
                await conn.execute(
                    "SELECT logical_id,snapshot_id FROM research_staging.consolidated_sources WHERE scope_id=%s AND root_id=%s AND operation_id=%s",
                    (scope, root, operation),
                )
            ).fetchall()
            bindings = {r["logical_id"]: r["snapshot_id"] for r in links}
            if set(bindings) != {s.snapshot_id for s in knowledge.context.snapshots}:
                raise StorageConflictError("consolidated retained sources incomplete")
            sources = []
            for snapshot in knowledge.context.snapshots:
                source = await self._read_source(
                    conn, scope, root, bindings[snapshot.snapshot_id]
                )
                sources.append(
                    ResolvedContextSource(
                        snapshot.content_ref,
                        source.body,
                        snapshot.normalization_version,
                        snapshot.media_type,
                    )
                )
            reports = tuple(RenderedReport(a, row[a.layer]) for a in manifest.artifacts)
            retained = RetainedConsolidated(
                row["knowledge"],
                row["manifest"],
                tuple(sources),
                reports,
                row["fixture_only"],
                row["manifest_digest"],
            )
            material = StoredMaterial(self, scope, root, retained, bindings, conn)
            context = knowledge.context
            await admit_checked_history(
                row["knowledge"],
                prior=(),
                scope_id=context.scope_id,
                research_id=context.research_id,
                revision_id=context.revision_id,
                resolver=material,
                reviewers=tuple({i.reviewer for i in knowledge.verification_inputs}),
            )
            admitted = await admit_render_manifest(
                row["manifest"],
                scope_id=context.scope_id,
                research_id=context.research_id,
                revision_id=context.revision_id,
                artifact_set_id=manifest.artifact_set_id,
                resolver=material,
                reviewers=tuple({i.reviewer for i in manifest.audit_inputs}),
            )
            _check_eligibility(admitted)
            return retained

    async def export_consolidated(
        self, scope: UUID, root: UUID, operation: UUID
    ) -> CanonicalDocument:
        """Export the exact retained consolidated publication for offline reuse."""
        retained = await self.read_consolidated(scope, root, operation)
        async with self._transaction(read=True) as conn:
            await self._require_consolidated(conn)
            row = await (
                await conn.execute(
                    "SELECT expires_at FROM research_staging.roots WHERE scope_id=%s AND root_id=%s AND NOT deleted AND expires_at>now() AND revision_format='consolidated' AND current_consolidated=%s",
                    (scope, root, operation),
                )
            ).fetchone()
            if row is None:
                raise StorageConflictError("consolidated export root unavailable")
            return await build_consolidated_bundle(
                retained,
                scope=scope,
                root=root,
                operation=operation,
                retained_until=row["expires_at"],
            )

    @staticmethod
    def _bundle_origin(raw: bytes, expected_digest: str) -> tuple[CanonicalDocument, UUID, UUID, UUID, datetime, str]:
        document = admit_canonical_json(raw, schema_version=CONSOLIDATED_BUNDLE_SCHEMA)
        if document.digest != expected_digest:
            raise ValueError("consolidated bundle differs from expected digest")
        fields = json.loads(document.data)
        try:
            scope, root, operation = (
                UUID(fields["scope_id"]),
                UUID(fields["root_id"]),
                UUID(fields["operation_id"]),
            )
            retained_until = datetime.fromisoformat(fields["retained_until"])
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("consolidated bundle origin is invalid") from error
        members = fields.get("members")
        try:
            knowledge = base64.b64decode(members["knowledge.json"]["data"], validate=True)
            context = CheckedKnowledge.model_validate_json(knowledge).context
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("consolidated bundle context is invalid") from error
        return document, scope, root, operation, retained_until, context_digest(context)

    async def reserve_consolidated_import(
        self, recipient: UUID, raw: bytes, expected_digest: str
    ) -> UUID:
        """Reserve a recipient root for a same-authority consolidated import."""
        document, origin_scope, origin_root, operation, retained, context_hash = (
            self._bundle_origin(raw, expected_digest)
        )
        await admit_consolidated_bundle(
            raw,
            expected_digest=expected_digest,
            scope=origin_scope,
            root=origin_root,
            operation=operation,
            now=datetime.now(UTC),
        )
        async with self._transaction() as conn:
            version = await self._require_consolidated_import_schema(conn)
            await self._coordinate_imports(conn)
            rows = await self._lock_roots(conn, {(origin_scope, origin_root)}, (recipient,))
            origin = rows[(origin_scope, origin_root)]
            self._active(origin, origin["generation"])
            if (
                origin["revision_format"] != "consolidated"
                or origin["current_consolidated"] != operation
            ):
                raise StorageConflictError("consolidated origin unavailable")
            count = await (
                await conn.execute(
                    "SELECT count(*) AS count FROM research_staging.import_operations WHERE origin_scope_id=%s AND origin_root_id=%s AND bundle_schema=%s",
                    (origin_scope, origin_root, CONSOLIDATED_BUNDLE_SCHEMA),
                )
            ).fetchone()
            if count is None or count["count"] >= 20:
                raise StorageConflictError("origin import limit exceeded")
            capacity = await (
                await conn.execute(
                    "SELECT quota-charged AS free FROM research_staging.scopes WHERE scope_id=%s",
                    (recipient,),
                )
            ).fetchone()
            size = len(raw)
            if capacity is None or capacity["free"] < size or size > MAX_BYTES:
                raise StorageConflictError("recipient quota exhausted")
            now = datetime.now(UTC)
            if any(
                value.tzinfo is None or value.utcoffset() is None
                for value in (retained, origin["expires_at"], now)
            ):
                raise ValueError("timezone-qualified retention required")
            deadline = min(retained, origin["expires_at"], now + timedelta(days=30))
            if deadline <= now:
                raise StorageConflictError("consolidated import retention expired")
            grant = min(deadline, now + timedelta(minutes=5))
            target = await (
                await conn.execute(
                    "INSERT INTO research_staging.roots(scope_id,quota,kind,revision_format,expires_at) VALUES (%s,%s,'import','consolidated',%s) RETURNING root_id",
                    (recipient, MAX_BYTES, grant),
                )
            ).fetchone()
            assert target is not None
            target_root = target["root_id"]
            await self._charge(conn, recipient, target_root, size)
            await conn.execute(
                "INSERT INTO research_staging.import_operations(scope_id,root_id,origin_scope_id,origin_root_id,publication_id,origin_generation,bundle_digest,context_digest,reserved,grant_expires_at,retained_until,bundle_schema) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    recipient,
                    target_root,
                    origin_scope,
                    origin_root,
                    operation,
                    origin["generation"],
                    document.digest,
                    context_hash,
                    size,
                    grant,
                    deadline,
                    CONSOLIDATED_BUNDLE_SCHEMA,
                ),
            )
            # Keep the version check explicit so an old schema cannot silently
            # accept the new bundle type.
            if version < 12:
                raise StorageConflictError("consolidated import schema unavailable")
            return target_root

    async def commit_consolidated_import(
        self, recipient: UUID, root: UUID, raw: bytes
    ) -> UUID:
        async with self._transaction(read=True) as conn:
            await self._require_consolidated_import_schema(conn)
            operation_row = await (
                await conn.execute(
                    "SELECT * FROM research_staging.import_operations WHERE scope_id=%s AND root_id=%s AND bundle_schema=%s",
                    (recipient, root, CONSOLIDATED_BUNDLE_SCHEMA),
                )
            ).fetchone()
            if operation_row is None:
                raise StorageConflictError("consolidated import operation unavailable")
        _document, origin_scope, origin_root, operation, _retained, context_hash = (
            self._bundle_origin(raw, operation_row["bundle_digest"])
        )
        bundle = await admit_consolidated_bundle(
            raw,
            expected_digest=operation_row["bundle_digest"],
            scope=origin_scope,
            root=origin_root,
            operation=operation,
            now=datetime.now(UTC),
        )
        async with self._transaction() as conn:
            await self._require_consolidated_import_schema(conn)
            await self._coordinate_imports(conn)
            rows = await self._lock_roots(
                conn, {(recipient, root), (origin_scope, origin_root)}
            )
            target, origin = rows[(recipient, root)], rows[(origin_scope, origin_root)]
            current = await (
                await conn.execute(
                    "SELECT * FROM research_staging.import_operations WHERE scope_id=%s AND root_id=%s AND bundle_schema=%s",
                    (recipient, root, CONSOLIDATED_BUNDLE_SCHEMA),
                )
            ).fetchone()
            if current is None:
                raise StorageConflictError("consolidated import operation unavailable")
            if current["state"] == "committed":
                if current["receipt_digest"] != bundle.digest:
                    raise StorageConflictError("consolidated import receipt mismatch")
                return root
            if (
                target["deleted"]
                or not target["fresh"]
                or target["kind"] != "import"
                or target["revision_format"] != "consolidated"
                or target["generation"] != 1
                or current["bundle_digest"] != bundle.digest
                or current["context_digest"] != context_hash
                or current["origin_scope_id"] != origin_scope
                or current["origin_root_id"] != origin_root
                or current["publication_id"] != operation
                or current["origin_generation"] != origin["generation"]
                or origin["deleted"]
                or not origin["fresh"]
                or origin["current_consolidated"] != current["publication_id"]
            ):
                raise StorageConflictError("consolidated import authority unavailable")
            now = datetime.now(UTC)
            deadline = min(current["retained_until"], origin["expires_at"])
            if deadline <= now or current["grant_expires_at"] <= now:
                raise StorageConflictError("consolidated import expired")
            size = len(raw)
            if size > current["reserved"]:
                raise StorageConflictError("consolidated import reservation exceeded")
            await conn.execute(
                "INSERT INTO research_staging.imported_bundles(scope_id,root_id,payload,digest,bundle_schema) VALUES (%s,%s,%s,%s,%s)",
                (recipient, root, bundle.data, bundle.digest, CONSOLIDATED_BUNDLE_SCHEMA),
            )
            await self._charge(conn, recipient, root, size - current["reserved"])
            await conn.execute(
                "UPDATE research_staging.import_operations SET state='committed',receipt_digest=%s WHERE scope_id=%s AND root_id=%s",
                (bundle.digest, recipient, root),
            )
            await conn.execute(
                "UPDATE research_staging.roots SET published_at=now(),expires_at=%s WHERE scope_id=%s AND root_id=%s",
                (deadline, recipient, root),
            )
            return root

    async def read_consolidated_import(
        self, recipient: UUID, root: UUID
    ) -> ImportedConsolidated:
        async with self._transaction(read=True) as conn:
            await self._require_consolidated_import_schema(conn)
            row = await (
                await conn.execute(
                    "SELECT o.*,b.payload,b.digest,least(t.expires_at,s.expires_at) AS effective_deadline FROM research_staging.import_operations o JOIN research_staging.imported_bundles b USING(scope_id,root_id,bundle_schema) JOIN research_staging.roots t USING(scope_id,root_id) JOIN research_staging.roots s ON s.scope_id=o.origin_scope_id AND s.root_id=o.origin_root_id WHERE o.scope_id=%s AND o.root_id=%s AND o.bundle_schema=%s AND o.state='committed' AND NOT t.deleted AND t.kind='import' AND t.revision_format='consolidated' AND t.expires_at>now() AND NOT s.deleted AND s.kind='native' AND s.revision_format='consolidated' AND s.generation=o.origin_generation AND s.current_consolidated=o.publication_id AND s.expires_at>now()",
                    (recipient, root, CONSOLIDATED_BUNDLE_SCHEMA),
                )
            ).fetchone()
            if row is None:
                raise StorageConflictError("consolidated import unavailable")
            document, origin_scope, origin_root, operation, _retained, _context = (
                self._bundle_origin(row["payload"], row["digest"])
            )
            if (
                row["origin_scope_id"] != origin_scope
                or row["origin_root_id"] != origin_root
                or row["publication_id"] != operation
            ):
                raise StorageConflictError("consolidated import origin mismatch")
            bundle = await admit_consolidated_bundle(
                row["payload"],
                expected_digest=row["digest"],
                scope=origin_scope,
                root=origin_root,
                operation=operation,
                now=datetime.now(UTC),
            )
            if row["receipt_digest"] != bundle.digest:
                raise StorageConflictError("consolidated import integrity mismatch")
            return ImportedConsolidated(recipient, root, row["effective_deadline"], document)

    async def consolidated_import_receipt(self, recipient: UUID, root: UUID) -> str | None:
        async with self._transaction(read=True) as conn:
            await self._require_consolidated_import_schema(conn)
            row = await (
                await conn.execute(
                    "SELECT receipt_digest FROM research_staging.import_operations WHERE scope_id=%s AND root_id=%s AND bundle_schema=%s AND state='committed'",
                    (recipient, root, CONSOLIDATED_BUNDLE_SCHEMA),
                )
            ).fetchone()
            return row["receipt_digest"] if row else None

    async def cancel_consolidated_import(self, recipient: UUID, root: UUID) -> None:
        async with self._transaction() as conn:
            version = await self._require_consolidated_import_schema(conn)
            await self._coordinate_imports(conn)
            operation = await (
                await conn.execute(
                    "SELECT * FROM research_staging.import_operations WHERE scope_id=%s AND root_id=%s AND bundle_schema=%s",
                    (recipient, root, CONSOLIDATED_BUNDLE_SCHEMA),
                )
            ).fetchone()
            if operation is None:
                raise StorageConflictError("consolidated import operation unavailable")
            rows = await self._lock_roots(
                conn,
                {(recipient, root), (operation["origin_scope_id"], operation["origin_root_id"])},
            )
            if operation["state"] == "pending":
                await self._purge_root(conn, recipient, root, rows[(recipient, root)], version)

    @staticmethod
    async def _require_consolidated_import_schema(conn: Connection) -> int:
        version = await (
            await conn.execute("SELECT version FROM research_staging.schema_version")
        ).fetchone()
        if version is None or version["version"] != 12:
            raise StorageConflictError("consolidated import schema unavailable")
        return int(version["version"])
