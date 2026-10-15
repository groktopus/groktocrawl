"""Schema-10 consolidated journey transactions and inherited-reader regressions."""

import asyncio
import unittest
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import uuid4

import test_research_import_db as legacy
from agent.experimental.canonical import admit_canonical_json
from agent.experimental.consolidated_bundle import (
    CONSOLIDATED_BUNDLE_SCHEMA,
    admit_consolidated_bundle,
)
from agent.experimental.consolidated_example import (
    BODIES,
    example_context,
    example_journey,
)
from agent.experimental.consolidated_store import ConsolidatedStore
from agent.experimental.context_sources import ResolvedContextSource
from agent.experimental.source_store import StorageConflictError


class OldLifecycleTests(legacy.OldLifecycleTests):
    pass


class CompleteHistoryTests(legacy.CompleteHistoryTests):
    pass


class PublicationTests(legacy.PublicationTests):
    pass


class ExportTests(legacy.ExportTests):
    pass


class CompleteImportTests(legacy.CompleteImportTests):
    pass


async def stage_journey(
    store, scope, root, operation, *, before=None, commit_store=None
):
    bindings = {}
    callbacks = {}
    for snapshot, body in zip(example_context().snapshots, BODIES, strict=True):

        async def acquire(snapshot=snapshot, body=body):
            reservation = await store.reserve(scope, root, 1, 1000)
            identity = await store.commit_source(
                scope, root, 1, reservation, body.encode(), snapshot.canonical_url
            )
            bindings[snapshot.snapshot_id] = identity
            return ResolvedContextSource(
                snapshot.content_ref, body.encode(), "utf8-exact/1", "text/plain"
            )

        callbacks[snapshot.snapshot_id] = acquire

    async def commit(result, knowledge_owner, render_owner):
        if before is not None:
            await before(result, knowledge_owner, render_owner)
        await (commit_store or store).commit_consolidated(
            scope, root, 1, operation, result, bindings, knowledge_owner, render_owner
        )

    return await example_journey(
        acquisitions=callbacks, commit=commit, timeout_seconds=120
    ).run()


class ConsolidatedTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = ConsolidatedStore()
        self.scope = uuid4()
        await self.store.provision_scope(self.scope)
        self.root = await self.store.create_consolidated_root(self.scope)
        self.op = await self.store.reserve_consolidated(
            self.scope, self.root, 1, 100000, example_context()
        )

    async def sql(self, query, args=()):
        async with self.store._transaction() as conn:
            return await (await conn.execute(query, args)).fetchall()

    async def run_journey(self, **kwargs):
        return await stage_journey(self.store, self.scope, self.root, self.op, **kwargs)

    async def test_exact_roundtrip_and_fixture_receipt(self):
        result = await self.run_journey()
        read = await self.store.read_consolidated(self.scope, self.root, self.op)
        self.assertEqual(read.knowledge_bytes, result.knowledge_bytes)
        self.assertEqual(read.manifest_bytes, result.manifest_bytes)
        self.assertEqual(read.reports, result.reports)
        self.assertEqual(read.sources, result.sources)
        self.assertTrue(read.fixture_only)
        self.assertEqual(
            await self.store.consolidated_receipt(self.scope, self.root, self.op),
            read.receipt_digest,
        )
        row = (
            await self.sql(
                "SELECT charged,expires_at-published_at AS retained FROM research_staging.roots WHERE root_id=%s",
                (self.root,),
            )
        )[0]
        expected = (
            sum(len(s.body) for s in result.sources)
            + len(result.knowledge_bytes)
            + len(result.manifest_bytes)
            + sum(len(r.body) for r in result.reports)
        )
        self.assertGreater(
            row["charged"], expected
        )  # staged source descriptor bytes are also charged
        self.assertEqual(row["retained"].days, 30)
        with self.assertRaises(StorageConflictError):
            await self.store.reserve_consolidated(
                self.scope, self.root, 1, 100000, example_context()
            )

    async def test_export_round_trip_preserves_exact_consolidated_material(self):
        await self.run_journey()
        bundle = await self.store.export_consolidated(
            self.scope, self.root, self.op
        )
        self.assertEqual(
            admit_canonical_json(
                bundle.data, schema_version=CONSOLIDATED_BUNDLE_SCHEMA
            ).data,
            bundle.data,
        )
        self.assertEqual(
            (
                await admit_consolidated_bundle(
                    bundle.data,
                    expected_digest=bundle.digest,
                    scope=self.scope,
                    root=self.root,
                    operation=self.op,
                    now=datetime.now(UTC),
                )
            ).data,
            bundle.data,
        )

    async def test_cancel_releases_only_pending_reservation(self):
        await self.store.cancel_consolidated(self.scope, self.root, self.op)
        await self.store.cancel_consolidated(self.scope, self.root, self.op)
        row = (
            await self.sql(
                "SELECT charged FROM research_staging.roots WHERE root_id=%s",
                (self.root,),
            )
        )[0]
        self.assertEqual(row["charged"], 0)
        with self.assertRaises(StorageConflictError):
            await self.run_journey()

    async def test_delete_purges_bytes_and_preserves_metadata_receipt(self):
        await self.run_journey()
        digest = await self.store.consolidated_receipt(self.scope, self.root, self.op)
        await self.store.delete_root(self.scope, self.root)
        with self.assertRaises(StorageConflictError):
            await self.store.read_consolidated(self.scope, self.root, self.op)
        self.assertEqual(
            await self.store.consolidated_receipt(self.scope, self.root, self.op),
            digest,
        )
        rows = await self.sql(
            "SELECT * FROM research_staging.consolidated_publications WHERE root_id=%s",
            (self.root,),
        )
        self.assertEqual(rows, [])
        self.assertEqual(
            (
                await self.sql(
                    "SELECT charged FROM research_staging.roots WHERE root_id=%s",
                    (self.root,),
                )
            )[0]["charged"],
            0,
        )

    async def test_deletion_between_execution_and_commit_blocks_write(self):
        async def before(*_args):
            await self.store.delete_root(self.scope, self.root)

        with self.assertRaises(StorageConflictError):
            await self.run_journey(before=before)
        self.assertIsNone(
            await self.store.consolidated_receipt(self.scope, self.root, self.op)
        )

    async def test_expired_root_cannot_publish(self):
        async def before(*_args):
            await self.sql(
                "UPDATE research_staging.roots SET expires_at=now()-interval '1 second' WHERE root_id=%s RETURNING root_id",
                (self.root,),
            )

        with self.assertRaises(StorageConflictError):
            await self.run_journey(before=before)

    async def test_generation_change_blocks_commit(self):
        async def before(*_args):
            await self.sql(
                "UPDATE research_staging.roots SET generation=generation+1 WHERE root_id=%s RETURNING root_id",
                (self.root,),
            )

        with self.assertRaises(StorageConflictError):
            await self.run_journey(before=before)

    async def test_closed_executor_cannot_publish(self):
        async def before(_result, knowledge, _render):
            knowledge.close()

        with self.assertRaises(ValueError):
            await self.run_journey(before=before)
        self.assertIsNone(
            await self.store.consolidated_receipt(self.scope, self.root, self.op)
        )

    async def test_scope_isolation(self):
        await self.run_journey()
        other = uuid4()
        await self.store.provision_scope(other)
        with self.assertRaises(StorageConflictError):
            await self.store.read_consolidated(other, self.root, self.op)
        self.assertIsNone(
            await self.store.consolidated_receipt(other, self.root, self.op)
        )

    async def test_report_corruption_fails_read(self):
        await self.run_journey()
        await self.sql(
            "UPDATE research_staging.consolidated_publications SET summary='changed'::bytea WHERE root_id=%s RETURNING root_id",
            (self.root,),
        )
        with self.assertRaises(ValueError):
            await self.store.read_consolidated(self.scope, self.root, self.op)

    async def test_reservation_and_competing_root_commit(self):
        second = await self.store.reserve_consolidated(
            self.scope, self.root, 1, 100000, example_context()
        )
        await self.run_journey()
        with self.assertRaises(StorageConflictError):
            await stage_journey(self.store, self.scope, self.root, second)
        await self.store.cancel_consolidated(self.scope, self.root, second)

    async def test_commit_fault_rollback_and_lost_ack(self):
        class Fault(ConsolidatedStore):
            after = False

            @asynccontextmanager
            async def _transaction(self, **kwargs):
                async with super()._transaction(**kwargs) as conn:
                    yield conn
                    if not kwargs.get("read") and not self.after:
                        raise ConnectionError("before commit")
                if not kwargs.get("read") and self.after:
                    raise ConnectionError("lost acknowledgement")

        fault = Fault()
        with self.assertRaises(ConnectionError):
            await self.run_journey(commit_store=fault)
        self.assertIsNone(
            await self.store.consolidated_receipt(self.scope, self.root, self.op)
        )
        fault.after = True
        with self.assertRaises(ConnectionError):
            await self.run_journey(commit_store=fault)
        self.assertIsNotNone(
            await self.store.consolidated_receipt(self.scope, self.root, self.op)
        )
        self.assertTrue(
            (
                await self.store.read_consolidated(self.scope, self.root, self.op)
            ).fixture_only
        )

    async def test_schema11_round_trip_preserves_nonfixture_provenance_marker(self):
        await self.store.migrate_consolidated_model()
        await self.run_journey()
        await self.sql(
            "UPDATE research_staging.consolidated_publications SET fixture_only=false WHERE root_id=%s RETURNING root_id",
            (self.root,),
        )
        retained = await self.store.read_consolidated(
            self.scope, self.root, self.op
        )
        self.assertFalse(retained.fixture_only)


class ZConsolidatedImportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = ConsolidatedStore()
        async with self.store._transaction(bootstrap=True) as conn:
            version = (
                await (
                    await conn.execute(
                        "SELECT version FROM research_staging.schema_version"
                    )
                ).fetchone()
            )["version"]
        if version == 10:
            await self.store.migrate_consolidated_model()
            version = 11
        if version == 11:
            await self.store.migrate_consolidated_imports()
        self.origin_scope = uuid4()
        self.recipient_scope = uuid4()
        await self.store.provision_scope(self.origin_scope)
        await self.store.provision_scope(self.recipient_scope)
        self.origin_root = await self.store.create_consolidated_root(self.origin_scope)
        self.origin_operation = await self.store.reserve_consolidated(
            self.origin_scope, self.origin_root, 1, 100000, example_context()
        )
        await stage_journey(
            self.store,
            self.origin_scope,
            self.origin_root,
            self.origin_operation,
        )
        self.bundle = await self.store.export_consolidated(
            self.origin_scope, self.origin_root, self.origin_operation
        )

    async def test_roundtrip_receipt_and_origin_deletion_authority(self):
        target = await self.store.reserve_consolidated_import(
            self.recipient_scope, self.bundle.data, self.bundle.digest
        )
        self.assertEqual(
            await self.store.commit_consolidated_import(
                self.recipient_scope, target, self.bundle.data
            ),
            target,
        )
        self.assertEqual(
            await self.store.commit_consolidated_import(
                self.recipient_scope, target, self.bundle.data
            ),
            target,
        )
        imported = await self.store.read_consolidated_import(
            self.recipient_scope, target
        )
        self.assertEqual(imported.bundle, self.bundle)
        self.assertEqual(
            await self.store.consolidated_import_receipt(
                self.recipient_scope, target
            ),
            self.bundle.digest,
        )
        await self.store.delete_root(self.origin_scope, self.origin_root)
        with self.assertRaises(StorageConflictError):
            await self.store.read_consolidated_import(self.recipient_scope, target)
        self.assertEqual(
            await self.store.consolidated_import_receipt(
                self.recipient_scope, target
            ),
            self.bundle.digest,
        )

    async def test_altered_bundle_and_expired_origin_fail_closed(self):
        target = await self.store.reserve_consolidated_import(
            self.recipient_scope, self.bundle.data, self.bundle.digest
        )
        altered = self.bundle.data.replace(b"fixture_only", b"fixture_only", 1)
        altered = altered + b" "
        with self.assertRaises(ValueError):
            await self.store.commit_consolidated_import(
                self.recipient_scope, target, altered
            )
        await self.store.cancel_consolidated_import(self.recipient_scope, target)
        await self.store.delete_root(self.origin_scope, self.origin_root)
        with self.assertRaises(StorageConflictError):
            await self.store.reserve_consolidated_import(
                self.recipient_scope, self.bundle.data, self.bundle.digest
            )


if __name__ == "__main__":
    asyncio.run(ConsolidatedStore().migrate_consolidated())
    unittest.main(verbosity=2)
