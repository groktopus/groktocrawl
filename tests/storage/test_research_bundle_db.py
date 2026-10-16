"""Actual complete-research export snapshots; offline bytes do not grant import access."""

import base64
import json
import unittest
from datetime import UTC, datetime
from unittest.mock import patch
from uuid import uuid4

import test_research_publication_db as fixtures
from agent.experimental.research_bundle import (
    ResearchBundleStore,
    admit_research_bundle,
)
from agent.experimental.source_store import SourceStore, StorageConflictError
from publication_fixture import CONTEXT, CONTEXT_V2
from research_publication_fixture import research_publication_payload


class CompleteExportTests(unittest.IsolatedAsyncioTestCase):
    sql = fixtures.CompletePublicationTests.sql
    prepare = fixtures.CompletePublicationTests.prepare
    commit = fixtures.CompletePublicationTests.commit
    publication = fixtures.CompletePublicationTests.publication
    publish = fixtures.CompletePublicationTests.publish
    advance = fixtures.CompletePublicationTests.advance

    async def asyncSetUp(self):
        await fixtures.CompletePublicationTests.asyncSetUp(self)
        self.store = ResearchBundleStore()
        self.published, raw = await self.publication()
        await self.publish(self.published, raw)

    async def export(self, publication=None, context=CONTEXT):
        return await self.store.export_research_publication(
            self.scope, self.root, publication or self.published, context
        )

    async def state(self):
        return await self.sql(
            "SELECT charged,expires_at,current_research_revision FROM research_staging.roots WHERE root_id=%s",
            (self.root,),
        )

    async def test_exact_offline_export_preserves_state(self):
        before = await self.state()
        result = await self.export()
        verified = admit_research_bundle(
            result.data,
            expected_digest=result.digest,
            scope=self.scope,
            root=self.root,
            publication=self.published,
            context=CONTEXT,
            now=datetime.now(UTC),
        )
        self.assertEqual(verified.revision_ids, (self.revision,))
        self.assertEqual(verified.snapshot_ids, (self.snapshot,))
        self.assertEqual(
            base64.b64decode(
                json.loads(result.data)["members"][f"sources/{self.snapshot}.body"][
                    "data"
                ]
            ),
            self.body,
        )
        self.assertEqual(await self.export(), result)
        self.assertEqual(before, await self.state())

    async def test_current_child_and_historical_rerender_export_distinct_ancestry(self):
        child = await self.advance()
        pin = await self.store.read_research(self.scope, self.root, child)
        pub = await self.store.reserve_research_publication(
            self.scope, self.root, 1, child, 100000, CONTEXT
        )
        await self.store.commit_research_publication(
            self.scope,
            self.root,
            1,
            child,
            pub,
            research_publication_payload(pin, pub, CONTEXT),
            CONTEXT,
        )
        self.assertEqual(
            json.loads((await self.export(pub)).data)["revision_ids"],
            [str(self.revision), str(child)],
        )
        old, raw = await self.publication(
            CONTEXT_V2, rerender_of=self.published, original_context=CONTEXT
        )
        await self.publish(old, raw, CONTEXT_V2)
        self.assertEqual(
            json.loads((await self.export(old, CONTEXT_V2)).data)["revision_ids"],
            [str(self.revision)],
        )
        self.assertEqual((await self.state())[0]["current_research_revision"], child)

    async def test_scope_context_expiry_and_deletion_deny_export(self):
        with self.assertRaises(StorageConflictError):
            await self.store.export_research_publication(
                uuid4(), self.root, self.published, CONTEXT
            )
        with self.assertRaises(StorageConflictError):
            await self.export(context=CONTEXT_V2)
        await self.sql(
            "UPDATE research_staging.roots SET expires_at=now()-interval '1 second' WHERE root_id=%s RETURNING root_id",
            (self.root,),
        )
        with self.assertRaises(StorageConflictError):
            await self.export()
        await self.store.collect_expired(self.scope)
        with self.assertRaises(StorageConflictError):
            await self.export()

    async def test_corrupt_history_source_ledger_is_not_exported(self):
        await self.sql(
            "DELETE FROM research_staging.research_revision_sources WHERE root_id=%s RETURNING snapshot_id",
            (self.root,),
        )
        with self.assertRaises(StorageConflictError):
            await self.export()
        await self.store.delete_root(self.scope, self.root)

    async def test_budget_failure_does_not_change_retention_or_charge(self):
        before = await self.state()
        with patch("agent.experimental.research_bundle.MAX_BYTES", 128):
            with self.assertRaisesRegex(StorageConflictError, "byte limit"):
                await self.export()
        self.assertEqual(before, await self.state())
        await self.export()

    async def test_export_snapshot_remains_consistent_during_deletion(self):
        class DeleteDuringRead(ResearchBundleStore):
            deleted = False

            async def _read_source(self, conn, scope, root, snapshot):
                if not self.deleted:
                    self.deleted = True
                    await SourceStore().delete_root(scope, root)
                return await super()._read_source(conn, scope, root, snapshot)

        store = DeleteDuringRead()
        result = await store.export_research_publication(
            self.scope, self.root, self.published, CONTEXT
        )
        self.assertTrue(store.deleted)
        admit_research_bundle(
            result.data,
            expected_digest=result.digest,
            scope=self.scope,
            root=self.root,
            publication=self.published,
            context=CONTEXT,
            now=datetime.now(UTC),
        )
        with self.assertRaises(StorageConflictError):
            await self.export()


if __name__ == "__main__":
    unittest.main(verbosity=2)
