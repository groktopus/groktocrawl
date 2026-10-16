"""Actual same-authority import, revocation and schema-5 export regression cases."""

import asyncio
import json
import unittest
from contextlib import asynccontextmanager
from datetime import timedelta
from uuid import UUID, uuid4

import test_artifact_bundle_db as base
from agent.experimental.import_store import ImportStore
from agent.experimental.source_store import StorageConflictError
from publication_fixture import CONTEXT, CONTEXT_V2


class ImportStorageTests(base.ExportStorageTests):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.store = ImportStore()
        self.bundle = await self.export()
        self.recipient = uuid4()
        await self.store.provision_scope(self.recipient)

    async def grant(self, recipient=None):
        return await self.store.reserve_import(
            recipient or self.recipient,
            self.scope,
            self.root,
            self.publication,
            self.bundle.data,
            self.bundle.digest,
            CONTEXT,
        )

    async def commit(self, target, store=None):
        return await (store or self.store).commit_import(
            self.recipient, target, self.bundle.data, CONTEXT
        )

    async def sql(self, query, params=()):
        async with self.store._transaction() as conn:
            return await (await conn.execute(query, params)).fetchall()

    async def test_import_roundtrip_receipt_and_native_write_rejection(self):
        target = await self.grant()
        with self.assertRaises(StorageConflictError):
            await self.store.read_import(self.recipient, target, CONTEXT)
        await self.commit(target)
        result = await self.store.read_import(self.recipient, target, CONTEXT)
        self.assertEqual(result.bundle.document, self.bundle)
        self.assertEqual(result.bundle.scope_id, self.scope)
        self.assertEqual(result.recipient_scope, self.recipient)
        self.assertNotEqual(target, self.root)
        self.assertEqual(await self.commit(target), target)
        self.assertEqual(
            await self.store.import_receipt(self.recipient, target), self.bundle.digest
        )
        with self.assertRaises(StorageConflictError):
            await self.store.reserve(self.recipient, target, 1, 100)
        with self.assertRaises(StorageConflictError):
            await self.store.reserve_revision(self.recipient, target, 1, None, 100)
        with self.assertRaises(StorageConflictError):
            await self.store.reserve_publication(
                self.recipient, target, 1, self.revision, 100, CONTEXT
            )
        with self.assertRaises((StorageConflictError, ValueError)):
            await self.store.reserve_import(
                self.scope,
                self.recipient,
                target,
                self.publication,
                self.bundle.data,
                self.bundle.digest,
                CONTEXT,
            )
        with self.assertRaises(StorageConflictError):
            await self.store.read_import(self.scope, target, CONTEXT)

    async def test_origin_deletion_purges_copies_and_pending_grants(self):
        first, pending = await self.grant(), await self.grant()
        await self.commit(first)
        await self.store.delete_root(self.scope, self.root)
        for target in (first, pending):
            with self.assertRaises(StorageConflictError):
                await self.store.read_import(self.recipient, target, CONTEXT)
            with self.assertRaises(StorageConflictError):
                await self.commit(target)
        self.assertEqual(
            await self.sql(
                "SELECT count(*) AS n FROM research_staging.imported_bundles WHERE scope_id=%s",
                (self.recipient,),
            ),
            [{"n": 0}],
        )
        self.assertEqual(
            await self.sql(
                "SELECT charged FROM research_staging.scopes WHERE scope_id=%s",
                (self.recipient,),
            ),
            [{"charged": 0}],
        )
        self.assertEqual(
            await self.store.import_receipt(self.recipient, first), self.bundle.digest
        )

    async def test_recipient_delete_preserves_origin_and_peer(self):
        first, second = await self.grant(), await self.grant()
        await self.commit(first)
        await self.commit(second)
        await self.store.delete_root(self.recipient, first)
        await self.store.read_import(self.recipient, second, CONTEXT)
        self.assertEqual(await self.export(), self.bundle)
        await self.store.cancel_import(self.recipient, second)
        await self.store.read_import(self.recipient, second, CONTEXT)

    async def test_pending_expiry_cancel_and_completed_grant_expiry(self):
        pending = await self.grant()
        await self.sql(
            "UPDATE research_staging.import_operations SET grant_expires_at=now()-interval '1 second' WHERE scope_id=%s AND root_id=%s RETURNING root_id",
            (self.recipient, pending),
        )
        with self.assertRaises(StorageConflictError):
            await self.commit(pending)
        await self.store.cancel_import(self.recipient, pending)
        await self.store.cancel_import(self.recipient, pending)
        committed = await self.grant()
        await self.commit(committed)
        await self.sql(
            "UPDATE research_staging.import_operations SET grant_expires_at=now()-interval '1 second' WHERE scope_id=%s AND root_id=%s RETURNING root_id",
            (self.recipient, committed),
        )
        await self.store.read_import(self.recipient, committed, CONTEXT)
        self.assertEqual(await self.commit(committed), committed)

    async def test_wrong_bundle_context_and_absent_origin(self):
        target = await self.grant()
        with self.assertRaises(StorageConflictError):
            await self.store.commit_import(
                self.recipient, target, self.bundle.data, CONTEXT_V2
            )
        with self.assertRaises(ValueError):
            await self.store.reserve_import(
                self.recipient,
                self.scope,
                self.root,
                self.publication,
                self.bundle.data,
                "0" * 64,
                CONTEXT,
            )
        data = json.loads(self.bundle.data)
        from agent.experimental.canonical import admit_canonical_json

        data["scope_id"] = str(uuid4())
        forged = admit_canonical_json(
            json.dumps(data).encode(), schema_version=data["schema_version"]
        )
        with self.assertRaises((ValueError, StorageConflictError)):
            await self.store.reserve_import(
                self.recipient,
                UUID(data["scope_id"]),
                self.root,
                self.publication,
                forged.data,
                forged.digest,
                CONTEXT,
            )
        await self.store.cancel_import(self.recipient, target)

    async def test_before_commit_rollback_and_lost_ack(self):
        class Fault(ImportStore):
            after = False

            @asynccontextmanager
            async def _transaction(self, **kwargs):
                async with super()._transaction(**kwargs) as conn:
                    yield conn
                    if not kwargs.get("read") and not self.after:
                        raise ConnectionError("before COMMIT")
                if not kwargs.get("read") and self.after:
                    raise ConnectionError("lost COMMIT ACK")

        target = await self.grant()
        fault = Fault()
        with self.assertRaises(ConnectionError):
            await self.commit(target, fault)
        self.assertIsNone(await self.store.import_receipt(self.recipient, target))
        with self.assertRaises(StorageConflictError):
            await self.store.read_import(self.recipient, target, CONTEXT)
        fault.after = True
        with self.assertRaises(ConnectionError):
            await self.commit(target, fault)
        self.assertEqual(
            await self.store.import_receipt(self.recipient, target), self.bundle.digest
        )
        self.assertEqual(await self.commit(target), target)

    async def test_import_commit_delete_race(self):
        target = await self.grant()
        outcomes = await asyncio.gather(
            self.commit(target),
            self.store.delete_root(self.scope, self.root),
            return_exceptions=True,
        )
        self.assertTrue(
            outcomes[0] == target or isinstance(outcomes[0], StorageConflictError)
        )
        self.assertNotIsInstance(outcomes[1], Exception)
        with self.assertRaises(StorageConflictError):
            await self.store.read_import(self.recipient, target, CONTEXT)

    async def test_import_grant_delete_race(self):
        outcomes = await asyncio.gather(
            self.grant(),
            self.store.delete_root(self.scope, self.root),
            return_exceptions=True,
        )
        self.assertTrue(isinstance(outcomes[0], (UUID, StorageConflictError)))
        self.assertNotIsInstance(outcomes[1], Exception)
        if isinstance(outcomes[0], UUID):
            with self.assertRaises(StorageConflictError):
                await self.commit(outcomes[0])

    async def test_opposing_scope_imports(self):
        other = base.ExportStorageTests()
        await other.asyncSetUp()
        other_bundle = await other.export()
        targets = await asyncio.gather(
            self.grant(other.scope),
            self.store.reserve_import(
                self.scope,
                other.scope,
                other.root,
                other.publication,
                other_bundle.data,
                other_bundle.digest,
                CONTEXT,
            ),
        )
        await asyncio.gather(
            self.store.commit_import(
                other.scope, targets[0], self.bundle.data, CONTEXT
            ),
            self.store.commit_import(
                self.scope, targets[1], other_bundle.data, CONTEXT
            ),
        )
        await self.store.read_import(other.scope, targets[0], CONTEXT)
        await self.store.read_import(self.scope, targets[1], CONTEXT)

    async def test_recipient_quota_race(self):
        limited = uuid4()
        await self.store.provision_scope(limited, len(self.bundle.data))
        outcomes = await asyncio.gather(
            self.grant(limited), self.grant(limited), return_exceptions=True
        )
        self.assertEqual(sum(isinstance(value, UUID) for value in outcomes), 1)
        self.assertEqual(
            sum(isinstance(value, StorageConflictError) for value in outcomes), 1
        )

    async def test_twenty_grant_fanout_bound_and_cancelled_metadata(self):
        for _ in range(19):
            target = await self.grant()
            await self.store.cancel_import(self.recipient, target)
        outcomes = await asyncio.gather(
            self.grant(), self.grant(), return_exceptions=True
        )
        self.assertEqual(sum(isinstance(value, UUID) for value in outcomes), 1)
        self.assertEqual(
            sum(isinstance(value, StorageConflictError) for value in outcomes), 1
        )
        await self.store.delete_root(self.scope, self.root)

    async def test_origin_expiry_denies_grant_commit_and_reopen(self):
        first, pending = await self.grant(), await self.grant()
        await self.commit(first)
        await self.sql(
            "UPDATE research_staging.roots SET expires_at=now()-interval '1 second' WHERE scope_id=%s AND root_id=%s RETURNING root_id",
            (self.scope, self.root),
        )
        with self.assertRaises(StorageConflictError):
            await self.grant()
        with self.assertRaises(StorageConflictError):
            await self.commit(pending)
        with self.assertRaises(StorageConflictError):
            await self.store.read_import(self.recipient, first, CONTEXT)
        await self.store.delete_root(self.scope, self.root)

    async def test_current_origin_retention_clamps_without_renewal(self):
        target = await self.grant()
        await self.commit(target)
        before = await self.root_state()
        await self.sql(
            "UPDATE research_staging.roots SET expires_at=expires_at-interval '1 day' WHERE scope_id=%s AND root_id=%s RETURNING root_id",
            (self.scope, self.root),
        )
        original = await self.store.read_import(self.recipient, target, CONTEXT)
        second = await self.grant()
        await self.commit(second)
        copied = await self.store.read_import(self.recipient, second, CONTEXT)
        self.assertEqual(original.retained_until, copied.retained_until)
        self.assertEqual(original.bundle.document, self.bundle)
        state = await self.root_state()
        self.assertEqual(state["expires_at"], before["expires_at"] - timedelta(days=1))

    async def test_stale_generation_and_import_migration_refusal(self):
        target = await self.grant()
        await self.sql(
            "UPDATE research_staging.roots SET generation=generation+1 WHERE scope_id=%s AND root_id=%s RETURNING root_id",
            (self.scope, self.root),
        )
        with self.assertRaises(StorageConflictError):
            await self.commit(target)
        with self.assertRaises(StorageConflictError):
            await self.store.migrate_imports()
        await self.store.delete_root(self.scope, self.root)


if __name__ == "__main__":
    asyncio.run(ImportStore().migrate_imports())
    unittest.main(verbosity=2)
