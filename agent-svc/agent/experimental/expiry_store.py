"""Explicit bounded expiry collection, with no scheduler or execution recovery."""

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from uuid import UUID

from .import_store import ImportStore
from .source_store import Connection, StorageConflictError


@dataclass(frozen=True)
class CollectionOutcome:
    root_id: UUID
    status: Literal["purged", "skipped"]
    purged_roots: int


class ExpiryStore(ImportStore):
    async def migrate_expiry(self) -> None:
        sql = (
            Path(__file__)
            .with_name("migrations")
            .joinpath("006_expiry_collection.sql")
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
            if version != [{"version": 5}]:
                raise StorageConflictError("migration requires schema 5")
            await conn.execute(sql, prepare=False)

    @staticmethod
    async def _require_expiry_schema(conn: Connection) -> int:
        version = await (
            await conn.execute("SELECT version FROM research_staging.schema_version")
        ).fetchall()
        if version not in (
            [{"version": 6}],
            [{"version": 7}],
            [{"version": 8}],
            [{"version": 9}],
            [{"version": 10}],
            [{"version": 11}],
            [{"version": 12}],
        ):
            raise StorageConflictError("expiry schema unavailable")
        return int(version[0]["version"])

    async def _expiry_candidates(self, scope: UUID, limit: int) -> tuple[UUID, ...]:
        async with self._transaction(read=True) as conn:
            await self._require_expiry_schema(conn)
            exists = await (
                await conn.execute(
                    "SELECT 1 FROM research_staging.scopes WHERE scope_id=%s", (scope,)
                )
            ).fetchone()
            if exists is None:
                raise StorageConflictError("scope unavailable")
            rows = await (
                await conn.execute(
                    "SELECT root_id FROM research_staging.roots WHERE scope_id=%s AND NOT deleted AND expires_at<=now() ORDER BY expires_at,root_id LIMIT %s",
                    (scope, limit),
                )
            ).fetchall()
            return tuple(row["root_id"] for row in rows)

    async def _collect_candidate(self, scope: UUID, root: UUID) -> CollectionOutcome:
        async with self._transaction() as conn:
            version = await self._require_expiry_schema(conn)
            await self._coordinate_imports(conn)
            rows = await self._lock_purge_roots(conn, scope, root)
            candidate = rows[(scope, root)]
            # Re-evaluate after all locks, using database time rather than a stale hint.
            expired = await (
                await conn.execute(
                    "SELECT expires_at<=clock_timestamp() AS expired FROM research_staging.roots WHERE scope_id=%s AND root_id=%s",
                    (scope, root),
                )
            ).fetchone()
            if candidate["deleted"] or expired is None or not expired["expired"]:
                return CollectionOutcome(root, "skipped", 0)
            purged = 0
            for (target_scope, target_root), row in rows.items():
                if not row["deleted"]:
                    await self._purge_root(
                        conn, target_scope, target_root, row, version
                    )
                    purged += 1
            return CollectionOutcome(root, "purged", purged)

    async def collect_expired(
        self, scope: UUID, limit: int = 20
    ) -> tuple[CollectionOutcome, ...]:
        """Trusted caller authorizes scope. Earlier root commits survive later failures."""
        if type(limit) is not int or not 1 <= limit <= 20:
            raise ValueError("candidate limit must be an integer from 1 to 20")
        try:
            async with asyncio.timeout(60):
                candidates = await self._expiry_candidates(scope, limit)
                outcomes = []
                for root in candidates:
                    outcomes.append(await self._collect_candidate(scope, root))
                return tuple(outcomes)
        except (Exception, asyncio.CancelledError) as exc:
            exc.add_note(
                "Expiry collection may have committed earlier candidates; inspect lifecycle/receipts or repeat an explicit bounded pass."
            )
            raise
