"""Bounded structural revisions over retained sources, not final research answers."""

import json
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from .canonical import MAX_BYTES, CanonicalDocument, admit_canonical_json
from .knowledge import KnowledgeStructure, validate_structure
from .source_store import (
    Connection,
    SourceStore,
    StorageConflictError,
    source_descriptor,
)

REVISION_SCHEMA = "retained-structure-prototype/1"
MAX_REVISIONS = 20


@dataclass(frozen=True)
class RetainedRevision:
    revision_id: UUID
    parent_id: UUID | None
    document: CanonicalDocument
    structure: KnowledgeStructure


def admit_revision(
    raw: bytes, scope: UUID, root: UUID, revision: UUID
) -> RetainedRevision:
    document = admit_canonical_json(raw, schema_version=REVISION_SCHEMA)
    fields = json.loads(document.data)
    if set(fields) != {"schema_version", "parent_revision_id", "structure"}:
        raise ValueError("unexpected revision fields")
    if fields["parent_revision_id"] is not None and not isinstance(
        fields["parent_revision_id"], str
    ):
        raise ValueError("parent ID must be UUID text")
    parent = (
        UUID(fields["parent_revision_id"])
        if fields["parent_revision_id"] is not None
        else None
    )
    if parent is not None and str(parent) != fields["parent_revision_id"]:
        raise ValueError("parent ID must be canonical UUID text")
    structure = validate_structure(
        fields["structure"],
        scope_id=str(scope),
        research_id=str(root),
        revision_id=str(revision),
    )
    for snapshot in structure.snapshots:
        if str(UUID(snapshot.snapshot_id)) != snapshot.snapshot_id:
            raise ValueError("snapshot ID must be a retained UUID")
    return RetainedRevision(revision, parent, document, structure)


def entity_records(structure: KnowledgeStructure) -> dict[str, tuple[str, str]]:
    """Exact immutable record comparison; this is not a portable digest format."""
    result = {}
    for kind, key, records in (
        ("snapshot", "snapshot_id", structure.snapshots),
        ("evidence", "evidence_id", structure.evidence),
        ("claim", "claim_id", structure.claims),
        ("relationship", "relationship_id", structure.relationships),
    ):
        for record in records:
            result[getattr(record, key)] = (kind, record.model_dump_json())
    return result


class RevisionStore(SourceStore):
    async def migrate_revisions(self) -> None:
        """Explicit extension after a verified isolated backup; no automatic upgrade."""
        sql = (
            Path(__file__)
            .with_name("migrations")
            .joinpath("002_structure_revisions.sql")
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
            if version != [{"version": 1}]:
                raise StorageConflictError("migration requires schema 1")
            await conn.execute(sql, prepare=False)

    @staticmethod
    async def _require_revision_schema(conn: Connection) -> None:
        version = await (
            await conn.execute("SELECT version FROM research_staging.schema_version")
        ).fetchall()
        if version not in (
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
        ):
            raise StorageConflictError("revision schema unavailable")

    async def reserve_revision(
        self, scope: UUID, root: UUID, generation: int, parent: UUID | None, size: int
    ) -> UUID:
        if type(size) is not int or not 0 < size <= MAX_BYTES:
            raise ValueError("invalid revision reservation")
        async with self._transaction() as conn:
            await self._require_revision_schema(conn)
            row = await self._lock(conn, scope, root)
            self._active(row, generation)
            if row.get("revision_format", "structure") != "structure":
                raise StorageConflictError("root requires complete research revisions")
            if row["current_revision"] != parent:
                raise StorageConflictError("stale revision parent")
            if size > min(row["scope_free"], row["quota"] - row["charged"]):
                raise StorageConflictError("quota exhausted")
            await self._charge(conn, scope, root, size)
            await self._renew_staging(conn, scope, root)
            result = await (
                await conn.execute(
                    "INSERT INTO research_staging.revision_operations(scope_id,root_id,generation,parent_id,reserved) VALUES (%s,%s,%s,%s,%s) RETURNING revision_id",
                    (scope, root, generation, parent, size),
                )
            ).fetchone()
            assert result is not None
            return result["revision_id"]

    @staticmethod
    async def _check_sources(
        conn: Connection, scope: UUID, root: UUID, structure: KnowledgeStructure
    ) -> None:
        for source in structure.snapshots:
            row = await (
                await conn.execute(
                    "SELECT s.body_digest,s.descriptor,s.descriptor_digest,b.body FROM research_staging.snapshots s JOIN research_staging.blobs b ON b.scope_id=s.scope_id AND b.digest=s.body_digest WHERE s.scope_id=%s AND s.root_id=%s AND s.snapshot_id=%s",
                    (scope, root, UUID(source.snapshot_id)),
                )
            ).fetchone()
            expected = source_descriptor(
                source.text.encode("utf-8"), source.canonical_url
            )
            if (
                row is None
                or row["body"] != source.text.encode("utf-8")
                or row["body_digest"] != source.digest
                or row["descriptor"] != expected.data
                or row["descriptor_digest"] != expected.digest
                or source.normalization_version != "utf8-exact/1"
            ):
                raise StorageConflictError("retained source reference mismatch")

    async def commit_revision(
        self, scope: UUID, root: UUID, generation: int, revision: UUID, raw: bytes
    ) -> UUID:
        admitted = admit_revision(raw, scope, root, revision)
        async with self._transaction() as conn:
            await self._require_revision_schema(conn)
            current = await self._lock(conn, scope, root)
            self._active(current, generation)
            if current.get("revision_format", "structure") != "structure":
                raise StorageConflictError("root requires complete research revisions")
            operation = await (
                await conn.execute(
                    "SELECT * FROM research_staging.revision_operations WHERE scope_id=%s AND root_id=%s AND revision_id=%s",
                    (scope, root, revision),
                )
            ).fetchone()
            if (
                operation is None
                or operation["state"] == "cancelled"
                or operation["generation"] != generation
            ):
                raise StorageConflictError("revision operation unavailable")
            if operation["state"] == "committed":
                if operation["input_digest"] != admitted.document.digest:
                    raise StorageConflictError("revision input changed")
                return revision
            if (
                current["current_revision"] != admitted.parent_id
                or operation["parent_id"] != admitted.parent_id
            ):
                raise StorageConflictError("stale revision parent")
            if len(admitted.document.data) > operation["reserved"]:
                raise StorageConflictError("revision reservation exceeded")
            history = await (
                await conn.execute(
                    "SELECT revision_id,payload FROM research_staging.revisions WHERE scope_id=%s AND root_id=%s LIMIT %s",
                    (scope, root, MAX_REVISIONS),
                )
            ).fetchall()
            if len(history) >= MAX_REVISIONS:
                raise StorageConflictError("revision history limit exceeded")
            incoming = entity_records(admitted.structure)
            for previous in history:
                prior = admit_revision(
                    previous["payload"], scope, root, previous["revision_id"]
                )
                if any(
                    incoming[key] != value
                    for key, value in entity_records(prior.structure).items()
                    if key in incoming
                ):
                    raise StorageConflictError("entity identity reassigned")
            await self._check_sources(conn, scope, root, admitted.structure)
            await conn.execute(
                "INSERT INTO research_staging.revisions(scope_id,root_id,revision_id,parent_id,payload,digest) VALUES (%s,%s,%s,%s,%s,%s)",
                (
                    scope,
                    root,
                    revision,
                    admitted.parent_id,
                    admitted.document.data,
                    admitted.document.digest,
                ),
            )
            for source in admitted.structure.snapshots:
                await conn.execute(
                    "INSERT INTO research_staging.revision_sources(scope_id,root_id,revision_id,snapshot_id) VALUES (%s,%s,%s,%s)",
                    (scope, root, revision, UUID(source.snapshot_id)),
                )
            await self._charge(
                conn, scope, root, len(admitted.document.data) - operation["reserved"]
            )
            await conn.execute(
                "UPDATE research_staging.revision_operations SET state='committed',input_digest=%s WHERE scope_id=%s AND root_id=%s AND revision_id=%s",
                (admitted.document.digest, scope, root, revision),
            )
            await conn.execute(
                "UPDATE research_staging.roots SET current_revision=%s WHERE scope_id=%s AND root_id=%s",
                (revision, scope, root),
            )
            await self._renew_staging(conn, scope, root)
            return revision

    async def read_revision(
        self, scope: UUID, root: UUID, revision: UUID
    ) -> RetainedRevision:
        async with self._transaction(read=True) as conn:
            await self._require_revision_schema(conn)
            return await self._read_revision(conn, scope, root, revision)

    async def _read_revision(
        self, conn: Connection, scope: UUID, root: UUID, revision: UUID
    ) -> RetainedRevision:
        row = await (
            await conn.execute(
                "SELECT v.* FROM research_staging.revisions v JOIN research_staging.roots r USING(scope_id,root_id) WHERE v.scope_id=%s AND v.root_id=%s AND v.revision_id=%s AND NOT r.deleted AND r.expires_at>now()",
                (scope, root, revision),
            )
        ).fetchone()
        if row is None:
            raise StorageConflictError("revision unavailable")
        admitted = admit_revision(row["payload"], scope, root, revision)
        if (
            admitted.document.data != row["payload"]
            or admitted.document.digest != row["digest"]
            or admitted.parent_id != row["parent_id"]
        ):
            raise StorageConflictError("revision integrity mismatch")
        refs = await (
            await conn.execute(
                "SELECT snapshot_id FROM research_staging.revision_sources WHERE scope_id=%s AND root_id=%s AND revision_id=%s",
                (scope, root, revision),
            )
        ).fetchall()
        if {str(ref["snapshot_id"]) for ref in refs} != {
            s.snapshot_id for s in admitted.structure.snapshots
        }:
            raise StorageConflictError("revision ledger mismatch")
        await self._check_sources(conn, scope, root, admitted.structure)
        return admitted

    async def revision_receipt(
        self, scope: UUID, root: UUID, revision: UUID
    ) -> str | None:
        """Return committed input digest for lost-ACK reconciliation, even after delete."""
        async with self._transaction(read=True) as conn:
            await self._require_revision_schema(conn)
            row = await (
                await conn.execute(
                    "SELECT input_digest FROM research_staging.revision_operations WHERE scope_id=%s AND root_id=%s AND revision_id=%s AND state='committed'",
                    (scope, root, revision),
                )
            ).fetchone()
            return row["input_digest"] if row else None

    async def cancel_revision(self, scope: UUID, root: UUID, revision: UUID) -> None:
        async with self._transaction() as conn:
            await self._require_revision_schema(conn)
            await self._lock(conn, scope, root)
            row = await (
                await conn.execute(
                    "SELECT reserved FROM research_staging.revision_operations WHERE scope_id=%s AND root_id=%s AND revision_id=%s AND state='pending'",
                    (scope, root, revision),
                )
            ).fetchone()
            if row:
                await self._charge(conn, scope, root, -row["reserved"])
                await conn.execute(
                    "UPDATE research_staging.revision_operations SET state='cancelled' WHERE scope_id=%s AND root_id=%s AND revision_id=%s",
                    (scope, root, revision),
                )
