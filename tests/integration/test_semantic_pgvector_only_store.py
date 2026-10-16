"""Opt-in real-PostgreSQL checks for the semantic pgvector-only store."""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

from tests.outcome_governance import governed_skip

sys.path.insert(0, str(Path(__file__).parents[2] / "semantic-svc"))

from shadow_pgvector import PgvectorShadowStore, ShadowConfig, ShadowRecord


def test_pgvector_only_store_round_trip_and_capacity_cleanup():
    dsn = os.getenv("PGVECTOR_SEMANTIC_TEST_DSN")
    if not dsn:
        governed_skip(
            "set PGVECTOR_SEMANTIC_TEST_DSN to an isolated test database",
            owner="repository-maintainer",
            issue="#672",
            classification="retained",
            environment="opt-in PostgreSQL integration DSN is not configured",
        )

    schema = f"gtc_semantic_it_{uuid.uuid4().hex[:16]}"
    config = ShadowConfig(
        mode="pgvector_only",
        dsn=dsn,
        schema=schema,
        table="pages",
        sample_rate=1.0,
        score_tolerance=0.0001,
        timeout_seconds=5,
    )
    store = PgvectorShadowStore(config, dimension=1024)
    point_ids = [uuid.uuid4().int % (10**18) for _ in range(2)]
    vectors = [[1.0, 0.0] + [0.0] * 1022, [0.0, 1.0] + [0.0] * 1022]
    model = "v_bge-m3"
    try:
        store.ensure_schema()
        store.upsert_many(
            [
                ShadowRecord(
                    point_ids[index],
                    f"https://example.test/{index}",
                    f"Page {index}",
                    vectors[index],
                    model,
                    {"url": f"https://example.test/{index}", "content_excerpt": str(index)},
                )
                for index in range(2)
            ]
        )

        assert store.count(model=model) == 2
        assert store.get_payload(point_ids[0], model=model)["url"] == (
            "https://example.test/0"
        )
        assert store.search(vectors[0], model=model, limit=2)[0].point_id == point_ids[0]
        assert {record.point_id for record in store.records(model=model)} == set(point_ids)

        assert store.evict_to_limit(model=model, max_docs=1) == 1
        assert len(store.active_ids(model=model)) == 1
    finally:
        store.delete_many(point_ids)
        store.close()
        import psycopg

        with psycopg.connect(dsn, autocommit=True) as connection:
            connection.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
