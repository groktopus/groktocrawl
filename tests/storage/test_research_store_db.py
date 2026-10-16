"""Real complete-history storage plus schema-7 legacy expiry/import regressions."""

import asyncio
import json
import unittest
from contextlib import asynccontextmanager
from uuid import UUID, uuid4

import test_expiry_store_db as legacy
from agent.experimental.canonical import MAX_BYTES
from agent.experimental.research_store import ResearchStore
from agent.experimental.source_store import StorageConflictError
from research_fixture import research_payload


class LegacyRegressionTests(legacy.ExpiryStorageTests):
    pass


class ResearchStorageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = ResearchStore()
        self.scope = uuid4()
        await self.store.provision_scope(self.scope)
        self.root = await self.store.create_research_root(self.scope)
        self.body = b"Complete fixture evidence\r\nwith NUL\x00."
        operation = await self.store.reserve(self.scope, self.root, 1, 1000)
        self.snapshot = await self.store.commit_source(
            self.scope,
            self.root,
            1,
            operation,
            self.body,
            "https://example.test/revision",
        )

    async def sql(self, query, args=()):
        async with self.store._transaction() as conn:
            return await (await conn.execute(query, args)).fetchall()

    async def prepare(self, prior=(), size=100000):
        parent = (
            UUID(prior[-1].research.verifications.structure.revision_id)
            if prior
            else None
        )
        revision = await self.store.reserve_research(
            self.scope, self.root, 1, parent, size
        )
        return revision, research_payload(
            self.scope, self.root, revision, self.snapshot, self.body, prior
        )

    async def commit(self, revision, raw, store=None):
        return await (store or self.store).commit_research(
            self.scope, self.root, 1, revision, raw
        )

    async def test_complete_root_successor_read_and_receipt(self):
        first, raw = await self.prepare()
        await self.commit(first, raw)
        value = await self.store.read_research(self.scope, self.root, first)
        second, next_raw = await self.prepare((value.revision,))
        await self.commit(second, next_raw)
        self.assertEqual(await self.commit(first, raw), first)
        self.assertEqual(
            await self.store.read_research(self.scope, self.root, first), value
        )
        self.assertEqual(
            await self.store.research_receipt(self.scope, self.root, first),
            value.document.digest,
        )
        self.assertEqual(
            (
                await self.store.read_research(self.scope, self.root, second)
            ).revision.parent_revision_id,
            str(first),
        )
        changed = json.loads(raw)
        changed["revision"]["research"]["objective"] = "Different request"
        with self.assertRaises(StorageConflictError):
            await self.commit(first, json.dumps(changed).encode())

    async def test_changed_verification_and_assessment_ids_rejected(self):
        first, raw = await self.prepare()
        await self.commit(first, raw)
        prior = (await self.store.read_research(self.scope, self.root, first)).revision
        for group, key in (
            ("records", "verification_id"),
            ("assessments", "assessment_id"),
        ):
            second, next_raw = await self.prepare((prior,))
            data = json.loads(next_raw)
            item = data["revision"]["research"]["verifications"][group][0]
            new_id = item[key]
            old_id = getattr(getattr(prior.research.verifications, group)[0], key)
            item[key] = old_id
            data["revision"]["introductions"] = [
                d for d in data["revision"]["introductions"] if d["entity_id"] != new_id
            ]
            if key == "assessment_id":
                for link in data["revision"]["research"]["verifications"][
                    "assessment_links"
                ]:
                    link["assessment_ids"] = [
                        old_id if value == new_id else value
                        for value in link["assessment_ids"]
                    ]
            with self.assertRaises(ValueError):
                await self.commit(second, json.dumps(data).encode())
            await self.store.cancel_research(self.scope, self.root, second)

    async def test_removed_question_id_cannot_return_with_changed_meaning(self):
        first, raw = await self.prepare()
        await self.commit(first, raw)
        a = (await self.store.read_research(self.scope, self.root, first)).revision
        second, raw = await self.prepare((a,))
        data = json.loads(raw)
        data["revision"]["research"]["questions"][0]["question_id"] = "q-2"
        data["revision"]["introductions"].append(
            {"kind": "question", "entity_id": "q-2", "predecessor_id": "q-1"}
        )
        await self.commit(second, json.dumps(data).encode())
        b = (await self.store.read_research(self.scope, self.root, second)).revision
        third, raw = await self.prepare((a, b))
        data = json.loads(raw)
        data["revision"]["research"]["questions"][0]["question"] = (
            "A changed question under the old q-1 identity"
        )
        with self.assertRaises(ValueError):
            await self.commit(third, json.dumps(data).encode())
        await self.store.cancel_research(self.scope, self.root, third)

    async def test_two_children_only_one_current_parent_wins(self):
        first, raw = await self.prepare()
        await self.commit(first, raw)
        prior = (await self.store.read_research(self.scope, self.root, first)).revision
        a, a_raw = await self.prepare((prior,))
        b, b_raw = await self.prepare((prior,))
        outcomes = await asyncio.gather(
            self.commit(a, a_raw), self.commit(b, b_raw), return_exceptions=True
        )
        self.assertEqual(sum(isinstance(value, UUID) for value in outcomes), 1)
        self.assertEqual(
            sum(isinstance(value, StorageConflictError) for value in outcomes), 1
        )
        await self.store.cancel_research(self.scope, self.root, a)
        await self.store.cancel_research(self.scope, self.root, b)

    async def test_legacy_and_complete_roots_do_not_mix(self):
        legacy_root = await self.store.create_root(self.scope)
        with self.assertRaises(StorageConflictError):
            await self.store.reserve_research(self.scope, legacy_root, 1, None, 100)
        with self.assertRaises(StorageConflictError):
            await self.store.reserve_revision(self.scope, self.root, 1, None, 100)
        with self.assertRaises(StorageConflictError):
            await self.store.migrate_research()

    async def test_missing_source_and_wrong_scope_rejected(self):
        revision, _ = await self.prepare()
        raw = research_payload(self.scope, self.root, revision, uuid4(), self.body)
        with self.assertRaises(StorageConflictError):
            await self.commit(revision, raw)
        wrong = research_payload(uuid4(), self.root, revision, self.snapshot, self.body)
        with self.assertRaises(ValueError):
            await self.commit(revision, wrong)
        await self.store.cancel_research(self.scope, self.root, revision)

    async def test_reservation_size_and_double_cancel(self):
        revision, raw = await self.prepare(size=1)
        with self.assertRaises(StorageConflictError):
            await self.commit(revision, raw)
        before = await self.sql(
            "SELECT charged FROM research_staging.roots WHERE scope_id=%s AND root_id=%s",
            (self.scope, self.root),
        )
        await self.store.cancel_research(self.scope, self.root, revision)
        await self.store.cancel_research(self.scope, self.root, revision)
        after = await self.sql(
            "SELECT charged FROM research_staging.roots WHERE scope_id=%s AND root_id=%s",
            (self.scope, self.root),
        )
        self.assertEqual(after[0]["charged"], before[0]["charged"] - 1)
        with self.assertRaises(StorageConflictError):
            await self.commit(revision, raw)

    async def test_delete_and_expiry_purge_complete_payload_and_keep_receipt(self):
        revision, raw = await self.prepare()
        await self.commit(revision, raw)
        receipt = await self.store.research_receipt(self.scope, self.root, revision)
        await self.sql(
            "UPDATE research_staging.roots SET expires_at=now()-interval '1 second' WHERE scope_id=%s AND root_id=%s RETURNING root_id",
            (self.scope, self.root),
        )
        with self.assertRaises(StorageConflictError):
            await self.store.read_research(self.scope, self.root, revision)
        await self.store.collect_expired(self.scope)
        self.assertEqual(
            await self.store.research_receipt(self.scope, self.root, revision), receipt
        )
        self.assertEqual(
            await self.sql(
                "SELECT count(*) AS n FROM research_staging.research_revisions WHERE scope_id=%s",
                (self.scope,),
            ),
            [{"n": 0}],
        )
        with self.assertRaises(StorageConflictError):
            await self.commit(revision, raw)

    async def test_corrupt_source_ledger_denies_reopen(self):
        revision, raw = await self.prepare()
        await self.commit(revision, raw)
        await self.sql(
            "DELETE FROM research_staging.research_revision_sources WHERE scope_id=%s AND root_id=%s RETURNING snapshot_id",
            (self.scope, self.root),
        )
        with self.assertRaises(StorageConflictError):
            await self.store.read_research(self.scope, self.root, revision)
        await self.store.delete_root(self.scope, self.root)

    async def test_fault_before_commit_and_lost_ack(self):
        class Fault(ResearchStore):
            after = False

            @asynccontextmanager
            async def _transaction(self, **kwargs):
                async with super()._transaction(**kwargs) as conn:
                    yield conn
                    if not self.after:
                        raise ConnectionError("before COMMIT")
                if self.after:
                    raise ConnectionError("lost COMMIT ACK")

        revision, raw = await self.prepare()
        fault = Fault()
        with self.assertRaises(ConnectionError):
            await self.commit(revision, raw, fault)
        self.assertIsNone(
            await self.store.research_receipt(self.scope, self.root, revision)
        )
        fault.after = True
        with self.assertRaises(ConnectionError):
            await self.commit(revision, raw, fault)
        self.assertEqual(await self.commit(revision, raw), revision)
        self.assertIsNotNone(
            await self.store.research_receipt(self.scope, self.root, revision)
        )

    async def test_twenty_revisions_and_quota_are_bounded(self):
        prior = []
        for _ in range(20):
            revision, raw = await self.prepare(tuple(prior))
            await self.commit(revision, raw)
            prior.append(
                (
                    await self.store.read_research(self.scope, self.root, revision)
                ).revision
            )
        extra, raw = await self.prepare(tuple(prior))
        with self.assertRaises(ValueError):
            await self.commit(extra, raw)
        await self.store.cancel_research(self.scope, self.root, extra)
        small = await self.store.create_research_root(self.scope, 100)
        with self.assertRaises(StorageConflictError):
            await self.store.reserve_research(self.scope, small, 1, None, MAX_BYTES)


if __name__ == "__main__":
    asyncio.run(ResearchStore().migrate_research())
    unittest.main(verbosity=2)
