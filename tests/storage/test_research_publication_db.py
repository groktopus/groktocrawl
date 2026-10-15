"""Actual schema-8 publication/rerender transactions plus complete storage regressions."""

import asyncio
import json
import unittest
from contextlib import asynccontextmanager
from uuid import uuid4

import test_research_store_db as legacy
from agent.experimental.research_publication_store import ResearchPublicationStore
from agent.experimental.source_store import StorageConflictError
from publication_fixture import CONTEXT, CONTEXT_V2
from research_publication_fixture import research_publication_payload


class LegacyLifecycleTests(legacy.LegacyRegressionTests):
    pass


class CompleteHistoryTests(legacy.ResearchStorageTests):
    pass


class CompletePublicationTests(unittest.IsolatedAsyncioTestCase):
    sql = legacy.ResearchStorageTests.sql
    prepare = legacy.ResearchStorageTests.prepare
    commit = legacy.ResearchStorageTests.commit

    async def asyncSetUp(self):
        await legacy.ResearchStorageTests.asyncSetUp(self)
        self.store = ResearchPublicationStore()
        self.revision, raw = await self.prepare()
        await self.commit(self.revision, raw)
        self.pinned = await self.store.read_research(
            self.scope, self.root, self.revision
        )

    async def publication(self, context=CONTEXT, **kwargs):
        identity = await self.store.reserve_research_publication(
            self.scope, self.root, 1, self.revision, 100000, context, **kwargs
        )
        return identity, research_publication_payload(self.pinned, identity, context)

    async def publish(self, identity, raw, context=CONTEXT, store=None):
        return await (store or self.store).commit_research_publication(
            self.scope, self.root, 1, self.revision, identity, raw, context
        )

    async def advance(self):
        revision, raw = await self.prepare((self.pinned.revision,))
        await self.commit(revision, raw)
        return revision

    async def test_publication_roundtrip_replay_and_retention(self):
        identity, raw = await self.publication()
        await self.publish(identity, raw)
        result = await self.store.read_research_publication(
            self.scope, self.root, identity, CONTEXT
        )
        state = await self.sql(
            "SELECT charged,expires_at,published_at FROM research_staging.roots WHERE root_id=%s",
            (self.root,),
        )
        self.assertEqual((state[0]["expires_at"] - state[0]["published_at"]).days, 30)
        self.assertEqual(await self.publish(identity, raw), identity)
        await self.store.cancel_research_publication(self.scope, self.root, identity)
        self.assertEqual(
            state,
            await self.sql(
                "SELECT charged,expires_at,published_at FROM research_staging.roots WHERE root_id=%s",
                (self.root,),
            ),
        )
        self.assertEqual(
            await self.store.research_publication_receipt(
                self.scope, self.root, identity
            ),
            result.document.digest,
        )
        changed = json.loads(raw)
        changed["publication"]["audits"][0]["reason"] = (
            "Different passing audit rationale"
        )
        with self.assertRaises(StorageConflictError):
            await self.publish(identity, json.dumps(changed).encode())

    async def test_old_revision_requires_explicit_historical_rerender(self):
        original, raw = await self.publication()
        await self.publish(original, raw)
        newer = await self.advance()
        with self.assertRaises(StorageConflictError):
            await self.publication()
        rerender, raw = await self.publication(
            CONTEXT_V2, rerender_of=original, original_context=CONTEXT
        )
        await self.publish(rerender, raw, CONTEXT_V2)
        value = await self.store.read_research_publication(
            self.scope, self.root, rerender, CONTEXT_V2
        )
        self.assertEqual(
            json.loads(value.document.data)["revision_digest"],
            self.pinned.document.digest,
        )
        self.assertEqual(
            (
                await self.sql(
                    "SELECT current_research_revision FROM research_staging.roots WHERE root_id=%s",
                    (self.root,),
                )
            )[0]["current_research_revision"],
            newer,
        )
        await self.store.read_research_publication(
            self.scope, self.root, original, CONTEXT
        )
        with self.assertRaises(StorageConflictError):
            await self.store.read_research_publication(
                self.scope, self.root, rerender, CONTEXT
            )

    async def test_current_revision_changes_after_reservation(self):
        identity, raw = await self.publication()
        await self.advance()
        with self.assertRaises(StorageConflictError):
            await self.publish(identity, raw)
        self.assertIsNone(
            await self.store.research_publication_receipt(
                self.scope, self.root, identity
            )
        )
        await self.store.cancel_research_publication(self.scope, self.root, identity)

    async def test_historical_context_and_original_are_required(self):
        original, raw = await self.publication()
        await self.publish(original, raw)
        with self.assertRaises(ValueError):
            await self.publication(rerender_of=original)
        with self.assertRaises(StorageConflictError):
            await self.publication(
                CONTEXT_V2, rerender_of=uuid4(), original_context=CONTEXT
            )
        changed = CONTEXT_V2.model_copy(update={"policy_version": "other"})
        with self.assertRaises(StorageConflictError):
            await self.publication(
                changed, rerender_of=original, original_context=CONTEXT
            )
        with self.assertRaises(StorageConflictError):
            await self.store.migrate_research_publications()

    async def test_quota_cancellation_and_late_commit(self):
        identity = await self.store.reserve_research_publication(
            self.scope, self.root, 1, self.revision, 1, CONTEXT
        )
        raw = research_publication_payload(self.pinned, identity, CONTEXT)
        with self.assertRaises(StorageConflictError):
            await self.publish(identity, raw)
        before = (
            await self.sql(
                "SELECT charged FROM research_staging.roots WHERE root_id=%s",
                (self.root,),
            )
        )[0]["charged"]
        await self.store.cancel_research_publication(self.scope, self.root, identity)
        await self.store.cancel_research_publication(self.scope, self.root, identity)
        self.assertEqual(
            (
                await self.sql(
                    "SELECT charged FROM research_staging.roots WHERE root_id=%s",
                    (self.root,),
                )
            )[0]["charged"],
            before - 1,
        )
        with self.assertRaises(StorageConflictError):
            await self.publish(identity, raw)

    async def test_delete_purges_outputs_but_preserves_receipts(self):
        identity, raw = await self.publication()
        await self.publish(identity, raw)
        receipt = await self.store.research_publication_receipt(
            self.scope, self.root, identity
        )
        pending, pending_raw = await self.publication()
        await self.store.delete_root(self.scope, self.root)
        self.assertEqual(
            await self.store.research_publication_receipt(
                self.scope, self.root, identity
            ),
            receipt,
        )
        for table in (
            "research_publications",
            "research_publication_sources",
            "research_revisions",
            "snapshots",
        ):
            self.assertEqual(
                await self.sql(
                    f"SELECT count(*) AS n FROM research_staging.{table} WHERE root_id=%s",
                    (self.root,),
                ),
                [{"n": 0}],
            )
        with self.assertRaises(StorageConflictError):
            await self.publish(pending, pending_raw)
        with self.assertRaises(StorageConflictError):
            await self.store.read_research_publication(
                self.scope, self.root, identity, CONTEXT
            )

    async def test_expiry_blocks_read_and_collects_outputs(self):
        identity, raw = await self.publication()
        await self.publish(identity, raw)
        await self.sql(
            "UPDATE research_staging.roots SET expires_at=now()-interval '1 second' WHERE root_id=%s RETURNING root_id",
            (self.root,),
        )
        with self.assertRaises(StorageConflictError):
            await self.store.read_research_publication(
                self.scope, self.root, identity, CONTEXT
            )
        await self.store.collect_expired(self.scope)
        self.assertEqual(
            await self.sql(
                "SELECT count(*) AS n FROM research_staging.research_publications WHERE root_id=%s",
                (self.root,),
            ),
            [{"n": 0}],
        )
        self.assertIsNotNone(
            await self.store.research_publication_receipt(
                self.scope, self.root, identity
            )
        )

    async def test_corrupt_output_or_source_ledger_denies_read(self):
        for corruption in ("output", "ledger"):
            identity, raw = await self.publication()
            await self.publish(identity, raw)
            if corruption == "output":
                await self.sql(
                    "UPDATE research_staging.research_publications SET summary=%s WHERE publication_id=%s RETURNING publication_id",
                    (b"wrong", identity),
                )
            else:
                await self.sql(
                    "DELETE FROM research_staging.research_publication_sources WHERE publication_id=%s RETURNING snapshot_id",
                    (identity,),
                )
            with self.assertRaises(StorageConflictError):
                await self.store.read_research_publication(
                    self.scope, self.root, identity, CONTEXT
                )
        await self.store.delete_root(self.scope, self.root)

    async def test_identity_generation_and_format_separation(self):
        identity, raw = await self.publication()
        for scope, root, generation in (
            (uuid4(), self.root, 1),
            (self.scope, uuid4(), 1),
            (self.scope, self.root, 2),
        ):
            with self.assertRaises((StorageConflictError, ValueError)):
                await self.store.commit_research_publication(
                    scope, root, generation, self.revision, identity, raw, CONTEXT
                )
        structural = await self.store.create_root(self.scope)
        with self.assertRaises(StorageConflictError):
            await self.store.reserve_research_publication(
                self.scope, structural, 1, self.revision, 100000, CONTEXT
            )
        with self.assertRaises(StorageConflictError):
            await self.store.reserve_publication(
                self.scope, self.root, 1, self.revision, 100000, CONTEXT
            )
        await self.store.cancel_research_publication(self.scope, self.root, identity)

    async def test_source_closure_and_cancel_commit_race(self):
        identity, raw = await self.publication()
        outcomes = await asyncio.gather(
            self.publish(identity, raw),
            self.store.cancel_research_publication(self.scope, self.root, identity),
            return_exceptions=True,
        )
        self.assertIsNone(outcomes[1])
        receipt = await self.store.research_publication_receipt(
            self.scope, self.root, identity
        )
        self.assertEqual(receipt is not None, outcomes[0] == identity)
        pending, raw = await self.publication()
        await self.sql(
            "DELETE FROM research_staging.research_revision_sources WHERE root_id=%s RETURNING snapshot_id",
            (self.root,),
        )
        with self.assertRaises(StorageConflictError):
            await self.publish(pending, raw)
        await self.store.delete_root(self.scope, self.root)

    async def test_write_fault_rollback_and_lost_ack(self):
        class Fault(ResearchPublicationStore):
            after = False

            @asynccontextmanager
            async def _transaction(self, **kwargs):
                async with super()._transaction(**kwargs) as conn:
                    yield conn
                    if not kwargs.get("read") and not self.after:
                        raise ConnectionError("before commit")
                if not kwargs.get("read") and self.after:
                    raise ConnectionError("lost acknowledgement")

        identity, raw = await self.publication()
        fault = Fault()
        with self.assertRaises(ConnectionError):
            await self.publish(identity, raw, store=fault)
        self.assertIsNone(
            await self.store.research_publication_receipt(
                self.scope, self.root, identity
            )
        )
        fault.after = True
        with self.assertRaises(ConnectionError):
            await self.publish(identity, raw, store=fault)
        self.assertEqual(await self.publish(identity, raw), identity)


if __name__ == "__main__":
    asyncio.run(ResearchPublicationStore().migrate_research_publications())
    unittest.main(verbosity=2)
