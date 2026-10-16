"""Real PostgreSQL publication/lifecycle contracts with synthetic audit verdicts."""

import asyncio
import json
import unittest
from contextlib import asynccontextmanager
from uuid import uuid4

import psycopg
from agent.experimental.publication_store import PublicationStore, admit_publication
from agent.experimental.source_store import StorageConflictError
from publication_fixture import CONTEXT, publication_payload, supported_revision
from test_revision_store_db import payload


class PublicationStorageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = PublicationStore()
        self.scope = uuid4()
        await self.store.provision_scope(self.scope)
        self.root = await self.store.create_root(self.scope)
        op = await self.store.reserve(self.scope, self.root, 1, 1000)
        self.body = b"Publication fixture evidence."
        self.snapshot = await self.store.commit_source(
            self.scope, self.root, 1, op, self.body, "https://example.test/revision"
        )
        self.revision = await self.store.reserve_revision(
            self.scope, self.root, 1, None, 10000
        )
        self.raw_revision = supported_revision(
            payload(self.scope, self.root, self.revision, self.snapshot, self.body)
        )
        await self.store.commit_revision(
            self.scope, self.root, 1, self.revision, self.raw_revision
        )
        self.structure = (
            await self.store.read_revision(self.scope, self.root, self.revision)
        ).structure

    async def prepare(self, size=100000):
        pub = await self.store.reserve_publication(
            self.scope, self.root, 1, self.revision, size, CONTEXT
        )
        return pub, publication_payload(self.structure, pub)

    async def commit(self, pub, raw, store=None):
        return await (store or self.store).commit_publication(
            self.scope, self.root, 1, self.revision, pub, raw, CONTEXT
        )

    async def read(self, pub):
        return await PublicationStore().read_publication(
            self.scope, self.root, pub, CONTEXT
        )

    async def sql(self, query, params=()):
        async with await psycopg.AsyncConnection.connect() as conn:
            return await (await conn.execute(query, params)).fetchall()

    async def test_commit_reopen_replay_receipt_and_wrong_context(self):
        pub, raw = await self.prepare()
        await self.commit(pub, raw)
        retained = await self.read(pub)
        self.assertEqual(retained, admit_publication(raw, self.structure, pub, CONTEXT))
        self.assertEqual(await self.commit(pub, raw), pub)
        self.assertEqual(
            await self.store.publication_receipt(self.scope, self.root, pub),
            retained.document.digest,
        )
        with self.assertRaises(StorageConflictError):
            await self.store.read_publication(
                self.scope,
                self.root,
                pub,
                CONTEXT.model_copy(update={"policy_version": "wrong"}),
            )
        with self.assertRaises(StorageConflictError):
            await self.store.read_publication(uuid4(), self.root, pub, CONTEXT)

    async def test_stale_revision_and_failed_audit_do_not_publish(self):
        pub, raw = await self.prepare()
        failed = json.loads(raw)
        failed["publication"]["audits"][0]["verdict"] = "fail"
        with self.assertRaises(ValueError):
            await self.commit(pub, json.dumps(failed).encode())
        child = await self.store.reserve_revision(
            self.scope, self.root, 1, self.revision, 10000
        )
        child_raw = json.loads(self.raw_revision)
        child_raw["parent_revision_id"] = str(self.revision)
        child_raw["structure"]["revision_id"] = str(child)
        await self.store.commit_revision(
            self.scope, self.root, 1, child, json.dumps(child_raw).encode()
        )
        with self.assertRaises(StorageConflictError):
            await self.commit(pub, raw)
        self.assertIsNone(
            await self.store.publication_receipt(self.scope, self.root, pub)
        )
        await self.store.cancel_publication(self.scope, self.root, pub)

    async def test_lost_ack_and_atomic_receipt(self):
        class LostAck(PublicationStore):
            @asynccontextmanager
            async def _transaction(self, **kwargs):
                async with super()._transaction(**kwargs) as conn:
                    yield conn
                if not kwargs.get("read"):
                    raise ConnectionError("injected lost commit acknowledgement")

        pub, raw = await self.prepare()
        with self.assertRaises(ConnectionError):
            await self.commit(pub, raw, LostAck())
        self.assertEqual(
            await self.store.publication_receipt(self.scope, self.root, pub),
            (await self.read(pub)).document.digest,
        )
        self.assertEqual(await self.commit(pub, raw), pub)

    async def test_deletion_purges_outputs_and_denies_late_commit(self):
        pub, raw = await self.prepare()
        late, late_raw = await self.prepare()
        await self.commit(pub, raw)
        receipt = await self.store.publication_receipt(self.scope, self.root, pub)
        await self.store.delete_root(self.scope, self.root)
        with self.assertRaises(StorageConflictError):
            await self.read(pub)
        with self.assertRaises(StorageConflictError):
            await self.commit(late, late_raw)
        self.assertEqual(
            await self.store.publication_receipt(self.scope, self.root, pub), receipt
        )
        self.assertEqual(
            await self.sql(
                "SELECT count(*) FROM research_staging.publications WHERE scope_id=%s",
                (self.scope,),
            ),
            [(0,)],
        )
        self.assertEqual(
            await self.sql(
                "SELECT count(*) FROM research_staging.publication_sources WHERE scope_id=%s",
                (self.scope,),
            ),
            [(0,)],
        )
        self.assertEqual(
            await self.sql(
                "SELECT charged FROM research_staging.roots WHERE scope_id=%s",
                (self.scope,),
            ),
            [(0,)],
        )

    async def test_commit_delete_race_and_expired_publication(self):
        pub, raw = await self.prepare()
        outcomes = await asyncio.gather(
            self.commit(pub, raw),
            self.store.delete_root(self.scope, self.root),
            return_exceptions=True,
        )
        self.assertTrue(
            outcomes[0] == pub or isinstance(outcomes[0], StorageConflictError)
        )
        self.assertNotIsInstance(outcomes[1], Exception)
        with self.assertRaises(StorageConflictError):
            await self.read(pub)
        self.assertEqual(
            await self.sql(
                "SELECT count(*) FROM research_staging.publications WHERE scope_id=%s",
                (self.scope,),
            ),
            [(0,)],
        )

    async def test_expiry_blocks_outputs_but_preserves_receipt(self):
        pub, raw = await self.prepare()
        await self.commit(pub, raw)
        receipt = await self.store.publication_receipt(self.scope, self.root, pub)
        await self.sql(
            "UPDATE research_staging.roots SET expires_at=now()-interval '1 second' WHERE scope_id=%s RETURNING root_id",
            (self.scope,),
        )
        with self.assertRaises(StorageConflictError):
            await self.read(pub)
        self.assertEqual(
            await self.store.publication_receipt(self.scope, self.root, pub), receipt
        )
        await self.store.delete_root(self.scope, self.root)

    async def test_integrity_checks_outputs_and_ledger(self):
        pub, raw = await self.prepare()
        await self.commit(pub, raw)
        retained = await self.read(pub)
        await self.sql(
            "UPDATE research_staging.publications SET summary=%s WHERE publication_id=%s RETURNING publication_id",
            (b"Changed", pub),
        )
        with self.assertRaises(StorageConflictError):
            await self.read(pub)
        await self.sql(
            "UPDATE research_staging.publications SET summary=%s WHERE publication_id=%s RETURNING publication_id",
            (retained.summary, pub),
        )
        await self.sql(
            "DELETE FROM research_staging.publication_sources WHERE publication_id=%s RETURNING publication_id",
            (pub,),
        )
        with self.assertRaises(StorageConflictError):
            await self.read(pub)
        # Deliberate corrupt root is purged before the later backup rehearsal.
        await self.store.delete_root(self.scope, self.root)

    async def test_retention_reservations_and_migration_refusal(self):
        pub, raw = await self.prepare()
        await self.commit(pub, raw)
        query = "SELECT expires_at,published_at FROM research_staging.roots WHERE scope_id=%s"
        before = await self.sql(query, (self.scope,))
        self.assertEqual((before[0][0] - before[0][1]).days, 30)
        op = await self.store.reserve(self.scope, self.root, 1, 1000)
        await self.store.cancel_reservation(self.scope, self.root, op)
        revision = await self.store.reserve_revision(
            self.scope, self.root, 1, self.revision, 10000
        )
        await self.store.cancel_revision(self.scope, self.root, revision)
        pending, _ = await self.prepare()
        await self.store.cancel_publication(self.scope, self.root, pending)
        await self.store.cancel_publication(self.scope, self.root, pending)
        self.assertEqual(await self.sql(query, (self.scope,)), before)
        with self.assertRaises(StorageConflictError):
            await self.store.migrate_publications()
        small, small_raw = await self.prepare(1)
        with self.assertRaises(StorageConflictError):
            await self.commit(small, small_raw)
        await self.store.cancel_publication(self.scope, self.root, small)


if __name__ == "__main__":
    asyncio.run(PublicationStore().migrate_publications())
    unittest.main(verbosity=2)
