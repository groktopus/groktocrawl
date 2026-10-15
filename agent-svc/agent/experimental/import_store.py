"""Trusted-server same-authority imports; no public authentication or remote grants."""

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, ClassVar
from uuid import UUID

from .artifact_bundle import (
    BUNDLE_SCHEMA,
    ArtifactBundleStore,
    VerifiedBundle,
    admit_bundle,
)
from .canonical import MAX_BYTES, CanonicalDocument
from .publication_store import PublicationContext
from .source_store import Connection, StorageConflictError

MAX_IMPORTS = 20


@dataclass(frozen=True)
class ImportedArtifact:
    recipient_scope: UUID
    recipient_root: UUID
    retained_until: datetime
    bundle: VerifiedBundle


def effective_retention(
    exported: datetime, origin: datetime, now: datetime
) -> datetime:
    if any(
        value.tzinfo is None or value.utcoffset() is None
        for value in (exported, origin, now)
    ):
        raise ValueError("timezone-qualified retention required")
    deadline = min(exported, origin, now + timedelta(days=30))
    if deadline <= now:
        raise StorageConflictError("import retention expired")
    return deadline


class ImportStore(ArtifactBundleStore):
    bundle_schema: ClassVar[str] = BUNDLE_SCHEMA
    admit_import_bundle = staticmethod(admit_bundle)

    async def _export_import_origin(
        self,
        conn: Connection,
        scope: UUID,
        root: UUID,
        publication: UUID,
        context: PublicationContext,
    ) -> CanonicalDocument:
        return await self._export_publication(conn, scope, root, publication, context)

    @classmethod
    def _check_import_format(cls, operation: dict[str, Any]) -> None:
        if operation.get("bundle_schema", BUNDLE_SCHEMA) != cls.bundle_schema:
            raise StorageConflictError("import bundle format unavailable")

    async def migrate_imports(self) -> None:
        sql = (
            Path(__file__)
            .with_name("migrations")
            .joinpath("005_scoped_imports.sql")
            .read_text()
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
            if version != [{"version": 4}]:
                raise StorageConflictError("migration requires schema 4")
            await conn.execute(sql, prepare=False)

    @staticmethod
    async def _require_import_schema(conn: Connection) -> int:
        version = await (
            await conn.execute("SELECT version FROM research_staging.schema_version")
        ).fetchall()
        if version not in (
            [{"version": 5}],
            [{"version": 6}],
            [{"version": 7}],
            [{"version": 8}],
            [{"version": 9}],
            [{"version": 10}],
            [{"version": 11}],
            [{"version": 12}],
        ):
            raise StorageConflictError("import schema unavailable")
        return int(version[0]["version"])

    async def _match_origin(
        self, conn: Connection, bundle: VerifiedBundle, context: PublicationContext
    ) -> None:
        live = json.loads(
            (
                await self._export_import_origin(
                    conn,
                    bundle.scope_id,
                    bundle.root_id,
                    bundle.publication_id,
                    context,
                )
            ).data
        )
        incoming = json.loads(bundle.document.data)
        # Retention is independently bounded by both current origin and bundle.
        incoming.pop("retained_until")
        live.pop("retained_until")
        if incoming != live:
            raise StorageConflictError("bundle differs from live origin")

    @classmethod
    async def _operation(
        cls, conn: Connection, scope: UUID, root: UUID
    ) -> dict[str, Any]:
        row = await (
            await conn.execute(
                "SELECT * FROM research_staging.import_operations WHERE scope_id=%s AND root_id=%s",
                (scope, root),
            )
        ).fetchone()
        if row is None:
            raise StorageConflictError("import operation unavailable")
        cls._check_import_format(row)
        return row

    @classmethod
    def _validate(
        cls, raw: bytes, operation: dict[str, Any], context: PublicationContext
    ) -> VerifiedBundle:
        cls._check_import_format(operation)
        if context.digest() != operation["context_digest"]:
            raise StorageConflictError("import context unavailable")
        return cls.admit_import_bundle(
            raw,
            expected_digest=operation["bundle_digest"],
            scope=operation["origin_scope_id"],
            root=operation["origin_root_id"],
            publication=operation["publication_id"],
            context=context,
            now=datetime.now(UTC),
        )

    async def reserve_import(
        self,
        recipient: UUID,
        origin_scope: UUID,
        origin_root: UUID,
        publication: UUID,
        raw: bytes,
        expected_digest: str,
        context: PublicationContext,
    ) -> UUID:
        """Privileged internal grant issuer; caller must authorize the recipient scope."""
        bundle = self.admit_import_bundle(
            raw,
            expected_digest=expected_digest,
            scope=origin_scope,
            root=origin_root,
            publication=publication,
            context=context,
            now=datetime.now(UTC),
        )
        async with self._transaction() as conn:
            version = await self._require_import_schema(conn)
            await self._coordinate_imports(conn)
            rows = await self._lock_roots(
                conn, {(origin_scope, origin_root)}, (recipient,)
            )
            origin = rows[(origin_scope, origin_root)]
            self._active(origin, origin["generation"])
            await self._match_origin(conn, bundle, context)
            count = await (
                await conn.execute(
                    "SELECT count(*) AS count FROM research_staging.import_operations WHERE origin_scope_id=%s AND origin_root_id=%s",
                    (origin_scope, origin_root),
                )
            ).fetchone()
            if count is None or count["count"] >= MAX_IMPORTS:
                raise StorageConflictError("origin import limit exceeded")
            capacity = await (
                await conn.execute(
                    "SELECT quota-charged AS free FROM research_staging.scopes WHERE scope_id=%s",
                    (recipient,),
                )
            ).fetchone()
            size = len(bundle.document.data)
            if capacity is None or capacity["free"] < size:
                raise StorageConflictError("recipient quota exhausted")
            now = datetime.now(UTC)
            retained_until = effective_retention(
                datetime.fromisoformat(
                    json.loads(bundle.document.data)["retained_until"]
                ),
                origin["expires_at"],
                now,
            )
            grant_until = min(retained_until, now + timedelta(minutes=5))
            target = await (
                await conn.execute(
                    "INSERT INTO research_staging.roots(scope_id,kind,quota,expires_at) VALUES (%s,'import',%s,%s) RETURNING root_id",
                    (recipient, MAX_BYTES, grant_until),
                )
            ).fetchone()
            assert target is not None
            root = target["root_id"]
            await self._charge(conn, recipient, root, size)
            await conn.execute(
                "INSERT INTO research_staging.import_operations(scope_id,root_id,origin_scope_id,origin_root_id,publication_id,origin_generation,bundle_digest,context_digest,reserved,grant_expires_at,retained_until) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    recipient,
                    root,
                    origin_scope,
                    origin_root,
                    publication,
                    origin["generation"],
                    bundle.document.digest,
                    context.digest(),
                    size,
                    grant_until,
                    retained_until,
                ),
            )
            if version >= 9:
                await conn.execute(
                    "UPDATE research_staging.import_operations SET bundle_schema=%s WHERE scope_id=%s AND root_id=%s",
                    (self.bundle_schema, recipient, root),
                )
            return root

    async def commit_import(
        self, recipient: UUID, root: UUID, raw: bytes, context: PublicationContext
    ) -> UUID:
        # Representation checks occur before acquiring mutation locks.
        async with self._transaction(read=True) as conn:
            await self._require_import_schema(conn)
            before = await self._operation(conn, recipient, root)
        bundle = self._validate(raw, before, context)
        async with self._transaction() as conn:
            version = await self._require_import_schema(conn)
            await self._coordinate_imports(conn)
            operation = await self._operation(conn, recipient, root)
            origin_key = (operation["origin_scope_id"], operation["origin_root_id"])
            rows = await self._lock_roots(conn, {origin_key, (recipient, root)})
            origin, target = rows[origin_key], rows[(recipient, root)]
            self._active(origin, operation["origin_generation"])
            if (
                target["deleted"]
                or not target["fresh"]
                or target["kind"] != "import"
                or target["generation"] != 1
                or operation["state"] == "cancelled"
            ):
                raise StorageConflictError("import root unavailable")
            if (
                operation["bundle_digest"] != bundle.document.digest
                or operation["context_digest"] != context.digest()
            ):
                raise StorageConflictError("import binding changed")
            if operation["state"] == "committed":
                if operation["receipt_digest"] != bundle.document.digest:
                    raise StorageConflictError("import receipt mismatch")
                return root
            now = datetime.now(UTC)
            if operation["grant_expires_at"] <= now:
                raise StorageConflictError("import grant expired")
            await self._match_origin(conn, bundle, context)
            retained_until = effective_retention(
                operation["retained_until"], origin["expires_at"], now
            )
            size = len(bundle.document.data)
            if size > operation["reserved"]:
                raise StorageConflictError("import reservation exceeded")
            if version >= 9:
                await conn.execute(
                    "INSERT INTO research_staging.imported_bundles(scope_id,root_id,payload,digest,bundle_schema) VALUES (%s,%s,%s,%s,%s)",
                    (
                        recipient,
                        root,
                        bundle.document.data,
                        bundle.document.digest,
                        self.bundle_schema,
                    ),
                )
            else:
                await conn.execute(
                    "INSERT INTO research_staging.imported_bundles(scope_id,root_id,payload,digest) VALUES (%s,%s,%s,%s)",
                    (recipient, root, bundle.document.data, bundle.document.digest),
                )
            await self._charge(conn, recipient, root, size - operation["reserved"])
            await conn.execute(
                "UPDATE research_staging.import_operations SET state='committed',receipt_digest=%s WHERE scope_id=%s AND root_id=%s",
                (bundle.document.digest, recipient, root),
            )
            await conn.execute(
                "UPDATE research_staging.roots SET published_at=now(),expires_at=%s WHERE scope_id=%s AND root_id=%s",
                (retained_until, recipient, root),
            )
            return root

    async def read_import(
        self, recipient: UUID, root: UUID, context: PublicationContext
    ) -> ImportedArtifact:
        async with self._transaction(read=True) as conn:
            await self._require_import_schema(conn)
            row = await (
                await conn.execute(
                    "SELECT o.*,b.payload,b.digest,least(t.expires_at,s.expires_at) AS effective_deadline FROM research_staging.import_operations o JOIN research_staging.imported_bundles b USING(scope_id,root_id) JOIN research_staging.roots t USING(scope_id,root_id) JOIN research_staging.roots s ON s.scope_id=o.origin_scope_id AND s.root_id=o.origin_root_id WHERE o.scope_id=%s AND o.root_id=%s AND o.state='committed' AND NOT t.deleted AND t.kind='import' AND t.generation=1 AND t.expires_at>now() AND NOT s.deleted AND s.kind='native' AND s.generation=o.origin_generation AND s.expires_at>now()",
                    (recipient, root),
                )
            ).fetchone()
            if row is None:
                raise StorageConflictError("import unavailable")
            bundle = self._validate(row["payload"], row, context)
            if (
                bundle.document.data != row["payload"]
                or bundle.document.digest != row["digest"]
                or bundle.document.digest != row["receipt_digest"]
            ):
                raise StorageConflictError("import integrity mismatch")
            await self._match_origin(conn, bundle, context)
            return ImportedArtifact(recipient, root, row["effective_deadline"], bundle)

    async def import_receipt(self, recipient: UUID, root: UUID) -> str | None:
        async with self._transaction(read=True) as conn:
            await self._require_import_schema(conn)
            row = await (
                await conn.execute(
                    "SELECT * FROM research_staging.import_operations WHERE scope_id=%s AND root_id=%s AND state='committed'",
                    (recipient, root),
                )
            ).fetchone()
            if row:
                self._check_import_format(row)
            return row["receipt_digest"] if row else None

    async def cancel_import(self, recipient: UUID, root: UUID) -> None:
        async with self._transaction() as conn:
            version = await self._require_import_schema(conn)
            await self._coordinate_imports(conn)
            operation = await self._operation(conn, recipient, root)
            rows = await self._lock_roots(
                conn,
                {
                    (recipient, root),
                    (operation["origin_scope_id"], operation["origin_root_id"]),
                },
            )
            if operation["state"] == "pending":
                await self._purge_root(
                    conn, recipient, root, rows[(recipient, root)], version
                )
