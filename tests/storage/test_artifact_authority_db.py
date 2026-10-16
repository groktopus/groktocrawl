"""Schema-13 atomic artifact authority and deletion checks."""

import asyncio
import unittest
from uuid import uuid4

from agent.experimental.artifact_authority import ArtifactAuthority
from agent.experimental.slopsearx_provenance_store import SlopSearXProvenanceStore
from agent.experimental.source_store import StorageConflictError


class ArtifactAuthorityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = ArtifactAuthority()
        self.scope = uuid4()
        self.research = uuid4()
        self.run = uuid4()
        self.artifact_set = uuid4()
        await self.store.provision_scope(self.scope)
        self.manifest = b'{"schema_version":"render-manifest-prototype/1"}'
        self.artifacts = {
            "summary": ("summary-1", b"summary bytes"),
            "analysis": ("analysis-1", b"analysis bytes"),
            "dossier": ("dossier-1", b"dossier bytes"),
        }

    async def test_atomic_roundtrip_and_identical_replay(self):
        committed = await self.store.commit(
            self.scope,
            self.research,
            self.run,
            self.artifact_set,
            self.manifest,
            self.artifacts,
        )
        replayed = await self.store.commit(
            self.scope,
            self.research,
            self.run,
            self.artifact_set,
            self.manifest,
            self.artifacts,
        )
        reopened = await ArtifactAuthority().read_run(self.scope, self.run)
        self.assertEqual(replayed, committed)
        self.assertEqual(reopened, committed)
        self.assertEqual(
            {value.layer: value.body for value in reopened.artifacts},
            {key: value[1] for key, value in self.artifacts.items()},
        )

    async def test_conflicting_replay_fails_closed(self):
        await self.store.commit(
            self.scope,
            self.research,
            self.run,
            self.artifact_set,
            self.manifest,
            self.artifacts,
        )
        changed = {**self.artifacts, "summary": ("summary-1", b"changed")}
        with self.assertRaises(StorageConflictError):
            await self.store.commit(
                self.scope,
                self.research,
                self.run,
                self.artifact_set,
                self.manifest,
                changed,
            )

    async def test_deletion_removes_bytes_and_blocks_replay(self):
        await self.store.commit(
            self.scope,
            self.research,
            self.run,
            self.artifact_set,
            self.manifest,
            self.artifacts,
        )
        await self.store.delete(self.scope, self.research)
        with self.assertRaises(StorageConflictError):
            await self.store.read(self.scope, self.research)
        with self.assertRaises(StorageConflictError):
            await self.store.commit(
                self.scope,
                self.research,
                self.run,
                self.artifact_set,
                self.manifest,
                self.artifacts,
            )
        async with self.store._transaction(read=True) as conn:
            row = await (
                await conn.execute(
                    "SELECT deleted,manifest FROM research_staging.research_artifact_sets WHERE scope_id=%s AND research_id=%s",
                    (self.scope, self.research),
                )
            ).fetchone()
            children = await (
                await conn.execute(
                    "SELECT count(*) AS count FROM research_staging.research_artifacts WHERE scope_id=%s AND research_id=%s",
                    (self.scope, self.research),
                )
            ).fetchone()
        self.assertEqual(row, {"deleted": True, "manifest": None})
        self.assertEqual(children, {"count": 0})

    async def test_deletion_before_commit_fences_late_publication(self):
        await self.store.delete(self.scope, self.research)
        await self.store.delete(self.scope, self.research)
        with self.assertRaises(StorageConflictError):
            await self.store.commit(
                self.scope,
                self.research,
                self.run,
                self.artifact_set,
                self.manifest,
                self.artifacts,
            )

    async def test_exact_evidence_replay_scope_expiry_and_delete(self):
        evidence = {
            "source-1": ("Complete Unicode evidence: 🐙".encode(), "text/plain")
        }
        await self.store.commit(
            self.scope,
            self.research,
            self.run,
            self.artifact_set,
            self.manifest,
            self.artifacts,
            evidence=evidence,
            knowledge=b'{"context":{"objective":"Retained synthetic policy"}}',
        )
        assert (
            await self.store.read_evidence(self.scope, self.research, "source-1")
            == evidence["source-1"]
        )
        assert (
            await self.store.read_knowledge(self.scope, self.research)
            == b'{"context":{"objective":"Retained synthetic policy"}}'
        )
        catalog = await self.store.list_retained(self.scope, limit=1, offset=0)
        self.assertEqual(len(catalog), 1)
        self.assertEqual(catalog[0]["objective"], "Retained synthetic policy")
        self.assertEqual(catalog[0]["run_id"], self.run)
        self.assertEqual(await self.store.list_retained(self.scope, offset=1), [])
        self.assertEqual(await self.store.list_retained(uuid4()), [])
        restored = await self.store.lookup(self.scope, run=self.run)
        self.assertEqual(restored.research_id, self.research)
        with self.assertRaises(StorageConflictError):
            await self.store.read_evidence(uuid4(), self.research, "source-1")
        with self.assertRaises(StorageConflictError):
            await self.store.commit(
                self.scope,
                self.research,
                self.run,
                self.artifact_set,
                self.manifest,
                self.artifacts,
                evidence={"source-1": (b"tampered", "text/plain")},
            )
        await self.store.delete(self.scope, self.research)
        self.assertEqual(await self.store.list_retained(self.scope), [])
        with self.assertRaises(StorageConflictError) as deleted:
            await self.store.lookup(self.scope, run=self.run)
        self.assertEqual(deleted.exception.status_code, 410)
        async with self.store._transaction() as conn:
            cleared = await (
                await conn.execute(
                    "SELECT knowledge,knowledge_digest,objective FROM research_staging.research_artifact_sets WHERE scope_id=%s AND research_id=%s",
                    (self.scope, self.research),
                )
            ).fetchone()
        self.assertEqual(
            cleared, {"knowledge": None, "knowledge_digest": None, "objective": None}
        )
        with self.assertRaises(StorageConflictError):
            await self.store.read_evidence(self.scope, self.research, "source-1")

    async def test_expired_evidence_fails_closed(self):
        await self.store.commit(
            self.scope,
            self.research,
            self.run,
            self.artifact_set,
            self.manifest,
            self.artifacts,
            evidence={"source-1": (b"exact", "text/plain")},
        )
        async with self.store._transaction() as conn:
            await conn.execute(
                "UPDATE research_staging.research_artifact_sets SET expires_at=now()-interval '1 second' WHERE scope_id=%s AND research_id=%s",
                (self.scope, self.research),
            )
        with self.assertRaises(StorageConflictError):
            await self.store.read_evidence(self.scope, self.research, "source-1")


if __name__ == "__main__":
    store = ArtifactAuthority()
    asyncio.run(store.migrate_artifact_authority())
    asyncio.run(store.migrate_deletion_fence())
    asyncio.run(SlopSearXProvenanceStore().migrate())
    asyncio.run(store.migrate_evidence_authority())
    unittest.main(verbosity=2)
