"""Opt-in integration checks against an isolated PostgreSQL/pgvector database."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

from agent.research_memory_pgvector import MemoryVector, PgvectorResearchMemoryStore

from tests.outcome_governance import governed_skip


def test_pgvector_research_memory_store_round_trip_and_expiry():
    dsn = os.getenv("RESEARCH_MEMORY_PGVECTOR_TEST_DSN")
    if not dsn:
        governed_skip(
            "set RESEARCH_MEMORY_PGVECTOR_TEST_DSN to an isolated test database",
            owner="repository-maintainer",
            issue="#672",
            classification="retained",
            environment="opt-in PostgreSQL integration DSN is not configured",
        )
    store = PgvectorResearchMemoryStore(dsn)
    store.ensure_schema()
    now = datetime.now(UTC)
    live_id, expired_id = str(uuid.uuid4()), str(uuid.uuid4())
    vector = [1.0] + [0.0] * 1023
    try:
        store.upsert(
            MemoryVector(
                live_id,
                "query alpha",
                "user-a",
                "fp-a",
                now,
                now + timedelta(hours=1),
                vector,
            )
        )
        # Upsert is idempotent for the stable memory UUID.
        store.upsert(
            MemoryVector(
                live_id,
                "query alpha",
                "user-a",
                "fp-a",
                now,
                now + timedelta(hours=1),
                vector,
            )
        )
        store.upsert(
            MemoryVector(
                expired_id,
                "expired",
                "user-a",
                None,
                now - timedelta(days=2),
                now - timedelta(days=1),
                vector,
            )
        )

        assert store.count() >= 2
        assert (
            store.search(vector, user_id="user-a", limit=5)[0]["memory_id"] == live_id
        )
        assert all(
            row["memory_id"] != live_id
            for row in store.search(vector, user_id="user-b")
        )
        assert store.delete_expired() >= 1
        assert expired_id not in store.snapshot()
        assert store.delete(live_id) is True
        assert store.delete(live_id) is False
    finally:
        store.delete(live_id)
        store.delete(expired_id)
