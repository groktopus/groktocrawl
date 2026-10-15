"""Historical presentation reuse plus schema-4 regression of publication lifecycle."""

import asyncio
import json
import unittest
from uuid import uuid4

import test_publication_store_db as base
from agent.experimental.publication_store import PublicationStore
from agent.experimental.source_store import StorageConflictError
from publication_fixture import CONTEXT, CONTEXT_V2, publication_payload


class HistoricalRerenderTests(base.PublicationStorageTests):
    async def historical(self):
        original, raw = await self.prepare()
        await self.commit(original, raw)
        child = await self.store.reserve_revision(
            self.scope, self.root, 1, self.revision, 10000
        )
        child_raw = json.loads(self.raw_revision)
        child_raw["parent_revision_id"] = str(self.revision)
        child_raw["structure"]["revision_id"] = str(child)
        await self.store.commit_revision(
            self.scope, self.root, 1, child, json.dumps(child_raw).encode()
        )
        return original, raw, child

    async def reserve_rerender(self, original, context=CONTEXT_V2):
        return await self.store.reserve_publication(
            self.scope,
            self.root,
            1,
            self.revision,
            100000,
            context,
            rerender_of=original,
            original_context=CONTEXT,
        )

    async def test_historical_rerender_preserves_original_and_current_pointer(self):
        original, original_raw, child = await self.historical()
        before = await self.read(original)
        rerender = await self.reserve_rerender(original)
        raw = publication_payload(self.structure, rerender, CONTEXT_V2)
        await self.store.commit_publication(
            self.scope, self.root, 1, self.revision, rerender, raw, CONTEXT_V2
        )
        new = await self.store.read_publication(
            self.scope, self.root, rerender, CONTEXT_V2
        )
        self.assertEqual(
            json.loads(new.document.data)["research"],
            json.loads(original_raw)["research"],
        )
        self.assertNotEqual(new.document.digest, before.document.digest)
        self.assertEqual(await self.read(original), before)
        self.assertEqual(
            await self.sql(
                "SELECT current_revision FROM research_staging.roots WHERE root_id=%s",
                (self.root,),
            ),
            [(child,)],
        )
        self.assertEqual(
            await self.store.commit_publication(
                self.scope, self.root, 1, self.revision, rerender, raw, CONTEXT_V2
            ),
            rerender,
        )
        with self.assertRaises(StorageConflictError):
            await self.prepare()

    async def test_changed_research_rejected_even_with_new_valid_audits(self):
        from agent.experimental.knowledge import text_digest
        from agent.experimental.publication import FixtureRenderAudit, RenderInput

        original, _, _ = await self.historical()
        rerender = await self.reserve_rerender(original)
        data = json.loads(publication_payload(self.structure, rerender, CONTEXT_V2))
        data["research"]["questions"][0]["question"] = "A different research question"
        # Recompute every fixture audit, so the historical binding is what rejects it.
        audits = []
        for raw_audit in data["publication"]["audits"]:
            raw_audit["checked_input"]["research"] = data["research"]
            checked = RenderInput.model_validate(raw_audit["checked_input"])
            raw_audit.update(
                checked_input_digest=checked.input_digest(),
                checked_output_digest=text_digest(checked.rendered_text()),
            )
            audits.append(
                FixtureRenderAudit.model_validate(raw_audit).model_dump(mode="json")
            )
        data["publication"]["audits"] = audits
        with self.assertRaisesRegex(StorageConflictError, "research changed"):
            await self.store.commit_publication(
                self.scope,
                self.root,
                1,
                self.revision,
                rerender,
                json.dumps(data).encode(),
                CONTEXT_V2,
            )
        await self.store.cancel_publication(self.scope, self.root, rerender)

    async def test_rerender_wrong_original_context_scope_and_policy(self):
        original, _, _ = await self.historical()
        with self.assertRaises(StorageConflictError):
            await self.reserve_rerender(uuid4())
        with self.assertRaises(StorageConflictError):
            await self.reserve_rerender(
                original, CONTEXT_V2.model_copy(update={"policy_version": "changed"})
            )
        with self.assertRaises(StorageConflictError):
            await self.store.reserve_publication(
                self.scope,
                self.root,
                1,
                self.revision,
                100000,
                CONTEXT_V2,
                rerender_of=original,
                original_context=CONTEXT_V2,
            )
        with self.assertRaises(ValueError):
            await self.store.reserve_publication(
                self.scope,
                self.root,
                1,
                self.revision,
                100000,
                CONTEXT_V2,
                rerender_of=original,
            )

    async def test_delete_between_rerender_reservation_and_commit(self):
        original, _, _ = await self.historical()
        rerender = await self.reserve_rerender(original)
        raw = publication_payload(self.structure, rerender, CONTEXT_V2)
        await self.store.delete_root(self.scope, self.root)
        with self.assertRaises(StorageConflictError):
            await self.store.commit_publication(
                self.scope, self.root, 1, self.revision, rerender, raw, CONTEXT_V2
            )
        self.assertIsNone(
            await self.store.publication_receipt(self.scope, self.root, rerender)
        )

    async def test_rerender_migration_refuses_reapplication(self):
        with self.assertRaises(StorageConflictError):
            await self.store.migrate_rerenders()


if __name__ == "__main__":
    asyncio.run(PublicationStore().migrate_rerenders())
    unittest.main(verbosity=2)
