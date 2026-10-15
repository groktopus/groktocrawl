"""Real PostgreSQL adapter contracts. Run explicitly on a NEW isolated database."""

import asyncio
import hashlib
import unittest
from contextlib import asynccontextmanager
from uuid import uuid4

import psycopg
from agent.experimental.source_store import SourceStore, StorageConflictError


class SourceStorageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = SourceStore()
        self.scope = uuid4()
        await self.store.provision_scope(self.scope, 10000)
        self.root = await self.store.create_root(self.scope, 8000)
        self.body = "Evidence é 😀\nNUL\0 survives".encode()
        self.url = "https://example.test/source"

    async def put(self, root=None):
        root = root or self.root
        op = await self.store.reserve(self.scope, root, 1, 1000)
        snapshot = await self.store.commit_source(
            self.scope, root, 1, op, self.body, self.url
        )
        return op, snapshot

    async def test_exact_reopen_and_receipt(self):
        op, snapshot = await self.put()
        reopened = SourceStore()
        result = await reopened.read_source(self.scope, self.root, snapshot)
        self.assertEqual(result.body, self.body)
        self.assertEqual(
            (await reopened.receipt(self.scope, self.root, op)).snapshot_id, snapshot
        )
        replay = await reopened.commit_source(
            self.scope, self.root, 1, op, self.body, self.url
        )
        self.assertEqual(replay, snapshot)
        with self.assertRaises(StorageConflictError):
            await reopened.commit_source(
                self.scope, self.root, 1, op, b"changed", self.url
            )

    async def test_lost_commit_ack_reconciles_without_second_snapshot(self):
        class LostAckStore(SourceStore):
            @asynccontextmanager
            async def _transaction(self, **kwargs):
                async with super()._transaction(**kwargs) as conn:
                    yield conn
                raise ConnectionError("simulated loss after successful DB commit")

        op = await self.store.reserve(self.scope, self.root, 1, 1000)
        with self.assertRaises(ConnectionError):
            await LostAckStore().commit_source(
                self.scope, self.root, 1, op, self.body, self.url
            )
        receipt = await self.store.receipt(self.scope, self.root, op)
        self.assertIsNotNone(receipt)
        snapshot = await self.store.commit_source(
            self.scope, self.root, 1, op, self.body, self.url
        )
        self.assertEqual(snapshot, receipt.snapshot_id)
        self.assertEqual(receipt.generation, 1)
        async with await psycopg.AsyncConnection.connect() as conn:
            row = await (
                await conn.execute(
                    "SELECT count(*) FROM research_staging.snapshots WHERE scope_id=%s AND root_id=%s",
                    (self.scope, self.root),
                )
            ).fetchone()
            self.assertEqual(row[0], 1)

    async def test_wrong_scope_root_and_generation(self):
        op, snapshot = await self.put()
        other_scope = uuid4()
        await self.store.provision_scope(other_scope)
        other_root = await self.store.create_root(self.scope)
        for scope, root in ((other_scope, self.root), (self.scope, other_root)):
            with self.assertRaises(StorageConflictError):
                await self.store.read_source(scope, root, snapshot)
            self.assertIsNone(await self.store.receipt(scope, root, op))
        with self.assertRaises(StorageConflictError):
            await self.store.reserve(self.scope, self.root, 2, 1000)
        with self.assertRaises(StorageConflictError):
            await self.store.reserve(self.scope, uuid4(), 1, 1000)

    async def test_reservation_limit_cancel_and_retry(self):
        op = await self.store.reserve(self.scope, self.root, 1, 1)
        with self.assertRaises(StorageConflictError):
            await self.store.commit_source(
                self.scope, self.root, 1, op, self.body, self.url
            )
        self.assertIsNone(await self.store.receipt(self.scope, self.root, op))
        await self.store.cancel_reservation(self.scope, self.root, op)
        await self.store.cancel_reservation(self.scope, self.root, op)
        with self.assertRaises(StorageConflictError):
            await self.store.commit_source(
                self.scope, self.root, 1, op, self.body, self.url
            )
        large = await self.store.reserve(self.scope, self.root, 1, 8000)
        await self.store.cancel_reservation(self.scope, self.root, large)
        await self.put()

    async def test_concurrent_root_quota(self):
        results = await asyncio.gather(
            *(self.store.reserve(self.scope, self.root, 1, 8000) for _ in range(2)),
            return_exceptions=True,
        )
        self.assertEqual(
            sum(isinstance(item, StorageConflictError) for item in results), 1
        )
        async with await psycopg.AsyncConnection.connect() as conn:
            row = await (
                await conn.execute(
                    "SELECT charged FROM research_staging.scopes WHERE scope_id=%s",
                    (self.scope,),
                )
            ).fetchone()
            self.assertEqual(row[0], 8000)

    async def test_concurrent_scope_quota_across_roots(self):
        other = await self.store.create_root(self.scope, 8000)
        results = await asyncio.gather(
            self.store.reserve(self.scope, self.root, 1, 7000),
            self.store.reserve(self.scope, other, 1, 7000),
            return_exceptions=True,
        )
        self.assertEqual(
            sum(isinstance(item, StorageConflictError) for item in results), 1
        )

    async def test_delete_preserves_shared_blob_and_receipt(self):
        op, snapshot = await self.put()
        other = await self.store.create_root(self.scope, 8000)
        _, other_snapshot = await self.put(other)
        pending = await self.store.reserve(self.scope, self.root, 1, 1000)
        await self.store.delete_root(self.scope, self.root)
        await self.store.delete_root(self.scope, self.root)
        with self.assertRaises(StorageConflictError):
            await self.store.read_source(self.scope, self.root, snapshot)
        with self.assertRaises(StorageConflictError):
            await self.store.commit_source(
                self.scope, self.root, 1, pending, self.body, self.url
            )
        self.assertEqual(
            (await self.store.receipt(self.scope, self.root, op)).snapshot_id, snapshot
        )
        self.assertEqual(
            (await self.store.read_source(self.scope, other, other_snapshot)).body,
            self.body,
        )
        await self.store.delete_root(self.scope, other)
        async with await psycopg.AsyncConnection.connect() as conn:
            row = await (
                await conn.execute(
                    "SELECT count(*) FROM research_staging.blobs WHERE scope_id=%s",
                    (self.scope,),
                )
            ).fetchone()
            self.assertEqual(row[0], 0)
            row = await (
                await conn.execute(
                    "SELECT charged FROM research_staging.scopes WHERE scope_id=%s",
                    (self.scope,),
                )
            ).fetchone()
            self.assertEqual(row[0], 0)

    async def test_commit_delete_interleaving(self):
        op = await self.store.reserve(self.scope, self.root, 1, 1000)
        commit, deletion = await asyncio.gather(
            self.store.commit_source(self.scope, self.root, 1, op, self.body, self.url),
            self.store.delete_root(self.scope, self.root),
            return_exceptions=True,
        )
        self.assertIsNone(deletion)
        if isinstance(commit, Exception):
            self.assertIsInstance(commit, StorageConflictError)
        else:
            with self.assertRaises(StorageConflictError):
                await self.store.read_source(self.scope, self.root, commit)
        with self.assertRaises(StorageConflictError):
            await self.store.reserve(self.scope, self.root, 1, 1000)

    async def test_expiry_denies_reads_and_writes(self):
        _, snapshot = await self.put()
        async with await psycopg.AsyncConnection.connect() as conn:
            await conn.execute(
                "UPDATE research_staging.roots SET expires_at=now()-interval '1 second' WHERE scope_id=%s AND root_id=%s",
                (self.scope, self.root),
            )
        with self.assertRaises(StorageConflictError):
            await self.store.read_source(self.scope, self.root, snapshot)
        with self.assertRaises(StorageConflictError):
            await self.store.reserve(self.scope, self.root, 1, 1000)
        await self.store.delete_root(self.scope, self.root)

    async def test_descriptor_reference_corruption_detected(self):
        _, snapshot = await self.put()
        async with await psycopg.AsyncConnection.connect() as conn:
            raw = b'{"body_sha256":"wrong","normalization":"utf8-exact/1","schema_version":"source-staging/1","url":"https://example.test/source"}'
            digest = hashlib.sha256(b"source-staging/1\0" + raw).hexdigest()
            await conn.execute(
                "UPDATE research_staging.snapshots SET descriptor=%s,descriptor_digest=%s WHERE scope_id=%s AND root_id=%s AND snapshot_id=%s",
                (raw, digest, self.scope, self.root, snapshot),
            )
        with self.assertRaises(StorageConflictError):
            await self.store.read_source(self.scope, self.root, snapshot)

        # Keep the remaining real database corpus valid for the restore rehearsal.
        await self.store.delete_root(self.scope, self.root)

    async def test_reinstall_refuses_existing_namespace(self):
        with self.assertRaises(psycopg.errors.DuplicateSchema):
            await self.store.install()
        await self.put()


if __name__ == "__main__":
    asyncio.run(SourceStore().install())
    unittest.main(verbosity=2)
