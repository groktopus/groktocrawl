"""PostgreSQL authority for complete experimental research artifact sets."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from .source_store import SourceStore, StorageConflictError

MAX_MANIFEST_BYTES = 1024 * 1024
MAX_ARTIFACT_BYTES = 10 * 1024 * 1024
MAX_SET_BYTES = 32 * 1024 * 1024
RETAINED_LAYERS = ("summary", "analysis", "dossier")


@dataclass(frozen=True)
class ArtifactMaterial:
    artifact_id: str
    layer: str
    body: bytes
    content_digest: str


@dataclass(frozen=True)
class RetainedArtifactSet:
    scope_id: UUID
    research_id: UUID
    run_id: UUID
    artifact_set_id: UUID
    manifest: bytes
    manifest_digest: str
    set_digest: str
    artifacts: tuple[ArtifactMaterial, ...]


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def artifact_set_digest(
    manifest_digest: str, artifacts: tuple[ArtifactMaterial, ...]
) -> str:
    material = [f"manifest:{manifest_digest}"]
    material.extend(
        f"{item.layer}:{item.artifact_id}:{item.content_digest}"
        for item in sorted(
            artifacts, key=lambda value: (value.layer, value.artifact_id)
        )
    )
    return _sha256("\n".join(material).encode("utf-8"))


class ArtifactLifecycleError(StorageConflictError):
    """A caller-scoped known root is unavailable under its current lifecycle."""

    def __init__(self, status_code: int):
        super().__init__("retained root unavailable")
        self.status_code = status_code


class ArtifactAuthority(SourceStore):
    """Atomic, scoped storage boundary; callers establish scope authorization."""

    async def migrate_artifact_authority(self) -> None:
        migration = (
            Path(__file__).with_name("migrations")
            / "013_research_artifact_authority.sql"
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
            if version != [{"version": 12}]:
                raise StorageConflictError(
                    "artifact authority migration requires schema 12"
                )
            await conn.execute(migration.read_text(), prepare=False)

    async def migrate_deletion_fence(self) -> None:
        migration = (
            Path(__file__).with_name("migrations") / "014_artifact_deletion_fence.sql"
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
            if version != [{"version": 13}]:
                raise StorageConflictError(
                    "artifact deletion migration requires schema 13"
                )
            await conn.execute(migration.read_text(), prepare=False)

    async def ensure_scope(self, scope: UUID) -> None:
        """Idempotently provision the server-derived experimental scope."""
        async with self._transaction() as conn:
            await conn.execute(
                "INSERT INTO research_staging.scopes(scope_id,quota) VALUES (%s,%s) ON CONFLICT (scope_id) DO NOTHING",
                (scope, 1024 * 1024 * 1024),
            )

    @staticmethod
    def _validate(
        manifest: bytes, artifacts: Mapping[str, tuple[str, bytes]]
    ) -> tuple[str, tuple[ArtifactMaterial, ...], str, int]:
        if (
            not isinstance(manifest, bytes)
            or not 0 < len(manifest) <= MAX_MANIFEST_BYTES
        ):
            raise ValueError("manifest byte limit exceeded")
        if set(artifacts) != set(RETAINED_LAYERS):
            raise ValueError("complete summary, analysis, and dossier set required")
        retained = []
        for layer in RETAINED_LAYERS:
            artifact_id, body = artifacts[layer]
            if not isinstance(artifact_id, str) or not 0 < len(artifact_id) <= 200:
                raise ValueError("invalid artifact identity")
            if not isinstance(body, bytes) or not 0 < len(body) <= MAX_ARTIFACT_BYTES:
                raise ValueError("artifact byte limit exceeded")
            retained.append(ArtifactMaterial(artifact_id, layer, body, _sha256(body)))
        values = tuple(retained)
        manifest_digest = _sha256(manifest)
        total = len(manifest) + sum(len(item.body) for item in values)
        if total > MAX_SET_BYTES:
            raise ValueError("artifact set byte limit exceeded")
        return (
            manifest_digest,
            values,
            artifact_set_digest(manifest_digest, values),
            total,
        )

    async def commit(
        self,
        scope: UUID,
        research: UUID,
        run: UUID,
        artifact_set: UUID,
        manifest: bytes,
        artifacts: Mapping[str, tuple[str, bytes]],
        evidence: Mapping[str, tuple[bytes, str]] | None = None,
        knowledge: bytes | None = None,
    ) -> RetainedArtifactSet:
        manifest_digest, retained, set_digest, total = self._validate(
            manifest, artifacts
        )
        evidence = evidence or {}
        objective = None
        if knowledge is not None:
            if not isinstance(knowledge, bytes) or len(knowledge) > MAX_MANIFEST_BYTES:
                raise ValueError("knowledge byte limit exceeded")
            payload = json.loads(knowledge)
            if not isinstance(payload, dict) or not isinstance(
                payload.get("context", {}), dict
            ):
                raise ValueError("invalid retained knowledge")
            objective = payload.get("context", {}).get("objective")
            if objective is not None and (
                not isinstance(objective, str) or len(objective) > 10_000
            ):
                raise ValueError("invalid retained objective")
        if knowledge is not None and len(knowledge) > MAX_MANIFEST_BYTES:
            raise ValueError("knowledge byte limit exceeded")
        evidence_total = len(knowledge or b"")
        for snapshot, (body, media_type) in evidence.items():
            if (
                not isinstance(snapshot, str)
                or not isinstance(body, bytes)
                or not 0 < len(snapshot) <= 200
                or len(body) > MAX_ARTIFACT_BYTES
                or media_type not in {"text/plain", "text/markdown"}
            ):
                raise ValueError("invalid retained evidence")
            body.decode("utf-8", errors="strict")
            evidence_total += len(body)
        if len(evidence) > 1000 or total + evidence_total > MAX_SET_BYTES:
            raise ValueError("artifact and evidence byte limit exceeded")
        total += evidence_total
        async with self._transaction() as conn:
            version = await (
                await conn.execute(
                    "SELECT version FROM research_staging.schema_version"
                )
            ).fetchall()
            if version not in ([{"version": 14}], [{"version": 15}], [{"version": 16}]):
                raise StorageConflictError("artifact authority schema unavailable")
            if (evidence or knowledge is not None) and version != [{"version": 16}]:
                raise StorageConflictError("evidence authority migration required")
            prior = await (
                await conn.execute(
                    "SELECT research_id,artifact_set_id,set_digest,deleted FROM research_staging.research_artifact_sets WHERE scope_id=%s AND (research_id=%s OR run_id=%s) FOR UPDATE",
                    (scope, research, run),
                )
            ).fetchone()
            if prior is not None:
                if prior["deleted"] or (
                    prior["research_id"],
                    prior["artifact_set_id"],
                    prior["set_digest"],
                ) != (research, artifact_set, set_digest):
                    raise StorageConflictError("artifact set replay changed")
                if evidence:
                    stored = await (
                        await conn.execute(
                            "SELECT snapshot_id,body,media_type FROM research_staging.research_artifact_evidence WHERE scope_id=%s AND research_id=%s",
                            (scope, research),
                        )
                    ).fetchall()
                    if {
                        row["snapshot_id"]: (bytes(row["body"]), row["media_type"])
                        for row in stored
                    } != dict(evidence):
                        raise StorageConflictError("evidence replay changed")
                if knowledge is not None:
                    existing = await (
                        await conn.execute(
                            "SELECT knowledge,knowledge_digest FROM research_staging.research_artifact_sets WHERE scope_id=%s AND research_id=%s",
                            (scope, research),
                        )
                    ).fetchone()
                    if (
                        existing is None
                        or existing["knowledge"] is None
                        or bytes(existing["knowledge"]) != knowledge
                        or existing["knowledge_digest"] != _sha256(knowledge)
                    ):
                        raise StorageConflictError("knowledge replay changed")
                return await self._read(conn, scope, research)
            await conn.execute(
                "INSERT INTO research_staging.research_artifact_sets(scope_id,research_id,run_id,artifact_set_id,manifest,manifest_digest,set_digest,total_bytes) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    scope,
                    research,
                    run,
                    artifact_set,
                    manifest,
                    manifest_digest,
                    set_digest,
                    total,
                ),
            )
            for item in retained:
                await conn.execute(
                    "INSERT INTO research_staging.research_artifacts(scope_id,research_id,artifact_id,layer,body,content_digest) VALUES (%s,%s,%s,%s,%s,%s)",
                    (
                        scope,
                        research,
                        item.artifact_id,
                        item.layer,
                        item.body,
                        item.content_digest,
                    ),
                )
            if knowledge is not None:
                await conn.execute(
                    "UPDATE research_staging.research_artifact_sets SET knowledge=%s,knowledge_digest=%s,objective=%s WHERE scope_id=%s AND research_id=%s",
                    (knowledge, _sha256(knowledge), objective, scope, research),
                )
            for snapshot, (body, media_type) in evidence.items():
                await conn.execute(
                    "INSERT INTO research_staging.research_artifact_evidence(scope_id,research_id,snapshot_id,body,content_digest,media_type) VALUES (%s,%s,%s,%s,%s,%s)",
                    (scope, research, snapshot, body, _sha256(body), media_type),
                )
            return RetainedArtifactSet(
                scope,
                research,
                run,
                artifact_set,
                manifest,
                manifest_digest,
                set_digest,
                retained,
            )

    async def _read(self, conn, scope: UUID, research: UUID) -> RetainedArtifactSet:
        row = await (
            await conn.execute(
                "SELECT * FROM research_staging.research_artifact_sets WHERE scope_id=%s AND research_id=%s AND NOT deleted AND expires_at>now()",
                (scope, research),
            )
        ).fetchone()
        if row is None:
            state = await (
                await conn.execute(
                    "SELECT deleted,expires_at>now() AS live FROM research_staging.research_artifact_sets WHERE scope_id=%s AND research_id=%s",
                    (scope, research),
                )
            ).fetchone()
            raise ArtifactLifecycleError(
                410
                if state is not None and (state["deleted"] or not state["live"])
                else 404
            )
        children = await (
            await conn.execute(
                "SELECT artifact_id,layer,body,content_digest FROM research_staging.research_artifacts WHERE scope_id=%s AND research_id=%s ORDER BY CASE layer WHEN 'summary' THEN 1 WHEN 'analysis' THEN 2 WHEN 'dossier' THEN 3 END,artifact_id",
                (scope, research),
            )
        ).fetchall()
        manifest = bytes(row["manifest"])
        artifacts = tuple(
            ArtifactMaterial(
                value["artifact_id"],
                value["layer"],
                bytes(value["body"]),
                value["content_digest"],
            )
            for value in children
        )
        if (
            {item.layer for item in artifacts} != set(RETAINED_LAYERS)
            or _sha256(manifest) != row["manifest_digest"]
            or any(_sha256(item.body) != item.content_digest for item in artifacts)
            or artifact_set_digest(row["manifest_digest"], artifacts)
            != row["set_digest"]
        ):
            raise StorageConflictError("artifact set integrity mismatch")
        return RetainedArtifactSet(
            scope,
            research,
            row["run_id"],
            row["artifact_set_id"],
            manifest,
            row["manifest_digest"],
            row["set_digest"],
            artifacts,
        )

    async def read(self, scope: UUID, research: UUID) -> RetainedArtifactSet:
        async with self._transaction(read=True) as conn:
            return await self._read(conn, scope, research)

    async def read_run(self, scope: UUID, run: UUID) -> RetainedArtifactSet:
        async with self._transaction(read=True) as conn:
            row = await (
                await conn.execute(
                    "SELECT research_id FROM research_staging.research_artifact_sets WHERE scope_id=%s AND run_id=%s AND NOT deleted AND expires_at>now()",
                    (scope, run),
                )
            ).fetchone()
            if row is None:
                raise StorageConflictError("artifact set unavailable")
            return await self._read(conn, scope, row["research_id"])

    async def find_run(self, scope: UUID, run: UUID) -> RetainedArtifactSet | None:
        """Return a committed set for reconciliation, or None when absent/deleted."""
        async with self._transaction(read=True) as conn:
            row = await (
                await conn.execute(
                    "SELECT research_id FROM research_staging.research_artifact_sets WHERE scope_id=%s AND run_id=%s AND NOT deleted AND expires_at>now()",
                    (scope, run),
                )
            ).fetchone()
            if row is None:
                return None
            return await self._read(conn, scope, row["research_id"])

    async def delete(self, scope: UUID, research: UUID) -> None:
        async with self._transaction() as conn:
            row = await (
                await conn.execute(
                    "SELECT deleted FROM research_staging.research_artifact_sets WHERE scope_id=%s AND research_id=%s FOR UPDATE",
                    (scope, research),
                )
            ).fetchone()
            if row is None:
                await conn.execute(
                    "INSERT INTO research_staging.research_artifact_sets(scope_id,research_id,manifest,manifest_digest,set_digest,total_bytes,deleted) VALUES (%s,%s,NULL,%s,%s,0,true)",
                    (scope, research, "0" * 64, "0" * 64),
                )
                return
            if row["deleted"]:
                return
            await conn.execute(
                "UPDATE research_staging.research_artifact_sets SET deleted=true,manifest=NULL WHERE scope_id=%s AND research_id=%s",
                (scope, research),
            )
            version = await (
                await conn.execute(
                    "SELECT version FROM research_staging.schema_version"
                )
            ).fetchall()
            if version == [{"version": 16}]:
                await conn.execute(
                    "UPDATE research_staging.research_artifact_sets SET knowledge=NULL,knowledge_digest=NULL,objective=NULL WHERE scope_id=%s AND research_id=%s",
                    (scope, research),
                )
                await conn.execute(
                    "DELETE FROM research_staging.research_artifact_evidence WHERE scope_id=%s AND research_id=%s",
                    (scope, research),
                )
            await conn.execute(
                "DELETE FROM research_staging.research_artifacts WHERE scope_id=%s AND research_id=%s",
                (scope, research),
            )

    async def read_evidence(
        self, scope: UUID, research: UUID, snapshot: str
    ) -> tuple[bytes, str]:
        """Read exact bytes within the artifact set's scope and retention fence."""
        async with self._transaction() as conn:
            await self._read(conn, scope, research)
            version = await (
                await conn.execute(
                    "SELECT version FROM research_staging.schema_version"
                )
            ).fetchall()
            if version != [{"version": 16}]:
                raise StorageConflictError("evidence authority migration required")
            row = await (
                await conn.execute(
                    "SELECT e.body,e.content_digest,e.media_type FROM research_staging.research_artifact_evidence e JOIN research_staging.research_artifact_sets s USING(scope_id,research_id) WHERE e.scope_id=%s AND e.research_id=%s AND e.snapshot_id=%s AND NOT s.deleted AND s.expires_at>now()",
                    (scope, research, snapshot),
                )
            ).fetchone()
            if row is None or _sha256(bytes(row["body"])) != row["content_digest"]:
                raise StorageConflictError("evidence unavailable")
            return bytes(row["body"]), row["media_type"]

    async def migrate_evidence_authority(self) -> None:
        async with self._transaction(bootstrap=True) as conn:
            await conn.execute(
                "LOCK TABLE research_staging.schema_version IN ACCESS EXCLUSIVE MODE"
            )
            version = await (
                await conn.execute(
                    "SELECT version FROM research_staging.schema_version"
                )
            ).fetchall()
            if version != [{"version": 15}]:
                raise StorageConflictError("evidence migration requires schema 15")
            await conn.execute(
                (
                    Path(__file__).with_name("migrations") / "016_artifact_evidence.sql"
                ).read_text(),
                prepare=False,
            )

    async def read_knowledge(self, scope: UUID, research: UUID) -> bytes:
        async with self._transaction() as conn:
            await self._read(conn, scope, research)
            version = await (
                await conn.execute(
                    "SELECT version FROM research_staging.schema_version"
                )
            ).fetchall()
            if version != [{"version": 16}]:
                raise StorageConflictError("evidence authority migration required")
            row = await (
                await conn.execute(
                    "SELECT knowledge,knowledge_digest FROM research_staging.research_artifact_sets WHERE scope_id=%s AND research_id=%s",
                    (scope, research),
                )
            ).fetchone()
            if (
                row is None
                or row["knowledge"] is None
                or _sha256(bytes(row["knowledge"])) != row["knowledge_digest"]
            ):
                raise StorageConflictError("retained knowledge unavailable")
            return bytes(row["knowledge"])

    async def retention(self, scope: UUID, research: UUID) -> dict[str, Any]:
        async with self._transaction(read=True) as conn:
            row = await (
                await conn.execute(
                    "SELECT deleted,expires_at,expires_at>now() AS live FROM research_staging.research_artifact_sets WHERE scope_id=%s AND research_id=%s",
                    (scope, research),
                )
            ).fetchone()
            if row is None:
                raise StorageConflictError("artifact set unavailable")
            return {
                "deleted": row["deleted"],
                "live": row["live"],
                "expires_at": row["expires_at"].isoformat(),
            }

    async def ensure_evidence_schema(self) -> None:
        async with self._transaction(read=True) as conn:
            version = await (
                await conn.execute(
                    "SELECT version FROM research_staging.schema_version"
                )
            ).fetchall()
            if version != [{"version": 16}]:
                raise StorageConflictError("evidence authority migration required")

    async def list_retained(
        self, scope: UUID, *, limit: int = 100, offset: int = 0
    ) -> list[dict[str, Any]]:
        if not 1 <= limit <= 101 or not 0 <= offset <= 100_000:
            raise ValueError("invalid authority page")
        async with self._transaction(read=True) as conn:
            version = await (
                await conn.execute(
                    "SELECT version FROM research_staging.schema_version"
                )
            ).fetchall()
            objective_column = (
                "objective" if version == [{"version": 16}] else "NULL AS objective"
            )
            return await (
                await conn.execute(
                    f"SELECT research_id,run_id,artifact_set_id,set_digest,expires_at,{objective_column} FROM research_staging.research_artifact_sets WHERE scope_id=%s AND NOT deleted AND expires_at>now() ORDER BY run_id LIMIT %s OFFSET %s",
                    (scope, limit, offset),
                )
            ).fetchall()

    async def lookup(
        self,
        scope: UUID,
        *,
        run: UUID | None = None,
        artifact_set: UUID | None = None,
        artifact_id: str | None = None,
    ) -> RetainedArtifactSet:
        if sum(value is not None for value in (run, artifact_set, artifact_id)) != 1:
            raise ValueError("one retained identity is required")
        async with self._transaction(read=True) as conn:
            if artifact_id is not None:
                row = await (
                    await conn.execute(
                        "SELECT research_id FROM research_staging.research_artifacts WHERE scope_id=%s AND artifact_id=%s",
                        (scope, artifact_id),
                    )
                ).fetchone()
            else:
                column = "run_id" if run is not None else "artifact_set_id"
                row = await (
                    await conn.execute(
                        f"SELECT research_id FROM research_staging.research_artifact_sets WHERE scope_id=%s AND {column}=%s",
                        (scope, run if run is not None else artifact_set),
                    )
                ).fetchone()
            if row is None:
                raise ArtifactLifecycleError(404)
            return await self._read(conn, scope, row["research_id"])
