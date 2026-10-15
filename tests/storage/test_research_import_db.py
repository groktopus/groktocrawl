"""Schema-9 complete imports and all preceding lifecycle formats on the new schema."""

import asyncio
import base64
import json
import unittest
from contextlib import asynccontextmanager
from uuid import uuid4

import test_import_store_db as imports
import test_research_bundle_db as exports
import test_research_publication_db as publications
from agent.experimental.artifact_bundle import bundle_member
from agent.experimental.canonical import admit_canonical_json
from agent.experimental.import_store import ImportStore
from agent.experimental.research_import_store import ResearchImportStore
from agent.experimental.source_store import StorageConflictError
from publication_fixture import CONTEXT
from research_publication_fixture import research_publication_payload


class OldLifecycleTests(publications.LegacyLifecycleTests):
    pass


class CompleteHistoryTests(publications.CompleteHistoryTests):
    pass


class PublicationTests(publications.CompletePublicationTests):
    pass


class ExportTests(exports.CompleteExportTests):
    pass


class CompleteImportTests(unittest.IsolatedAsyncioTestCase):
    grant = imports.ImportStorageTests.grant
    commit = imports.ImportStorageTests.commit
    sql = imports.ImportStorageTests.sql
    test_import_roundtrip_receipt_and_native_write_rejection = imports.ImportStorageTests.test_import_roundtrip_receipt_and_native_write_rejection
    test_origin_deletion_purges_copies_and_pending_grants = (
        imports.ImportStorageTests.test_origin_deletion_purges_copies_and_pending_grants
    )
    test_recipient_delete_preserves_origin_and_peer = (
        imports.ImportStorageTests.test_recipient_delete_preserves_origin_and_peer
    )
    test_pending_expiry_cancel_and_completed_grant_expiry = (
        imports.ImportStorageTests.test_pending_expiry_cancel_and_completed_grant_expiry
    )
    test_wrong_bundle_context_and_absent_origin = (
        imports.ImportStorageTests.test_wrong_bundle_context_and_absent_origin
    )
    test_import_commit_delete_race = (
        imports.ImportStorageTests.test_import_commit_delete_race
    )
    test_import_grant_delete_race = (
        imports.ImportStorageTests.test_import_grant_delete_race
    )
    test_recipient_quota_race = imports.ImportStorageTests.test_recipient_quota_race
    test_twenty_grant_fanout_bound_and_cancelled_metadata = (
        imports.ImportStorageTests.test_twenty_grant_fanout_bound_and_cancelled_metadata
    )
    test_origin_expiry_denies_grant_commit_and_reopen = (
        imports.ImportStorageTests.test_origin_expiry_denies_grant_commit_and_reopen
    )
    test_current_origin_retention_clamps_without_renewal = (
        imports.ImportStorageTests.test_current_origin_retention_clamps_without_renewal
    )
    test_stale_generation_and_import_migration_refusal = (
        imports.ImportStorageTests.test_stale_generation_and_import_migration_refusal
    )

    async def asyncSetUp(self):
        origin = exports.CompleteExportTests()
        await origin.asyncSetUp()
        self.store = ResearchImportStore()
        self.scope, self.root, self.revision = (
            origin.scope,
            origin.root,
            origin.revision,
        )
        self.publication = origin.published
        self.bundle = await self.export()
        self.recipient = uuid4()
        await self.store.provision_scope(self.recipient)
        self.origin = origin

    async def export(self):
        return await self.store.export_research_publication(
            self.scope, self.root, self.publication, CONTEXT
        )

    async def root_state(self):
        return (
            await self.sql(
                "SELECT charged,expires_at,current_research_revision FROM research_staging.roots WHERE root_id=%s",
                (self.root,),
            )
        )[0]

    async def test_format_separation_and_migration_refusal(self):
        target = await self.grant()
        for action in (
            ImportStore().commit_import(
                self.recipient, target, self.bundle.data, CONTEXT
            ),
            ImportStore().cancel_import(self.recipient, target),
        ):
            with self.assertRaises(StorageConflictError):
                await action
        await self.commit(target)
        with self.assertRaises(StorageConflictError):
            await ImportStore().read_import(self.recipient, target, CONTEXT)
        with self.assertRaises(StorageConflictError):
            await self.store.migrate_research_imports()
        old = imports.ImportStorageTests()
        await old.asyncSetUp()
        old_target = await old.grant()
        with self.assertRaises(StorageConflictError):
            await self.store.commit_import(
                old.recipient, old_target, old.bundle.data, CONTEXT
            )
        await old.store.cancel_import(old.recipient, old_target)

    async def test_complete_child_history_survives_copy(self):
        child = await self.origin.advance()
        pin = await self.store.read_research(self.scope, self.root, child)
        self.publication = await self.store.reserve_research_publication(
            self.scope, self.root, 1, child, 100000, CONTEXT
        )
        await self.store.commit_research_publication(
            self.scope,
            self.root,
            1,
            child,
            self.publication,
            research_publication_payload(pin, self.publication, CONTEXT),
            CONTEXT,
        )
        self.bundle = await self.export()
        target = await self.grant()
        await self.commit(target)
        copied = await self.store.read_import(self.recipient, target, CONTEXT)
        self.assertEqual(copied.bundle.revision_ids, (self.revision, child))
        self.assertEqual(copied.bundle.document, self.bundle)

    async def test_valid_rehashed_publication_must_match_live_origin(self):
        data = json.loads(self.bundle.data)
        raw = json.loads(base64.b64decode(data["members"]["publication.json"]["data"]))
        raw["publication"]["audits"][0]["reason"] = (
            "Different self-consistent audit rationale"
        )
        publication = admit_canonical_json(
            json.dumps(raw).encode(), schema_version=raw["schema_version"]
        )
        data["members"]["publication.json"] = bundle_member(publication.data)
        forged = admit_canonical_json(
            json.dumps(data).encode(), schema_version=data["schema_version"]
        )
        with self.assertRaisesRegex(StorageConflictError, "live origin"):
            await self.store.reserve_import(
                self.recipient,
                self.scope,
                self.root,
                self.publication,
                forged.data,
                forged.digest,
                CONTEXT,
            )

    async def test_complete_import_commit_faults(self):
        class Fault(ResearchImportStore):
            after = False

            @asynccontextmanager
            async def _transaction(self, **kwargs):
                async with super()._transaction(**kwargs) as conn:
                    yield conn
                    if not kwargs.get("read") and not self.after:
                        raise ConnectionError("before commit")
                if not kwargs.get("read") and self.after:
                    raise ConnectionError("lost acknowledgement")

        target = await self.grant()
        fault = Fault()
        with self.assertRaises(ConnectionError):
            await self.commit(target, fault)
        self.assertIsNone(await self.store.import_receipt(self.recipient, target))
        fault.after = True
        with self.assertRaises(ConnectionError):
            await self.commit(target, fault)
        self.assertEqual(await self.commit(target), target)

    async def test_opposing_complete_scope_imports(self):
        other = CompleteImportTests()
        await other.asyncSetUp()
        first, second = await asyncio.gather(
            self.grant(other.scope), other.grant(self.scope)
        )
        await asyncio.gather(
            self.store.commit_import(other.scope, first, self.bundle.data, CONTEXT),
            self.store.commit_import(self.scope, second, other.bundle.data, CONTEXT),
        )
        await self.store.read_import(other.scope, first, CONTEXT)
        await self.store.read_import(self.scope, second, CONTEXT)


if __name__ == "__main__":
    asyncio.run(ResearchImportStore().migrate_research_imports())
    unittest.main(verbosity=2)
