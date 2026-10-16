"""Complete bundles using the shared trusted-server import lifecycle."""

from pathlib import Path
from uuid import UUID

from .canonical import CanonicalDocument
from .publication_store import PublicationContext
from .research_bundle import (
    RESEARCH_BUNDLE_SCHEMA,
    ResearchBundleStore,
    admit_research_bundle,
)
from .source_store import Connection, StorageConflictError


class ResearchImportStore(ResearchBundleStore):
    bundle_schema = RESEARCH_BUNDLE_SCHEMA
    admit_import_bundle = staticmethod(admit_research_bundle)

    async def migrate_research_imports(self) -> None:
        migration = (
            Path(__file__).with_name("migrations") / "009_complete_research_imports.sql"
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
            if current != [{"version": 8}]:
                raise StorageConflictError(
                    "complete import migration requires schema 8"
                )
            await conn.execute(migration.read_text(), prepare=False)

    @staticmethod
    async def _require_import_schema(conn: Connection) -> int:
        current = await (
            await conn.execute("SELECT version FROM research_staging.schema_version")
        ).fetchall()
        if current not in (
            [{"version": 9}],
            [{"version": 10}],
            [{"version": 11}],
            [{"version": 12}],
        ):
            raise StorageConflictError("complete import schema unavailable")
        return current[0]["version"]

    async def _export_import_origin(
        self,
        conn: Connection,
        scope: UUID,
        root: UUID,
        publication: UUID,
        context: PublicationContext,
    ) -> CanonicalDocument:
        return await self._export_research_publication(
            conn, scope, root, publication, context
        )
