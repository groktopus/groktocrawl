"""Bounded internal storage for ADR-0082 SlopSearX provenance references."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from .slopsearx_provenance import ProvenanceReference, reference_document
from .source_store import SourceStore, StorageConflictError

MAX_REFERENCES_PER_RESEARCH = 100
class SlopSearXProvenanceStore(SourceStore):
    """Stores references beside a retained artifact set and shares its lifetime."""

    async def migrate(self) -> None:
        migration = (
            Path(__file__).with_name("migrations")
            / "015_slopsearx_provenance_references.sql"
        )
        async with self._transaction(bootstrap=True) as conn:
            await conn.execute(
                "LOCK TABLE research_staging.schema_version IN ACCESS EXCLUSIVE MODE"
            )
            version = await (
                await conn.execute("SELECT version FROM research_staging.schema_version")
            ).fetchall()
            if version != [{"version": 14}]:
                raise StorageConflictError("provenance migration requires schema 14")
            await conn.execute(migration.read_text(), prepare=False)

    async def put(
        self,
        scope: UUID,
        research: UUID,
        reference: ProvenanceReference,
        *,
        remote_expires_at: datetime,
    ) -> str:
        raw, digest = reference_document(reference)
        if remote_expires_at.tzinfo is None:
            raise ValueError("remote expiry must include a timezone")
        async with self._transaction() as conn:
            owner = await (
                await conn.execute(
                    "SELECT deleted FROM research_staging.research_artifact_sets WHERE scope_id=%s AND research_id=%s FOR UPDATE",
                    (scope, research),
                )
            ).fetchone()
            if owner is None or owner["deleted"]:
                raise StorageConflictError("artifact set unavailable")
            prior = await (
                await conn.execute(
                    "SELECT reference_digest FROM research_staging.slopsearx_provenance_references WHERE scope_id=%s AND research_id=%s AND result_id=%s",
                    (scope, research, reference.result_id),
                )
            ).fetchone()
            if prior is not None:
                if prior["reference_digest"] != digest:
                    raise StorageConflictError("provenance replay changed")
                return digest
            count = await (
                await conn.execute(
                    "SELECT count(*) AS value FROM research_staging.slopsearx_provenance_references WHERE scope_id=%s AND research_id=%s",
                    (scope, research),
                )
            ).fetchone()
            if count is None or count["value"] >= MAX_REFERENCES_PER_RESEARCH:
                raise StorageConflictError("provenance reference capacity exhausted")
            await conn.execute(
                "INSERT INTO research_staging.slopsearx_provenance_references(scope_id,research_id,result_id,reference,reference_digest,remote_expires_at) VALUES (%s,%s,%s,%s::jsonb,%s,%s)",
                (
                    scope,
                    research,
                    reference.result_id,
                    raw.decode(),
                    digest,
                    remote_expires_at,
                ),
            )
            return digest

    async def list(self, scope: UUID, research: UUID) -> list[dict[str, Any]]:
        async with self._transaction(read=True) as conn:
            rows = await (
                await conn.execute(
                    "SELECT reference,reference_digest,remote_expires_at FROM research_staging.slopsearx_provenance_references WHERE scope_id=%s AND research_id=%s ORDER BY result_id",
                    (scope, research),
                )
            ).fetchall()
            return [
                {
                    "reference": row["reference"],
                    "reference_digest": row["reference_digest"],
                    "remote_expires_at": row["remote_expires_at"],
                }
                for row in rows
            ]
