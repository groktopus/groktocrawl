"""Real migrated-database structural revision contracts."""

import asyncio
import hashlib
import json
import unittest
from contextlib import asynccontextmanager
from uuid import uuid4

import psycopg
from agent.experimental.revision_store import RevisionStore
from agent.experimental.source_store import SourceStore, StorageConflictError


def payload(
    scope,
    root,
    revision,
    snapshot,
    body,
    parent=None,
    claim="Source says retained evidence.",
):
    text = body.decode()
    return json.dumps(
        {
            "schema_version": "retained-structure-prototype/1",
            "parent_revision_id": str(parent) if parent else None,
            "structure": {
                "schema_version": "knowledge-structure-prototype/1",
                "scope_id": str(scope),
                "research_id": str(root),
                "revision_id": str(revision),
                "snapshots": [
                    {
                        "snapshot_id": str(snapshot),
                        "canonical_url": "https://example.test/revision",
                        "retrieved_at": "2026-09-01T00:00:00Z",
                        "normalization_version": "utf8-exact/1",
                        "media_type": "text/plain",
                        "text": text,
                        "digest": hashlib.sha256(body).hexdigest(),
                    }
                ],
                "evidence": [
                    {
                        "evidence_id": "e-1",
                        "snapshot_id": str(snapshot),
                        "start": 0,
                        "end": len(text),
                        "quote": text,
                        "quote_digest": hashlib.sha256(body).hexdigest(),
                    }
                ],
                "claims": [
                    {
                        "claim_id": "c-1",
                        "text": claim,
                        "kind": "source_statement",
                        "qualifiers": ["Fixture source attribution only."],
                    }
                ],
                "relationships": [
                    {
                        "relationship_id": "r-1",
                        "kind": "supports",
                        "source_id": "e-1",
                        "target_id": "c-1",
                        "rationale": "Fixture relation; not semantic verification.",
                    }
                ],
            },
        }
    ).encode()


class RevisionStorageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = RevisionStore()
        self.scope = uuid4()
        await self.store.provision_scope(self.scope)
        self.root = await self.store.create_root(self.scope)
        self.body = b"Retained evidence for structural revisions."
        op = await self.store.reserve(self.scope, self.root, 1, 1000)
        self.snapshot = await self.store.commit_source(
            self.scope, self.root, 1, op, self.body, "https://example.test/revision"
        )

    async def prepare(self, parent=None):
        revision = await self.store.reserve_revision(
            self.scope, self.root, 1, parent, 10000
        )
        return revision, payload(
            self.scope, self.root, revision, self.snapshot, self.body, parent
        )

    async def test_commit_reopen_and_replay(self):
        revision, raw = await self.prepare()
        await self.store.commit_revision(self.scope, self.root, 1, revision, raw)
        read = await RevisionStore().read_revision(self.scope, self.root, revision)
        self.assertEqual(read.structure.snapshots[0].text.encode(), self.body)
        self.assertEqual(
            await self.store.revision_receipt(self.scope, self.root, revision),
            read.document.digest,
        )
        self.assertEqual(
            await self.store.commit_revision(self.scope, self.root, 1, revision, raw),
            revision,
        )
        altered = json.loads(raw)
        altered["structure"]["claims"][0]["text"] = "Changed"
        with self.assertRaises(StorageConflictError):
            await self.store.commit_revision(
                self.scope, self.root, 1, revision, json.dumps(altered).encode()
            )

    async def test_competing_parent_and_immutable_entities(self):
        first, first_raw = await self.prepare()
        second, second_raw = await self.prepare()
        outcomes = await asyncio.gather(
            self.store.commit_revision(self.scope, self.root, 1, first, first_raw),
            self.store.commit_revision(self.scope, self.root, 1, second, second_raw),
            return_exceptions=True,
        )
        self.assertEqual(
            sum(isinstance(value, StorageConflictError) for value in outcomes), 1
        )
        winner = next(value for value in outcomes if not isinstance(value, Exception))
        loser = second if winner == first else first
        await self.store.cancel_revision(self.scope, self.root, loser)
        child, _ = await self.prepare(winner)
        with self.assertRaises(StorageConflictError):
            await self.store.commit_revision(
                self.scope,
                self.root,
                1,
                child,
                payload(
                    self.scope,
                    self.root,
                    child,
                    self.snapshot,
                    self.body,
                    winner,
                    claim="Reassigned claim identity",
                ),
            )
        raw = payload(self.scope, self.root, child, self.snapshot, self.body, winner)
        await self.store.commit_revision(self.scope, self.root, 1, child, raw)
        self.assertEqual(
            (await self.store.read_revision(self.scope, self.root, child)).parent_id,
            winner,
        )

    async def test_wrong_source_root_bytes_and_ledger(self):
        revision, raw = await self.prepare()
        other = await self.store.create_root(self.scope)
        op = await self.store.reserve(self.scope, other, 1, 1000)
        other_snapshot = await self.store.commit_source(
            self.scope, other, 1, op, self.body, "https://example.test/revision"
        )
        with self.assertRaises(StorageConflictError):
            await self.store.commit_revision(
                self.scope,
                self.root,
                1,
                revision,
                payload(self.scope, self.root, revision, other_snapshot, self.body),
            )
        with self.assertRaises(StorageConflictError):
            await self.store.commit_revision(
                self.scope,
                self.root,
                1,
                revision,
                payload(
                    self.scope, self.root, revision, self.snapshot, b"Forged content"
                ),
            )
        await self.store.commit_revision(self.scope, self.root, 1, revision, raw)
        async with await psycopg.AsyncConnection.connect() as conn:
            await conn.execute(
                "DELETE FROM research_staging.revision_sources WHERE scope_id=%s AND root_id=%s",
                (self.scope, self.root),
            )
        with self.assertRaises(StorageConflictError):
            await self.store.read_revision(self.scope, self.root, revision)
        await self.store.delete_root(self.scope, self.root)

    async def test_delete_clears_revisions_and_blocks_late_writer(self):
        first, raw = await self.prepare()
        await self.store.commit_revision(self.scope, self.root, 1, first, raw)
        pending, pending_raw = await self.prepare(first)
        await SourceStore().delete_root(self.scope, self.root)
        with self.assertRaises(StorageConflictError):
            await self.store.read_revision(self.scope, self.root, first)
        with self.assertRaises(StorageConflictError):
            await self.store.commit_revision(
                self.scope, self.root, 1, pending, pending_raw
            )
        self.assertIsNotNone(
            await self.store.revision_receipt(self.scope, self.root, first)
        )
        async with await psycopg.AsyncConnection.connect() as conn:
            row = await (
                await conn.execute(
                    "SELECT charged,current_revision FROM research_staging.roots WHERE scope_id=%s AND root_id=%s",
                    (self.scope, self.root),
                )
            ).fetchone()
            self.assertEqual(row, (0, None))
            row = await (
                await conn.execute(
                    "SELECT count(*) FROM research_staging.revisions WHERE scope_id=%s AND root_id=%s",
                    (self.scope, self.root),
                )
            ).fetchone()
            self.assertEqual(row[0], 0)

    async def test_lost_ack_receipt_and_reservation_limit(self):
        class LostAckStore(RevisionStore):
            @asynccontextmanager
            async def _transaction(self, **kwargs):
                async with super()._transaction(**kwargs) as conn:
                    yield conn
                raise ConnectionError("simulated post-commit ACK loss")

        small = await self.store.reserve_revision(self.scope, self.root, 1, None, 1)
        with self.assertRaises(StorageConflictError):
            await self.store.commit_revision(
                self.scope,
                self.root,
                1,
                small,
                payload(self.scope, self.root, small, self.snapshot, self.body),
            )
        await self.store.cancel_revision(self.scope, self.root, small)
        revision, raw = await self.prepare()
        with self.assertRaises(ConnectionError):
            await LostAckStore().commit_revision(
                self.scope, self.root, 1, revision, raw
            )
        self.assertIsNotNone(
            await self.store.revision_receipt(self.scope, self.root, revision)
        )
        self.assertEqual(
            await self.store.commit_revision(self.scope, self.root, 1, revision, raw),
            revision,
        )

    async def test_removed_identity_cannot_be_reassigned(self):
        first, raw = await self.prepare()
        await self.store.commit_revision(self.scope, self.root, 1, first, raw)
        second, raw = await self.prepare(first)
        reduced = json.loads(raw)
        reduced["structure"]["claims"] = []
        reduced["structure"]["relationships"] = []
        await self.store.commit_revision(
            self.scope, self.root, 1, second, json.dumps(reduced).encode()
        )
        third, _ = await self.prepare(second)
        with self.assertRaises(StorageConflictError):
            await self.store.commit_revision(
                self.scope,
                self.root,
                1,
                third,
                payload(
                    self.scope,
                    self.root,
                    third,
                    self.snapshot,
                    self.body,
                    second,
                    claim="Reassigned after removal",
                ),
            )
        await self.store.cancel_revision(self.scope, self.root, third)

    async def test_migration_refuses_reapplication(self):
        with self.assertRaises(StorageConflictError):
            await self.store.migrate_revisions()
        self.assertEqual(
            (
                await SourceStore().read_source(self.scope, self.root, self.snapshot)
            ).body,
            self.body,
        )


if __name__ == "__main__":
    asyncio.run(RevisionStore().migrate_revisions())
    unittest.main(verbosity=2)
