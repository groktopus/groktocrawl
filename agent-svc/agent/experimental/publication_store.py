"""Atomic retained fixture outputs. Fixture verdicts are not authenticated truth."""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from uuid import UUID

from .canonical import MAX_BYTES, CanonicalDocument, admit_canonical_json
from .knowledge import Identity, KnowledgeStructure, Record
from .publication import FixtureResearch, validate_fixture_publication
from .revision_store import RevisionStore
from .source_store import Connection, StorageConflictError
from .verification import FixtureVerifier, validate_fixture_verifications

PUBLICATION_SCHEMA = "retained-fixture-publication/1"


class PublicationContext(Record):
    schema_version: Literal["fixture-publication-context/1"] = (
        "fixture-publication-context/1"
    )
    policy_version: Identity
    verifier: FixtureVerifier
    renderer_version: Identity
    auditor: FixtureVerifier

    def digest(self) -> str:
        checked = PublicationContext.model_validate(self)
        return admit_canonical_json(
            checked.model_dump_json().encode(), schema_version=self.schema_version
        ).digest


@dataclass(frozen=True)
class RetainedPublication:
    document: CanonicalDocument
    summary: bytes
    analysis: bytes
    dossier: bytes

    @property
    def size(self) -> int:
        return sum(
            map(len, (self.document.data, self.summary, self.analysis, self.dossier))
        )


def admit_publication(
    raw: bytes,
    structure: KnowledgeStructure,
    publication_id: UUID,
    context: PublicationContext,
) -> RetainedPublication:
    context = PublicationContext.model_validate(context)
    document = admit_canonical_json(raw, schema_version=PUBLICATION_SCHEMA)
    fields = json.loads(document.data)
    if (
        set(fields) != {"schema_version", "revision_id", "research", "publication"}
        or fields["revision_id"] != structure.revision_id
    ):
        raise ValueError("publication differs from pinned revision")
    research = FixtureResearch.model_validate(fields["research"])
    validate_fixture_verifications(
        research.verifications,
        structure=structure,
        policy_version=context.policy_version,
        verifier=context.verifier,
    )
    audits = validate_fixture_publication(
        fields["publication"],
        research=research,
        artifact_set_id=str(publication_id),
        renderer_version=context.renderer_version,
        auditor=context.auditor,
    )
    outputs = {
        audit.checked_input.artifact.layer: audit.checked_input.rendered_text().encode(
            "utf-8"
        )
        for audit in audits.audits
    }
    result = RetainedPublication(
        document, outputs["summary"], outputs["analysis"], outputs["dossier"]
    )
    if result.size > MAX_BYTES:
        raise ValueError("publication total byte limit exceeded")
    return result


def research_digest(document: CanonicalDocument) -> str:
    """Pin all historical assessments, questions, dates and verification records."""
    return admit_canonical_json(
        json.dumps(
            {
                "schema_version": "retained-rerender-research/1",
                "research": json.loads(document.data)["research"],
            }
        ).encode(),
        schema_version="retained-rerender-research/1",
    ).digest


class PublicationStore(RevisionStore):
    async def migrate_publications(self) -> None:
        sql = (
            Path(__file__)
            .with_name("migrations")
            .joinpath("003_fixture_publications.sql")
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
            if version != [{"version": 2}]:
                raise StorageConflictError("migration requires schema 2")
            await conn.execute(sql, prepare=False)

    async def migrate_rerenders(self) -> None:
        sql = (
            Path(__file__)
            .with_name("migrations")
            .joinpath("004_historical_rerender.sql")
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
            if version != [{"version": 3}]:
                raise StorageConflictError("migration requires schema 3")
            await conn.execute(sql, prepare=False)

    @staticmethod
    async def _require_publication_schema(conn: Connection) -> None:
        version = await (
            await conn.execute("SELECT version FROM research_staging.schema_version")
        ).fetchall()
        if version not in (
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
            raise StorageConflictError("publication schema unavailable")

    async def reserve_publication(
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
        if (rerender_of is None) != (original_context is None):
            raise ValueError(
                "rerender requires original publication and trusted context"
            )
        if type(size) is not int or not 0 < size <= MAX_BYTES:
            raise ValueError("invalid publication reservation")
        async with self._transaction() as conn:
            await self._require_publication_schema(conn)
            row = await self._lock(conn, scope, root)
            self._active(row, generation)
            pinned_research = None
            if rerender_of is not None and original_context is not None:
                version = await (
                    await conn.execute(
                        "SELECT version FROM research_staging.schema_version"
                    )
                ).fetchall()
                if version not in (
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
                    raise StorageConflictError("rerender schema unavailable")
                original_context = PublicationContext.model_validate(original_context)
                if (
                    context.policy_version != original_context.policy_version
                    or context.verifier != original_context.verifier
                ):
                    raise StorageConflictError(
                        "rerender cannot change verification context"
                    )
                original = await self._read_publication(
                    conn, scope, root, rerender_of, original_context
                )
                if json.loads(original.document.data)["revision_id"] != str(revision):
                    raise StorageConflictError(
                        "rerender differs from original revision"
                    )
                pinned_research = research_digest(original.document)
            elif row["current_revision"] != revision:
                raise StorageConflictError("publication requires current revision")
            if size > min(row["scope_free"], row["quota"] - row["charged"]):
                raise StorageConflictError("quota exhausted")
            await self._charge(conn, scope, root, size)
            result = await (
                await conn.execute(
                    "INSERT INTO research_staging.publication_operations(scope_id,root_id,revision_id,generation,context_digest,reserved) VALUES (%s,%s,%s,%s,%s,%s) RETURNING publication_id",
                    (scope, root, revision, generation, digest, size),
                )
            ).fetchone()
            await self._renew_staging(conn, scope, root)
            assert result is not None
            if rerender_of is not None:
                await conn.execute(
                    "UPDATE research_staging.publication_operations SET rerender_of=%s,research_digest=%s WHERE scope_id=%s AND root_id=%s AND publication_id=%s",
                    (
                        rerender_of,
                        pinned_research,
                        scope,
                        root,
                        result["publication_id"],
                    ),
                )
            return result["publication_id"]

    async def commit_publication(
        self,
        scope: UUID,
        root: UUID,
        generation: int,
        revision: UUID,
        publication: UUID,
        raw: bytes,
        context: PublicationContext,
    ) -> UUID:
        # Expensive fixture/representation checks run outside write locks.
        pinned = await self.read_revision(scope, root, revision)
        admitted = admit_publication(raw, pinned.structure, publication, context)
        context_digest = context.digest()
        async with self._transaction() as conn:
            await self._require_publication_schema(conn)
            row = await self._lock(conn, scope, root)
            self._active(row, generation)
            operation = await (
                await conn.execute(
                    "SELECT * FROM research_staging.publication_operations WHERE scope_id=%s AND root_id=%s AND publication_id=%s",
                    (scope, root, publication),
                )
            ).fetchone()
            if (
                operation is None
                or operation["state"] == "cancelled"
                or operation["revision_id"] != revision
                or operation["generation"] != generation
                or operation["context_digest"] != context_digest
            ):
                raise StorageConflictError("publication operation unavailable")
            if operation["state"] == "committed":
                if operation["input_digest"] != admitted.document.digest:
                    raise StorageConflictError("publication input changed")
                return publication
            if operation.get("research_digest") is not None:
                if research_digest(admitted.document) != operation["research_digest"]:
                    raise StorageConflictError("rerender research changed")
            elif row["current_revision"] != revision:
                raise StorageConflictError("publication requires current revision")
            if admitted.size > operation["reserved"]:
                raise StorageConflictError("publication reservation exceeded")
            # Re-read in the locked transaction: no cross-transaction source closure.
            retained = await self._read_revision(conn, scope, root, revision)
            if retained.document != pinned.document:
                raise StorageConflictError("pinned revision changed")
            await conn.execute(
                "INSERT INTO research_staging.publications(scope_id,root_id,publication_id,revision_id,context_digest,payload,digest,summary,analysis,dossier) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    scope,
                    root,
                    publication,
                    revision,
                    context_digest,
                    admitted.document.data,
                    admitted.document.digest,
                    admitted.summary,
                    admitted.analysis,
                    admitted.dossier,
                ),
            )
            for source in retained.structure.snapshots:
                await conn.execute(
                    "INSERT INTO research_staging.publication_sources(scope_id,root_id,publication_id,snapshot_id) VALUES (%s,%s,%s,%s)",
                    (scope, root, publication, UUID(source.snapshot_id)),
                )
            await self._charge(conn, scope, root, admitted.size - operation["reserved"])
            await conn.execute(
                "UPDATE research_staging.publication_operations SET state='committed',input_digest=%s WHERE scope_id=%s AND root_id=%s AND publication_id=%s",
                (admitted.document.digest, scope, root, publication),
            )
            await conn.execute(
                "UPDATE research_staging.roots SET published_at=now(),expires_at=now()+interval '30 days' WHERE scope_id=%s AND root_id=%s",
                (scope, root),
            )
            return publication

    async def read_publication(
        self, scope: UUID, root: UUID, publication: UUID, context: PublicationContext
    ) -> RetainedPublication:
        async with self._transaction(read=True) as conn:
            await self._require_publication_schema(conn)
            return await self._read_publication(conn, scope, root, publication, context)

    async def _read_publication(
        self,
        conn: Connection,
        scope: UUID,
        root: UUID,
        publication: UUID,
        context: PublicationContext,
    ) -> RetainedPublication:
        context_digest = context.digest()
        row = await (
            await conn.execute(
                "SELECT p.* FROM research_staging.publications p JOIN research_staging.roots r USING(scope_id,root_id) WHERE p.scope_id=%s AND p.root_id=%s AND p.publication_id=%s AND NOT r.deleted AND r.expires_at>now()",
                (scope, root, publication),
            )
        ).fetchone()
        if row is None or row["context_digest"] != context_digest:
            raise StorageConflictError("publication unavailable")
        retained = await self._read_revision(conn, scope, root, row["revision_id"])
        admitted = admit_publication(
            row["payload"], retained.structure, publication, context
        )
        if (
            admitted.document.data != row["payload"]
            or admitted.document.digest != row["digest"]
            or any(
                getattr(admitted, layer) != row[layer]
                for layer in ("summary", "analysis", "dossier")
            )
        ):
            raise StorageConflictError("publication integrity mismatch")
        refs = await (
            await conn.execute(
                "SELECT snapshot_id FROM research_staging.publication_sources WHERE scope_id=%s AND root_id=%s AND publication_id=%s",
                (scope, root, publication),
            )
        ).fetchall()
        if {str(r["snapshot_id"]) for r in refs} != {
            s.snapshot_id for s in retained.structure.snapshots
        }:
            raise StorageConflictError("publication ledger mismatch")
        return admitted

    async def publication_receipt(
        self, scope: UUID, root: UUID, publication: UUID
    ) -> str | None:
        async with self._transaction(read=True) as conn:
            await self._require_publication_schema(conn)
            row = await (
                await conn.execute(
                    "SELECT input_digest FROM research_staging.publication_operations WHERE scope_id=%s AND root_id=%s AND publication_id=%s AND state='committed'",
                    (scope, root, publication),
                )
            ).fetchone()
            return row["input_digest"] if row else None

    async def cancel_publication(
        self, scope: UUID, root: UUID, publication: UUID
    ) -> None:
        async with self._transaction() as conn:
            await self._require_publication_schema(conn)
            await self._lock(conn, scope, root)
            row = await (
                await conn.execute(
                    "SELECT reserved FROM research_staging.publication_operations WHERE scope_id=%s AND root_id=%s AND publication_id=%s AND state='pending'",
                    (scope, root, publication),
                )
            ).fetchone()
            if row:
                await self._charge(conn, scope, root, -row["reserved"])
                await conn.execute(
                    "UPDATE research_staging.publication_operations SET state='cancelled' WHERE scope_id=%s AND root_id=%s AND publication_id=%s",
                    (scope, root, publication),
                )
