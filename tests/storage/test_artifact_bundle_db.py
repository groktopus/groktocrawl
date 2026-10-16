"""Actual PostgreSQL export snapshot and offline integrity contracts."""

import base64
import json
import unittest
from datetime import UTC, datetime
from unittest.mock import patch
from uuid import uuid4

from agent.experimental.artifact_bundle import ArtifactBundleStore, admit_bundle
from agent.experimental.source_store import StorageConflictError
from publication_fixture import (
    CONTEXT,
    CONTEXT_V2,
    publication_payload,
    supported_revision,
)
from test_revision_store_db import payload


class ExportStorageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = ArtifactBundleStore()
        self.scope = uuid4()
        await self.store.provision_scope(self.scope)
        self.root = await self.store.create_root(self.scope)
        self.body = b"Export exact bytes\r\nwith NUL\x00 and evidence."
        operation = await self.store.reserve(self.scope, self.root, 1, 1000)
        self.snapshot = await self.store.commit_source(
            self.scope,
            self.root,
            1,
            operation,
            self.body,
            "https://example.test/revision",
        )
        self.revision = await self.store.reserve_revision(
            self.scope, self.root, 1, None, 10000
        )
        self.raw = supported_revision(
            payload(self.scope, self.root, self.revision, self.snapshot, self.body)
        )
        await self.store.commit_revision(
            self.scope, self.root, 1, self.revision, self.raw
        )
        self.publication = await self.store.reserve_publication(
            self.scope, self.root, 1, self.revision, 100000, CONTEXT
        )
        self.structure = (
            await self.store.read_revision(self.scope, self.root, self.revision)
        ).structure
        await self.store.commit_publication(
            self.scope,
            self.root,
            1,
            self.revision,
            self.publication,
            publication_payload(self.structure, self.publication),
            CONTEXT,
        )

    async def export(self, publication=None, context=CONTEXT):
        return await self.store.export_publication(
            self.scope, self.root, publication or self.publication, context
        )

    async def root_state(self):
        async with self.store._transaction(read=True) as conn:
            return await (
                await conn.execute(
                    "SELECT charged,expires_at,current_revision FROM research_staging.roots WHERE scope_id=%s AND root_id=%s",
                    (self.scope, self.root),
                )
            ).fetchone()

    async def test_exact_offline_export_does_not_renew_or_charge(self):
        before = await self.root_state()
        bundle = await self.export()
        checked = admit_bundle(
            bundle.data,
            expected_digest=bundle.digest,
            scope=self.scope,
            root=self.root,
            publication=self.publication,
            context=CONTEXT,
            now=datetime.now(UTC),
        )
        self.assertEqual(checked.snapshot_ids, (self.snapshot,))
        members = json.loads(bundle.data)["members"]
        self.assertEqual(
            base64.b64decode(members[f"sources/{self.snapshot}.body"]["data"]),
            self.body,
        )
        self.assertEqual(before, await self.root_state())
        self.assertEqual(await self.export(), bundle)

    async def test_selected_ancestry_and_historical_rerender(self):
        child = await self.store.reserve_revision(
            self.scope, self.root, 1, self.revision, 10000
        )
        raw = json.loads(self.raw)
        raw["parent_revision_id"] = str(self.revision)
        raw["structure"]["revision_id"] = str(child)
        await self.store.commit_revision(
            self.scope, self.root, 1, child, json.dumps(raw).encode()
        )
        child_structure = (
            await self.store.read_revision(self.scope, self.root, child)
        ).structure
        pub = await self.store.reserve_publication(
            self.scope, self.root, 1, child, 100000, CONTEXT
        )
        await self.store.commit_publication(
            self.scope,
            self.root,
            1,
            child,
            pub,
            publication_payload(child_structure, pub),
            CONTEXT,
        )
        self.assertEqual(
            json.loads((await self.export(pub)).data)["revision_ids"],
            [str(self.revision), str(child)],
        )
        rerender = await self.store.reserve_publication(
            self.scope,
            self.root,
            1,
            self.revision,
            100000,
            CONTEXT_V2,
            rerender_of=self.publication,
            original_context=CONTEXT,
        )
        await self.store.commit_publication(
            self.scope,
            self.root,
            1,
            self.revision,
            rerender,
            publication_payload(self.structure, rerender, CONTEXT_V2),
            CONTEXT_V2,
        )
        old = json.loads((await self.export(rerender, CONTEXT_V2)).data)
        self.assertEqual(old["revision_ids"], [str(self.revision)])
        self.assertEqual((await self.root_state())["current_revision"], child)

    async def test_scope_context_expiry_and_deletion(self):
        with self.assertRaises(StorageConflictError):
            await self.store.export_publication(
                uuid4(), self.root, self.publication, CONTEXT
            )
        with self.assertRaises(StorageConflictError):
            await self.export(context=CONTEXT_V2)
        async with self.store._transaction() as conn:
            await conn.execute(
                "UPDATE research_staging.roots SET expires_at=now()-interval '1 second' WHERE scope_id=%s",
                (self.scope,),
            )
        with self.assertRaises(StorageConflictError):
            await self.export()
        await self.store.delete_root(self.scope, self.root)
        with self.assertRaises(StorageConflictError):
            await self.export()

    async def test_corrupt_reference_rejected_before_export(self):
        async with self.store._transaction() as conn:
            await conn.execute(
                "DELETE FROM research_staging.publication_sources WHERE scope_id=%s",
                (self.scope,),
            )
        with self.assertRaises(StorageConflictError):
            await self.export()
        await self.store.delete_root(self.scope, self.root)

    async def test_byte_budget_rejection_does_not_mutate_root(self):
        before = await self.root_state()
        # Exercise the overflow branch with a reduced test budget against real retained rows.
        with patch("agent.experimental.artifact_bundle.MAX_BYTES", 128):
            with self.assertRaisesRegex(StorageConflictError, "byte limit"):
                await self.export()
        self.assertEqual(before, await self.root_state())
        await self.export()


if __name__ == "__main__":
    unittest.main(verbosity=2)
