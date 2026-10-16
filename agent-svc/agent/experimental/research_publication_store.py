"""Atomic complete-revision fixture publication in isolated storage."""

import json
from pathlib import Path
from uuid import UUID

from .canonical import MAX_BYTES
from .publication_store import PublicationContext, RetainedPublication
from .research_publication import admit_research_publication
from .research_store import ResearchStore
from .source_store import Connection, StorageConflictError


class ResearchPublicationStore(ResearchStore):
    async def migrate_research_publications(self) -> None:
        migration = (
            Path(__file__).with_name("migrations")
            / "008_complete_research_publications.sql"
        )
        async with self._transaction(bootstrap=True) as conn:
            await conn.execute(
                "LOCK TABLE research_staging.schema_version IN ACCESS EXCLUSIVE MODE"
            )
            current = await (
                await conn.execute(
                    "SELECT version FROM research_staging.schema_version"
                )
            ).fetchall()
            if current != [{"version": 7}]:
                raise StorageConflictError(
                    "complete publication migration requires schema 7"
                )
            await conn.execute(migration.read_text(), prepare=False)

    @staticmethod
    async def _require_complete_publications(conn: Connection) -> None:
        current = await (
            await conn.execute("SELECT version FROM research_staging.schema_version")
        ).fetchall()
        if current not in (
            [{"version": 8}],
            [{"version": 9}],
            [{"version": 10}],
            [{"version": 11}],
            [{"version": 12}],
        ):
            raise StorageConflictError("complete publication schema unavailable")

    async def reserve_research_publication(
        self,
        scope: UUID,
        root: UUID,
        generation: int,
        revision: UUID,
        size: int,
        context: PublicationContext,
        *,
        rerender_of: UUID | None = None,
        original_context: PublicationContext | None = None,
    ) -> UUID:
        digest = context.digest()
        if type(size) is not int or not 0 < size <= MAX_BYTES:
            raise ValueError("invalid complete publication reservation")
        if (rerender_of is None) != (original_context is None):
            raise ValueError(
                "historical rerender requires original and trusted context"
            )
        async with self._transaction() as conn:
            await self._require_complete_publications(conn)
            row = await self._lock(conn, scope, root)
            self._active(row, generation)
            if row["revision_format"] != "research":
                raise StorageConflictError(
                    "complete publication requires research root"
                )
            pinned = (await self._research_history(conn, scope, root, revision))[-1]
            if original_context is not None and rerender_of is not None:
                original_context = PublicationContext.model_validate(original_context)
                if (
                    context.policy_version != original_context.policy_version
                    or context.verifier != original_context.verifier
                ):
                    raise StorageConflictError(
                        "historical rerender cannot change verification context"
                    )
                original = await self._read_research_publication(
                    conn, scope, root, rerender_of, original_context
                )
                if (
                    json.loads(original.document.data)["revision_digest"]
                    != pinned.document.digest
                ):
                    raise StorageConflictError("historical rerender revision changed")
            elif row["current_research_revision"] != revision:
                raise StorageConflictError(
                    "complete publication requires current revision"
                )
            if size > min(row["scope_free"], row["quota"] - row["charged"]):
                raise StorageConflictError("complete publication quota exhausted")
            await self._charge(conn, scope, root, size)
            op = await (
                await conn.execute(
                    "INSERT INTO research_staging.research_publication_operations(scope_id,root_id,revision_id,generation,context_digest,revision_digest,reserved,rerender_of) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING publication_id",
                    (
                        scope,
                        root,
                        revision,
                        generation,
                        digest,
                        pinned.document.digest,
                        size,
                        rerender_of,
                    ),
                )
            ).fetchone()
            await self._renew_staging(conn, scope, root)
            assert op is not None
            return op["publication_id"]

    async def commit_research_publication(
        self,
        scope: UUID,
        root: UUID,
        generation: int,
        revision: UUID,
        publication: UUID,
        raw: bytes,
        context: PublicationContext,
    ) -> UUID:
        # CPU-bound admission outside write locks; closure is rechecked below.
        pinned = await self.read_research(scope, root, revision)
        admitted = admit_research_publication(raw, pinned, publication, context)
        digest = context.digest()
        async with self._transaction() as conn:
            await self._require_complete_publications(conn)
            row = await self._lock(conn, scope, root)
            self._active(row, generation)
            op = await (
                await conn.execute(
                    "SELECT * FROM research_staging.research_publication_operations WHERE scope_id=%s AND root_id=%s AND publication_id=%s",
                    (scope, root, publication),
                )
            ).fetchone()
            if (
                row["revision_format"] != "research"
                or op is None
                or op["state"] == "cancelled"
                or op["generation"] != generation
                or op["revision_id"] != revision
                or op["context_digest"] != digest
                or op["revision_digest"] != pinned.document.digest
            ):
                raise StorageConflictError("complete publication operation unavailable")
            retained = (await self._research_history(conn, scope, root, revision))[-1]
            if retained != pinned:
                raise StorageConflictError(
                    "complete publication pinned history changed"
                )
            if op["state"] == "committed":
                if op["input_digest"] != admitted.document.digest:
                    raise StorageConflictError("complete publication replay changed")
                return publication
            if (
                op["rerender_of"] is None
                and row["current_research_revision"] != revision
            ):
                raise StorageConflictError(
                    "complete publication current revision changed"
                )
            if admitted.size > op["reserved"]:
                raise StorageConflictError("complete publication reservation exceeded")
            await conn.execute(
                "INSERT INTO research_staging.research_publications(scope_id,root_id,publication_id,revision_id,context_digest,revision_digest,payload,digest,summary,analysis,dossier) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    scope,
                    root,
                    publication,
                    revision,
                    digest,
                    pinned.document.digest,
                    admitted.document.data,
                    admitted.document.digest,
                    admitted.summary,
                    admitted.analysis,
                    admitted.dossier,
                ),
            )
            for source in retained.revision.research.verifications.structure.snapshots:
                await conn.execute(
                    "INSERT INTO research_staging.research_publication_sources(scope_id,root_id,publication_id,snapshot_id) VALUES (%s,%s,%s,%s)",
                    (scope, root, publication, UUID(source.snapshot_id)),
                )
            await self._charge(conn, scope, root, admitted.size - op["reserved"])
            await conn.execute(
                "UPDATE research_staging.research_publication_operations SET state='committed',input_digest=%s WHERE scope_id=%s AND root_id=%s AND publication_id=%s",
                (admitted.document.digest, scope, root, publication),
            )
            await conn.execute(
                "UPDATE research_staging.roots SET published_at=now(),expires_at=now()+interval '30 days' WHERE scope_id=%s AND root_id=%s",
                (scope, root),
            )
            return publication

    async def read_research_publication(
        self,
        scope: UUID,
        root: UUID,
        publication: UUID,
        context: PublicationContext,
    ) -> RetainedPublication:
        async with self._transaction(read=True) as conn:
            await self._require_complete_publications(conn)
            return await self._read_research_publication(
                conn, scope, root, publication, context
            )

    async def _read_research_publication(
        self,
        conn: Connection,
        scope: UUID,
        root: UUID,
        publication: UUID,
        context: PublicationContext,
    ) -> RetainedPublication:
        row = await (
            await conn.execute(
                "SELECT p.* FROM research_staging.research_publications p JOIN research_staging.roots r USING(scope_id,root_id) JOIN research_staging.research_publication_operations o USING(scope_id,root_id,publication_id) WHERE p.scope_id=%s AND p.root_id=%s AND p.publication_id=%s AND NOT r.deleted AND r.expires_at>now() AND r.revision_format='research' AND o.state='committed' AND o.input_digest=p.digest AND o.revision_digest=p.revision_digest AND o.context_digest=p.context_digest AND o.revision_id=p.revision_id",
                (scope, root, publication),
            )
        ).fetchone()
        if row is None or row["context_digest"] != context.digest():
            raise StorageConflictError("complete publication unavailable")
        pinned = (await self._research_history(conn, scope, root, row["revision_id"]))[
            -1
        ]
        result = admit_research_publication(
            row["payload"], pinned, publication, context
        )
        refs = await (
            await conn.execute(
                "SELECT snapshot_id FROM research_staging.research_publication_sources WHERE scope_id=%s AND root_id=%s AND publication_id=%s",
                (scope, root, publication),
            )
        ).fetchall()
        if (
            row["revision_digest"] != pinned.document.digest
            or row["payload"] != result.document.data
            or row["digest"] != result.document.digest
            or any(
                row[layer] != getattr(result, layer)
                for layer in ("summary", "analysis", "dossier")
            )
            or {str(item["snapshot_id"]) for item in refs}
            != {
                s.snapshot_id
                for s in pinned.revision.research.verifications.structure.snapshots
            }
        ):
            raise StorageConflictError("complete publication integrity mismatch")
        return result

    async def research_publication_receipt(
        self, scope: UUID, root: UUID, publication: UUID
    ) -> str | None:
        async with self._transaction(read=True) as conn:
            await self._require_complete_publications(conn)
            receipt = await (
                await conn.execute(
                    "SELECT input_digest FROM research_staging.research_publication_operations WHERE scope_id=%s AND root_id=%s AND publication_id=%s AND state='committed'",
                    (scope, root, publication),
                )
            ).fetchone()
            return receipt["input_digest"] if receipt else None

    async def cancel_research_publication(
        self, scope: UUID, root: UUID, publication: UUID
    ) -> None:
        async with self._transaction() as conn:
            await self._require_complete_publications(conn)
            await self._lock(conn, scope, root)
            pending = await (
                await conn.execute(
                    "UPDATE research_staging.research_publication_operations SET state='cancelled' WHERE scope_id=%s AND root_id=%s AND publication_id=%s AND state='pending' RETURNING reserved",
                    (scope, root, publication),
                )
            ).fetchone()
            if pending:
                await self._charge(conn, scope, root, -pending["reserved"])
