"""Opt-in source staging only; no IR publication, sessions or workflow recovery."""

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from .canonical import CanonicalDocument, admit_canonical_json
from .storage_admission import DEFAULT_STORAGE_ADMISSION, StorageAdmission

SCHEMA = "source-staging/1"
MAX_BODY = 10 * 1024 * 1024
MAX_RESERVATION = MAX_BODY + 1024 * 1024
ROOT_QUOTA = 100 * 1024 * 1024
SCOPE_QUOTA = 1024 * 1024 * 1024
type Connection = psycopg.AsyncConnection[dict[str, Any]]


class StorageConflictError(ValueError):
    """Missing, stale, deleted or mismatched storage operation; contains no data."""


@dataclass(frozen=True)
class CommitReceipt:
    snapshot_id: UUID
    input_digest: str
    generation: int


@dataclass(frozen=True)
class RetainedSource:
    snapshot_id: UUID
    body: bytes
    descriptor: CanonicalDocument


def source_descriptor(body: bytes, url: str) -> CanonicalDocument:
    """Validate bounded exact UTF-8 source bytes before entering a transaction."""
    if not isinstance(body, bytes) or len(body) > MAX_BODY:
        raise ValueError("source byte limit exceeded")
    body.decode("utf-8", errors="strict")
    if (
        not isinstance(url, str)
        or not url.startswith(("https://", "http://"))
        or len(url) > 8192
    ):
        raise ValueError("invalid source URL")
    return admit_canonical_json(
        json.dumps(
            {
                "schema_version": SCHEMA,
                "url": url,
                "normalization": "utf8-exact/1",
                "body_sha256": hashlib.sha256(body).hexdigest(),
            },
            ensure_ascii=True,
        ).encode(),
        schema_version=SCHEMA,
    )


class SourceStore:
    """Trusted server-side API. Callers must establish scope authorization.

    Each call owns a short connection/transaction. No user-supplied connection
    strings or scope selection may be exposed via a public API. No auto migration.
    """

    def __init__(
        self, conninfo: str = "", *, admission: StorageAdmission | None = None
    ) -> None:
        self._conninfo = conninfo
        self._admission = DEFAULT_STORAGE_ADMISSION if admission is None else admission

    @asynccontextmanager
    async def _transaction(
        self, *, read: bool = False, bootstrap: bool = False
    ) -> AsyncIterator[Connection]:
        with self._admission.slot():
            async with (
                asyncio.timeout(30),
                await psycopg.AsyncConnection.connect(
                    self._conninfo,
                    row_factory=dict_row,
                    connect_timeout=10,
                    options="-c statement_timeout=10000 -c lock_timeout=3000 -c idle_in_transaction_session_timeout=10000",
                ) as conn,
            ):
                mode = "REPEATABLE READ READ ONLY" if read else "READ COMMITTED"
                await conn.execute(f"SET TRANSACTION ISOLATION LEVEL {mode}")
                if not bootstrap:
                    version = await (
                        await conn.execute(
                            "SELECT version FROM research_staging.schema_version"
                        )
                    ).fetchall()
                    if version not in (
                        [{"version": 1}],
                        [{"version": 2}],
                        [{"version": 3}],
                        [{"version": 4}],
                        [{"version": 5}],
                        [{"version": 6}],
                        [{"version": 7}],
                        [{"version": 8}],
                        [{"version": 9}],
                        [{"version": 10}],
                        [{"version": 11}],
                        [{"version": 12}],
                        [{"version": 13}],
                        [{"version": 14}],
                        [{"version": 15}],
                        [{"version": 16}],
                    ):
                        raise StorageConflictError("unsupported storage schema")
                yield conn

    async def install(self) -> None:
        """Explicit first migration; refuses any existing namespace, atomically."""
        sql = (
            Path(__file__)
            .with_name("migrations")
            .joinpath("001_source_staging.sql")
            .read_text()
        )
        async with self._transaction(bootstrap=True) as conn:
            await conn.execute(sql, prepare=False)

    async def provision_scope(self, scope: UUID, quota: int = SCOPE_QUOTA) -> None:
        """Administrative operation; existing scope/policy is never overwritten."""
        if type(quota) is not int or not 0 < quota <= SCOPE_QUOTA:
            raise ValueError("invalid scope quota")
        async with self._transaction() as conn:
            await conn.execute(
                "INSERT INTO research_staging.scopes(scope_id,quota) VALUES (%s,%s)",
                (scope, quota),
            )

    async def create_root(self, scope: UUID, quota: int = ROOT_QUOTA) -> UUID:
        if type(quota) is not int or not 0 < quota <= ROOT_QUOTA:
            raise ValueError("invalid root quota")
        async with self._transaction() as conn:
            return await self._insert_root(conn, scope, quota)

    @staticmethod
    async def _insert_root(
        conn: Connection, scope: UUID, quota: int, *, research: bool = False
    ) -> UUID:
        row = await (
            await conn.execute(
                "SELECT scope_id FROM research_staging.scopes WHERE scope_id=%s FOR UPDATE",
                (scope,),
            )
        ).fetchone()
        if row is None:
            raise StorageConflictError("scope unavailable")
        row = await (
            await conn.execute(
                (
                    "INSERT INTO research_staging.roots(scope_id,quota,revision_format) VALUES (%s,%s,'research') RETURNING root_id"
                    if research
                    else "INSERT INTO research_staging.roots(scope_id,quota) VALUES (%s,%s) RETURNING root_id"
                ),
                (scope, quota),
            )
        ).fetchone()
        assert row is not None
        return row["root_id"]

    @staticmethod
    async def _lock(conn: Connection, scope: UUID, root: UUID) -> dict[str, Any]:
        scope_row = await (
            await conn.execute(
                "SELECT quota,charged FROM research_staging.scopes WHERE scope_id=%s FOR UPDATE",
                (scope,),
            )
        ).fetchone()
        row = await (
            await conn.execute(
                "SELECT *, expires_at > now() AS fresh FROM research_staging.roots WHERE scope_id=%s AND root_id=%s FOR UPDATE",
                (scope, root),
            )
        ).fetchone()
        if scope_row is None or row is None:
            raise StorageConflictError("root unavailable")
        return {**row, "scope_free": scope_row["quota"] - scope_row["charged"]}

    @staticmethod
    def _active(row: dict[str, Any], generation: int) -> None:
        if (
            row["deleted"]
            or not row["fresh"]
            or row["generation"] != generation
            or row.get("kind", "native") != "native"
        ):
            raise StorageConflictError("root unavailable")

    @staticmethod
    async def _charge(conn: Connection, scope: UUID, root: UUID, delta: int) -> None:
        await conn.execute(
            "UPDATE research_staging.scopes SET charged=charged+%s WHERE scope_id=%s",
            (delta, scope),
        )
        await conn.execute(
            "UPDATE research_staging.roots SET charged=charged+%s WHERE scope_id=%s AND root_id=%s",
            (delta, scope, root),
        )

    @staticmethod
    async def _renew_staging(conn: Connection, scope: UUID, root: UUID) -> None:
        version = await (
            await conn.execute("SELECT version FROM research_staging.schema_version")
        ).fetchone()
        if version and version["version"] >= 3:
            await conn.execute(
                "UPDATE research_staging.roots SET expires_at=CASE WHEN published_at IS NULL THEN now()+interval '24 hours' ELSE expires_at END WHERE scope_id=%s AND root_id=%s",
                (scope, root),
            )
        else:
            await conn.execute(
                "UPDATE research_staging.roots SET expires_at=now()+interval '24 hours' WHERE scope_id=%s AND root_id=%s",
                (scope, root),
            )

    async def reserve(
        self, scope: UUID, root: UUID, generation: int, size: int
    ) -> UUID:
        """Reserve logical capacity BEFORE acquisition; returned ID owns one write."""
        if type(size) is not int or not 0 < size <= MAX_RESERVATION:
            raise ValueError("invalid reservation size")
        async with self._transaction() as conn:
            row = await self._lock(conn, scope, root)
            self._active(row, generation)
            if size > min(row["scope_free"], row["quota"] - row["charged"]):
                raise StorageConflictError("quota exhausted")
            await self._charge(conn, scope, root, size)
            await self._renew_staging(conn, scope, root)
            operation = await (
                await conn.execute(
                    "INSERT INTO research_staging.operations(scope_id,root_id,generation,reserved) VALUES (%s,%s,%s,%s) RETURNING operation_id",
                    (scope, root, generation, size),
                )
            ).fetchone()
            assert operation is not None
            return operation["operation_id"]

    async def commit_source(
        self,
        scope: UUID,
        root: UUID,
        generation: int,
        operation: UUID,
        body: bytes,
        url: str,
    ) -> UUID:
        descriptor = source_descriptor(body, url)
        body_digest = hashlib.sha256(body).hexdigest()
        charge = len(body) + len(descriptor.data)
        async with self._transaction() as conn:
            row = await self._lock(conn, scope, root)
            self._active(row, generation)
            op = await (
                await conn.execute(
                    "SELECT * FROM research_staging.operations WHERE scope_id=%s AND root_id=%s AND operation_id=%s",
                    (scope, root, operation),
                )
            ).fetchone()
            if (
                op is None
                or op["generation"] != generation
                or op["state"] == "cancelled"
            ):
                raise StorageConflictError("operation unavailable")
            if op["state"] == "committed":
                if op["input_digest"] != descriptor.digest:
                    raise StorageConflictError("operation input changed")
                return op["snapshot_id"]
            if charge > op["reserved"]:
                raise StorageConflictError("reservation exceeded")
            await conn.execute(
                "INSERT INTO research_staging.blobs(scope_id,digest,body) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
                (scope, body_digest, body),
            )
            blob = await (
                await conn.execute(
                    "SELECT body FROM research_staging.blobs WHERE scope_id=%s AND digest=%s",
                    (scope, body_digest),
                )
            ).fetchone()
            if blob is None or blob["body"] != body:
                raise StorageConflictError("blob integrity mismatch")
            snapshot = await (
                await conn.execute(
                    "INSERT INTO research_staging.snapshots(scope_id,root_id,body_digest,descriptor,descriptor_digest) VALUES (%s,%s,%s,%s,%s) RETURNING snapshot_id",
                    (scope, root, body_digest, descriptor.data, descriptor.digest),
                )
            ).fetchone()
            assert snapshot is not None
            snapshot_id = snapshot["snapshot_id"]
            await conn.execute(
                "UPDATE research_staging.operations SET state='committed',input_digest=%s,snapshot_id=%s WHERE scope_id=%s AND root_id=%s AND operation_id=%s",
                (descriptor.digest, snapshot_id, scope, root, operation),
            )
            await self._charge(conn, scope, root, charge - op["reserved"])
            await self._renew_staging(conn, scope, root)
            return snapshot_id

    async def read_source(
        self, scope: UUID, root: UUID, snapshot: UUID
    ) -> RetainedSource:
        async with self._transaction(read=True) as conn:
            return await self._read_source(conn, scope, root, snapshot)

    async def _read_source(
        self, conn: Connection, scope: UUID, root: UUID, snapshot: UUID
    ) -> RetainedSource:
        row = await (
            await conn.execute(
                "SELECT s.*,b.body FROM research_staging.snapshots s JOIN research_staging.roots r USING(scope_id,root_id) JOIN research_staging.blobs b ON b.scope_id=s.scope_id AND b.digest=s.body_digest WHERE s.scope_id=%s AND s.root_id=%s AND s.snapshot_id=%s AND NOT r.deleted AND r.expires_at>now()",
                (scope, root, snapshot),
            )
        ).fetchone()
        if row is None:
            raise StorageConflictError("source unavailable")
        canonical = admit_canonical_json(row["descriptor"], schema_version=SCHEMA)
        fields = json.loads(canonical.data)
        expected = source_descriptor(row["body"], fields.get("url"))
        if (
            canonical.data != row["descriptor"]
            or canonical != expected
            or canonical.digest != row["descriptor_digest"]
            or fields["body_sha256"] != row["body_digest"]
        ):
            raise StorageConflictError("source integrity mismatch")
        return RetainedSource(snapshot, row["body"], canonical)

    async def receipt(
        self, scope: UUID, root: UUID, operation: UUID
    ) -> CommitReceipt | None:
        """Reconcile a lost commit ACK; metadata survives deletion, never body text."""
        async with self._transaction(read=True) as conn:
            row = await (
                await conn.execute(
                    "SELECT snapshot_id,input_digest,generation FROM research_staging.operations WHERE scope_id=%s AND root_id=%s AND operation_id=%s AND state='committed'",
                    (scope, root, operation),
                )
            ).fetchone()
            return CommitReceipt(**row) if row else None

    async def cancel_reservation(
        self, scope: UUID, root: UUID, operation: UUID
    ) -> None:
        async with self._transaction() as conn:
            await self._lock(conn, scope, root)
            row = await (
                await conn.execute(
                    "SELECT reserved FROM research_staging.operations WHERE scope_id=%s AND root_id=%s AND operation_id=%s AND state='pending'",
                    (scope, root, operation),
                )
            ).fetchone()
            if row:
                await self._charge(conn, scope, root, -row["reserved"])
                await conn.execute(
                    "UPDATE research_staging.operations SET state='cancelled' WHERE scope_id=%s AND root_id=%s AND operation_id=%s",
                    (scope, root, operation),
                )

    @staticmethod
    async def _coordinate_imports(conn: Connection) -> None:
        # Shared protocol key for schema-5 import topology mutations and deletion.
        await conn.execute("SELECT pg_advisory_xact_lock(1735553908, 5)")

    @staticmethod
    async def _lock_roots(
        conn: Connection,
        roots: set[tuple[UUID, UUID]],
        extra_scopes: tuple[UUID, ...] = (),
    ) -> dict[tuple[UUID, UUID], dict[str, Any]]:
        scopes = {scope for scope, _ in roots} | set(extra_scopes)
        for scope in sorted(scopes):
            found = await (
                await conn.execute(
                    "SELECT scope_id FROM research_staging.scopes WHERE scope_id=%s FOR UPDATE",
                    (scope,),
                )
            ).fetchone()
            if found is None:
                raise StorageConflictError("scope unavailable")
        result = {}
        for scope, root in sorted(roots):
            row = await (
                await conn.execute(
                    "SELECT *,expires_at>now() AS fresh FROM research_staging.roots WHERE scope_id=%s AND root_id=%s FOR UPDATE",
                    (scope, root),
                )
            ).fetchone()
            if row is None:
                raise StorageConflictError("root unavailable")
            result[(scope, root)] = row
        return result

    async def delete_root(self, scope: UUID, root: UUID) -> None:
        """Purge an origin and its bounded recipient copies; keep metadata receipts."""
        async with self._transaction() as conn:
            version_row = await (
                await conn.execute(
                    "SELECT version FROM research_staging.schema_version"
                )
            ).fetchone()
            assert version_row is not None
            version = version_row["version"]
            if version >= 5:
                await self._coordinate_imports(conn)
                rows = await self._lock_purge_roots(conn, scope, root)
                for (target_scope, target_root), row in rows.items():
                    await self._purge_root(
                        conn, target_scope, target_root, row, version
                    )
            else:
                row = await self._lock(conn, scope, root)
                await self._purge_root(conn, scope, root, row, version)

    async def _lock_purge_roots(
        self, conn: Connection, scope: UUID, root: UUID
    ) -> dict[tuple[UUID, UUID], dict[str, Any]]:
        # Caller owns the import coordination lock before discovering dependencies.
        dependents = await (
            await conn.execute(
                "SELECT scope_id,root_id FROM research_staging.import_operations WHERE origin_scope_id=%s AND origin_root_id=%s LIMIT 21",
                (scope, root),
            )
        ).fetchall()
        if len(dependents) > 20:
            raise StorageConflictError("import dependencies exceed purge bound")
        roots = {(scope, root)} | {(r["scope_id"], r["root_id"]) for r in dependents}
        rows = await self._lock_roots(conn, roots)
        if rows[(scope, root)]["kind"] == "import" and dependents:
            raise StorageConflictError("import root has invalid dependents")
        return rows

    async def _purge_root(
        self,
        conn: Connection,
        scope: UUID,
        root: UUID,
        row: dict[str, Any],
        version: int,
    ) -> None:
        if row["deleted"]:
            return
        if version >= 10:
            await conn.execute(
                "DELETE FROM research_staging.consolidated_publications WHERE scope_id=%s AND root_id=%s",
                (scope, root),
            )
            await conn.execute(
                "UPDATE research_staging.consolidated_operations SET state='cancelled' WHERE scope_id=%s AND root_id=%s AND state='pending'",
                (scope, root),
            )
            await conn.execute(
                "UPDATE research_staging.roots SET current_consolidated=NULL WHERE scope_id=%s AND root_id=%s",
                (scope, root),
            )
        if version >= 8:
            await conn.execute(
                "DELETE FROM research_staging.research_publications WHERE scope_id=%s AND root_id=%s",
                (scope, root),
            )
            await conn.execute(
                "UPDATE research_staging.research_publication_operations SET state='cancelled' WHERE scope_id=%s AND root_id=%s AND state='pending'",
                (scope, root),
            )
        if version >= 7:
            await conn.execute(
                "UPDATE research_staging.roots SET current_research_revision=NULL WHERE scope_id=%s AND root_id=%s",
                (scope, root),
            )
            await conn.execute(
                "DELETE FROM research_staging.research_revisions WHERE scope_id=%s AND root_id=%s",
                (scope, root),
            )
            await conn.execute(
                "UPDATE research_staging.research_revision_operations SET state='cancelled' WHERE scope_id=%s AND root_id=%s AND state='pending'",
                (scope, root),
            )
        if version >= 5:
            await conn.execute(
                "DELETE FROM research_staging.imported_bundles WHERE scope_id=%s AND root_id=%s",
                (scope, root),
            )
            await conn.execute(
                "UPDATE research_staging.import_operations SET state='cancelled' WHERE scope_id=%s AND root_id=%s AND state='pending'",
                (scope, root),
            )
        if version >= 3:
            await conn.execute(
                "DELETE FROM research_staging.publications WHERE scope_id=%s AND root_id=%s",
                (scope, root),
            )
            await conn.execute(
                "UPDATE research_staging.publication_operations SET state='cancelled' WHERE scope_id=%s AND root_id=%s AND state='pending'",
                (scope, root),
            )
        if version >= 2:
            await conn.execute(
                "UPDATE research_staging.roots SET current_revision=NULL WHERE scope_id=%s AND root_id=%s",
                (scope, root),
            )
            await conn.execute(
                "DELETE FROM research_staging.revisions WHERE scope_id=%s AND root_id=%s",
                (scope, root),
            )
            await conn.execute(
                "UPDATE research_staging.revision_operations SET state='cancelled' WHERE scope_id=%s AND root_id=%s AND state='pending'",
                (scope, root),
            )
        await conn.execute(
            "DELETE FROM research_staging.snapshots WHERE scope_id=%s AND root_id=%s",
            (scope, root),
        )
        await conn.execute(
            "DELETE FROM research_staging.blobs b WHERE b.scope_id=%s AND NOT EXISTS (SELECT 1 FROM research_staging.snapshots s WHERE s.scope_id=b.scope_id AND s.body_digest=b.digest)",
            (scope,),
        )
        await self._charge(conn, scope, root, -row["charged"])
        await conn.execute(
            "UPDATE research_staging.operations SET state='cancelled' WHERE scope_id=%s AND root_id=%s AND state='pending'",
            (scope, root),
        )
        await conn.execute(
            "UPDATE research_staging.roots SET deleted=true,deleted_at=now(),generation=generation+1 WHERE scope_id=%s AND root_id=%s",
            (scope, root),
        )
