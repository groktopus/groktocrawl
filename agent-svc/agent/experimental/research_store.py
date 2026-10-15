"""Complete retained fixture histories; legacy structural roots stay separate."""

from pathlib import Path
from uuid import UUID

from .canonical import MAX_BYTES
from .expiry_store import ExpiryStore
from .research_revision import (
    AdmittedResearchRevision,
    _decode,
    admit_research_revision,
)
from .source_store import ROOT_QUOTA, Connection, StorageConflictError


class ResearchStore(ExpiryStore):
    async def migrate_research(self) -> None:
        sql = (
            Path(__file__)
            .with_name("migrations")
            .joinpath("007_complete_research_revisions.sql")
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
            if version != [{"version": 6}]:
                raise StorageConflictError("migration requires schema 6")
            await conn.execute(sql, prepare=False)

    @staticmethod
    async def _require_research_schema(conn: Connection) -> None:
        version = await (
            await conn.execute("SELECT version FROM research_staging.schema_version")
        ).fetchall()
        if version not in (
            [{"version": 7}],
            [{"version": 8}],
            [{"version": 9}],
            [{"version": 10}],
            [{"version": 11}],
            [{"version": 12}],
        ):
            raise StorageConflictError("complete research schema unavailable")

    async def create_research_root(self, scope: UUID, quota: int = ROOT_QUOTA) -> UUID:
        if type(quota) is not int or not 0 < quota <= ROOT_QUOTA:
            raise ValueError("invalid root quota")
        async with self._transaction() as conn:
            await self._require_research_schema(conn)
            return await self._insert_root(conn, scope, quota, research=True)

    async def reserve_research(
        self, scope: UUID, root: UUID, generation: int, parent: UUID | None, size: int
    ) -> UUID:
        if type(size) is not int or not 0 < size <= MAX_BYTES:
            raise ValueError("invalid research reservation")
        async with self._transaction() as conn:
            await self._require_research_schema(conn)
            row = await self._lock(conn, scope, root)
            self._active(row, generation)
            if (
                row["revision_format"] != "research"
                or row["current_research_revision"] != parent
            ):
                raise StorageConflictError("research root or parent unavailable")
            if size > min(row["scope_free"], row["quota"] - row["charged"]):
                raise StorageConflictError("quota exhausted")
            await self._charge(conn, scope, root, size)
            await self._renew_staging(conn, scope, root)
            result = await (
                await conn.execute(
                    "INSERT INTO research_staging.research_revision_operations(scope_id,root_id,generation,parent_id,reserved) VALUES (%s,%s,%s,%s,%s) RETURNING revision_id",
                    (scope, root, generation, parent, size),
                )
            ).fetchone()
            assert result is not None
            return result["revision_id"]

    async def _research_history(
        self, conn: Connection, scope: UUID, root: UUID, tip: UUID | None
    ) -> tuple[AdmittedResearchRevision, ...]:
        rows: list[AdmittedResearchRevision] = []
        seen = set()
        cursor = tip
        while cursor is not None:
            if cursor in seen or len(rows) >= 20:
                raise StorageConflictError("complete research history exceeds bounds")
            seen.add(cursor)
            row = await (
                await conn.execute(
                    "SELECT v.* FROM research_staging.research_revisions v JOIN research_staging.roots r USING(scope_id,root_id) WHERE v.scope_id=%s AND v.root_id=%s AND v.revision_id=%s AND r.revision_format='research' AND NOT r.deleted AND r.expires_at>now()",
                    (scope, root, cursor),
                )
            ).fetchone()
            if row is None:
                raise StorageConflictError("complete research revision unavailable")
            decoded = _decode(row["payload"])
            structure = decoded.revision.research.verifications.structure
            if (
                decoded.document.data != row["payload"]
                or decoded.document.digest != row["digest"]
                or (structure.scope_id, structure.research_id, structure.revision_id)
                != (str(scope), str(root), str(cursor))
                or decoded.revision.parent_revision_id
                != (str(row["parent_id"]) if row["parent_id"] else None)
            ):
                raise StorageConflictError("complete research integrity mismatch")
            refs = await (
                await conn.execute(
                    "SELECT snapshot_id FROM research_staging.research_revision_sources WHERE scope_id=%s AND root_id=%s AND revision_id=%s",
                    (scope, root, cursor),
                )
            ).fetchall()
            if {str(ref["snapshot_id"]) for ref in refs} != {
                source.snapshot_id for source in structure.snapshots
            }:
                raise StorageConflictError("complete research source ledger mismatch")
            await self._check_sources(conn, scope, root, structure)
            rows.append(decoded)
            cursor = row["parent_id"]
        rows.reverse()
        if rows:
            last = rows[-1].revision
            admit_research_revision(
                rows[-1].document.data,
                scope_id=str(scope),
                research_id=str(root),
                revision_id=str(tip),
                parent_revision_id=last.parent_revision_id,
                prior=tuple(value.document.data for value in rows[:-1]),
            )
        return tuple(rows)

    async def commit_research(
        self, scope: UUID, root: UUID, generation: int, revision: UUID, raw: bytes
    ) -> UUID:
        incoming = _decode(raw)
        structure = incoming.revision.research.verifications.structure
        parent_text = incoming.revision.parent_revision_id
        parent = UUID(parent_text) if parent_text is not None else None
        if parent_text != (str(parent) if parent else None) or (
            structure.scope_id,
            structure.research_id,
            structure.revision_id,
        ) != (str(scope), str(root), str(revision)):
            raise ValueError("complete research identity mismatch")
        if any(
            str(UUID(source.snapshot_id)) != source.snapshot_id
            for source in structure.snapshots
        ):
            raise ValueError("retained snapshot requires canonical UUID")
        async with self._transaction() as conn:
            await self._require_research_schema(conn)
            current = await self._lock(conn, scope, root)
            self._active(current, generation)
            if current["revision_format"] != "research":
                raise StorageConflictError("root is not complete research")
            operation = await (
                await conn.execute(
                    "SELECT * FROM research_staging.research_revision_operations WHERE scope_id=%s AND root_id=%s AND revision_id=%s",
                    (scope, root, revision),
                )
            ).fetchone()
            if (
                operation is None
                or operation["state"] == "cancelled"
                or operation["generation"] != generation
                or operation["parent_id"] != parent
            ):
                raise StorageConflictError("research operation unavailable")
            if operation["state"] == "committed":
                if operation["input_digest"] != incoming.document.digest:
                    raise StorageConflictError("research input changed")
                return revision
            if current["current_research_revision"] != parent:
                raise StorageConflictError("stale research parent")
            if len(incoming.document.data) > operation["reserved"]:
                raise StorageConflictError("research reservation exceeded")
            history = await self._research_history(conn, scope, root, parent)
            admitted = admit_research_revision(
                raw,
                scope_id=str(scope),
                research_id=str(root),
                revision_id=str(revision),
                parent_revision_id=parent_text,
                prior=tuple(value.document.data for value in history),
            )
            await self._check_sources(conn, scope, root, structure)
            await conn.execute(
                "INSERT INTO research_staging.research_revisions(scope_id,root_id,revision_id,parent_id,payload,digest) VALUES (%s,%s,%s,%s,%s,%s)",
                (
                    scope,
                    root,
                    revision,
                    parent,
                    admitted.document.data,
                    admitted.document.digest,
                ),
            )
            for source in structure.snapshots:
                await conn.execute(
                    "INSERT INTO research_staging.research_revision_sources(scope_id,root_id,revision_id,snapshot_id) VALUES (%s,%s,%s,%s)",
                    (scope, root, revision, UUID(source.snapshot_id)),
                )
            await self._charge(
                conn, scope, root, len(admitted.document.data) - operation["reserved"]
            )
            await conn.execute(
                "UPDATE research_staging.research_revision_operations SET state='committed',input_digest=%s WHERE scope_id=%s AND root_id=%s AND revision_id=%s",
                (admitted.document.digest, scope, root, revision),
            )
            await conn.execute(
                "UPDATE research_staging.roots SET current_research_revision=%s WHERE scope_id=%s AND root_id=%s",
                (revision, scope, root),
            )
            await self._renew_staging(conn, scope, root)
            return revision

    async def read_research(
        self, scope: UUID, root: UUID, revision: UUID
    ) -> AdmittedResearchRevision:
        async with self._transaction(read=True) as conn:
            await self._require_research_schema(conn)
            return (await self._research_history(conn, scope, root, revision))[-1]

    async def research_receipt(
        self, scope: UUID, root: UUID, revision: UUID
    ) -> str | None:
        async with self._transaction(read=True) as conn:
            await self._require_research_schema(conn)
            row = await (
                await conn.execute(
                    "SELECT input_digest FROM research_staging.research_revision_operations WHERE scope_id=%s AND root_id=%s AND revision_id=%s AND state='committed'",
                    (scope, root, revision),
                )
            ).fetchone()
            return row["input_digest"] if row else None

    async def cancel_research(self, scope: UUID, root: UUID, revision: UUID) -> None:
        async with self._transaction() as conn:
            await self._require_research_schema(conn)
            await self._lock(conn, scope, root)
            row = await (
                await conn.execute(
                    "SELECT reserved FROM research_staging.research_revision_operations WHERE scope_id=%s AND root_id=%s AND revision_id=%s AND state='pending'",
                    (scope, root, revision),
                )
            ).fetchone()
            if row:
                await self._charge(conn, scope, root, -row["reserved"])
                await conn.execute(
                    "UPDATE research_staging.research_revision_operations SET state='cancelled' WHERE scope_id=%s AND root_id=%s AND revision_id=%s",
                    (scope, root, revision),
                )
