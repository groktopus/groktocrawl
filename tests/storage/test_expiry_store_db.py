"""Actual expiry transactions, plus all schema-6 import/export regressions."""

import asyncio
import unittest
from contextlib import asynccontextmanager
from uuid import uuid4

import test_import_store_db as base
from agent.experimental.expiry_store import ExpiryStore
from agent.experimental.source_store import StorageConflictError
from publication_fixture import CONTEXT


class ExpiryStorageTests(base.ImportStorageTests):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.store = ExpiryStore()

    async def expire(self, scope=None, root=None):
        await self.sql(
            "UPDATE research_staging.roots SET expires_at=now()-interval '1 second' WHERE scope_id=%s AND root_id=%s RETURNING root_id",
            (scope or self.scope, root or self.root),
        )

    async def test_expired_origin_collects_copies_and_preserves_receipts(self):
        first, pending = await self.grant(), await self.grant()
        await self.commit(first)
        await self.expire()
        with self.assertRaises(StorageConflictError):
            await self.export()
        (outcome,) = await self.store.collect_expired(self.scope)
        self.assertEqual((outcome.status, outcome.purged_roots), ("purged", 3))
        self.assertEqual(await self.store.collect_expired(self.scope), ())
        for target in (first, pending):
            with self.assertRaises(StorageConflictError):
                await self.commit(target)
        self.assertEqual(
            await self.store.import_receipt(self.recipient, first), self.bundle.digest
        )
        self.assertEqual(
            await self.sql(
                "SELECT charged FROM research_staging.scopes WHERE scope_id IN (%s,%s)",
                (self.scope, self.recipient),
            ),
            [{"charged": 0}, {"charged": 0}],
        )

    async def test_expired_recipient_preserves_origin_and_peer(self):
        first, second, pending = (
            await self.grant(),
            await self.grant(),
            await self.grant(),
        )
        await self.commit(first)
        await self.commit(second)
        await self.expire(self.recipient, first)
        await self.expire(self.recipient, pending)
        outcomes = await self.store.collect_expired(self.recipient)
        self.assertEqual(len(outcomes), 2)
        self.assertTrue(all(outcome.purged_roots == 1 for outcome in outcomes))
        self.assertEqual(await self.export(), self.bundle)
        await self.store.read_import(self.recipient, second, CONTEXT)

    async def test_shared_blob_and_live_staging_survive_collection(self):
        other = await self.store.create_root(self.scope)
        reservation = await self.store.reserve(self.scope, other, 1, 1000)
        snapshot = await self.store.commit_source(
            self.scope,
            other,
            1,
            reservation,
            self.body,
            "https://example.test/revision",
        )
        await self.expire()
        await self.store.collect_expired(self.scope)
        self.assertEqual(
            (await self.store.read_source(self.scope, other, snapshot)).body, self.body
        )
        await self.store.delete_root(self.scope, other)

    async def test_candidate_limit_and_expired_staging_reservations(self):
        roots = [await self.store.create_root(self.scope) for _ in range(3)]
        for root in roots:
            await self.store.reserve(self.scope, root, 1, 100)
            await self.expire(self.scope, root)
        before = await self.root_state()
        first = await self.store.collect_expired(self.scope, 2)
        second = await self.store.collect_expired(self.scope, 2)
        self.assertEqual((len(first), len(second)), (2, 1))
        self.assertEqual(await self.store.collect_expired(self.scope), ())
        self.assertEqual(await self.root_state(), before)
        self.assertEqual(
            await self.sql(
                "SELECT charged FROM research_staging.scopes WHERE scope_id=%s",
                (self.scope,),
            ),
            [{"charged": before["charged"]}],
        )
        with self.assertRaises(StorageConflictError):
            await self.store.collect_expired(uuid4())
        with self.assertRaises(StorageConflictError):
            await self.store.migrate_expiry()

    async def test_stale_candidate_hint_preserves_real_writer_renewal(self):
        root = await self.store.create_root(self.scope)

        # Model a discovery snapshot overtaken by a valid writer's committed renewal.
        class StaleHint(ExpiryStore):
            async def _expiry_candidates(self, scope, limit):
                await self.reserve(scope, root, 1, 100)
                return (root,)

        (outcome,) = await StaleHint().collect_expired(self.scope)
        self.assertEqual(outcome.status, "skipped")
        await self.store.reserve(self.scope, root, 1, 100)
        await self.expire(self.scope, root)
        await self.store.collect_expired(self.scope)
        with self.assertRaises(StorageConflictError):
            await self.store.reserve(self.scope, root, 1, 100)

    async def test_writer_holding_lock_renews_before_collector_ownership(self):
        root = await self.store.create_root(self.scope)
        await self.sql(
            "UPDATE research_staging.roots SET expires_at=clock_timestamp()+interval '1 second' WHERE scope_id=%s AND root_id=%s RETURNING root_id",
            (self.scope, root),
        )
        discovered = asyncio.Event()

        class Signals(ExpiryStore):
            async def _expiry_candidates(self, scope, limit):
                candidates = await super()._expiry_candidates(scope, limit)
                discovered.set()
                return candidates

        async with self.store._transaction() as conn:
            row = await self.store._lock(conn, self.scope, root)
            self.store._active(row, 1)
            await conn.execute("SELECT pg_sleep(1.1)")
            task = asyncio.create_task(Signals().collect_expired(self.scope))
            await asyncio.wait_for(discovered.wait(), 1)
            await self.store._renew_staging(conn, self.scope, root)
        outcomes = await task
        self.assertEqual([(o.root_id, o.status) for o in outcomes], [(root, "skipped")])
        await self.store.reserve(self.scope, root, 1, 100)
        await self.store.delete_root(self.scope, root)

    async def test_collection_serializes_with_import_commit_and_deletion(self):
        target = await self.grant()
        await self.expire()
        outcomes = await asyncio.gather(
            self.store.collect_expired(self.scope),
            self.commit(target),
            self.store.delete_root(self.scope, self.root),
            return_exceptions=True,
        )
        self.assertIsInstance(outcomes[0], tuple)
        self.assertIsInstance(outcomes[1], StorageConflictError)
        self.assertIsNone(outcomes[2])
        self.assertEqual(
            await self.sql(
                "SELECT count(*) AS n FROM research_staging.imported_bundles WHERE scope_id=%s",
                (self.recipient,),
            ),
            [{"n": 0}],
        )

    async def test_before_commit_failure_and_lost_ack_are_reconcilable(self):
        class Fault(ExpiryStore):
            after = False

            @asynccontextmanager
            async def _transaction(self, **kwargs):
                async with super()._transaction(**kwargs) as conn:
                    yield conn
                    if not kwargs.get("read") and not self.after:
                        raise ConnectionError("before COMMIT")
                if not kwargs.get("read") and self.after:
                    raise ConnectionError("lost COMMIT ACK")

        await self.expire()
        fault = Fault()
        with self.assertRaises(ConnectionError) as error:
            await fault.collect_expired(self.scope)
        self.assertTrue(error.exception.__notes__)
        self.assertEqual(len(await self.store._expiry_candidates(self.scope, 20)), 1)
        fault.after = True
        with self.assertRaises(ConnectionError):
            await fault.collect_expired(self.scope)
        self.assertEqual(await self.store.collect_expired(self.scope), ())
        self.assertEqual(
            await self.sql(
                "SELECT deleted,charged FROM research_staging.roots WHERE scope_id=%s AND root_id=%s",
                (self.scope, self.root),
            ),
            [{"deleted": True, "charged": 0}],
        )
        self.assertIsNotNone(
            await self.store.publication_receipt(
                self.scope, self.root, self.publication
            )
        )

    async def test_later_candidate_failure_preserves_prior_commit(self):
        other = await self.store.create_root(self.scope)
        await self.expire()
        await self.expire(self.scope, other)

        class Partial(ExpiryStore):
            calls = 0

            async def _collect_candidate(self, scope, root):
                self.calls += 1
                if self.calls == 2:
                    raise ConnectionError("second candidate failed")
                return await super()._collect_candidate(scope, root)

        with self.assertRaises(ConnectionError):
            await Partial().collect_expired(self.scope)
        self.assertEqual(len(await self.store._expiry_candidates(self.scope, 20)), 1)
        self.assertEqual(len(await self.store.collect_expired(self.scope)), 1)

    async def test_schema_six_candidate_index_is_available(self):
        index = await self.sql(
            "SELECT indexdef FROM pg_indexes WHERE schemaname='research_staging' AND indexname='live_scope_expiry'"
        )
        self.assertEqual(len(index), 1)
        plan = await self.sql(
            "EXPLAIN (FORMAT JSON) SELECT root_id FROM research_staging.roots WHERE scope_id=%s AND NOT deleted AND expires_at<=now() ORDER BY expires_at,root_id LIMIT 20",
            (self.scope,),
        )
        self.assertEqual(plan[0]["QUERY PLAN"][0]["Plan"]["Node Type"], "Limit")


if __name__ == "__main__":
    asyncio.run(ExpiryStore().migrate_expiry())
    unittest.main(verbosity=2)
