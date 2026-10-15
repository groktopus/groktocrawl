"""Exercise the semantic pgvector adapter against the shared retained store."""

from __future__ import annotations

import os

from shadow_pgvector import PgvectorShadowStore, ShadowConfig, ShadowRecord


def main() -> None:
    config = ShadowConfig(
        mode="pgvector",
        dsn=(
            f"postgresql://{os.environ['PGUSER']}@{os.environ['PGHOST']}:5432/"
            f"{os.environ['PGDATABASE']}"
        ),
        schema="groktocrawl_x_semantic_shadow_probe",
        table="pages",
        sample_rate=1,
        score_tolerance=0.0001,
        timeout_seconds=10,
    )
    store = PgvectorShadowStore(config, dimension=3)
    large_id = 2**64 - 1
    try:
        store.ensure_schema()
        store.upsert_many(
            [
                ShadowRecord(
                    point_id=large_id,
                    url="https://fixture.invalid/large",
                    title="Large ID",
                    vector=[1.0, 0.0, 0.0],
                    model="v_fixture",
                    payload={"fixture": True},
                ),
                ShadowRecord(
                    point_id=2,
                    url="https://fixture.invalid/other",
                    title="Other",
                    vector=[0.0, 1.0, 0.0],
                    model="v_fixture",
                    payload={"fixture": True},
                ),
            ]
        )
        assert store.active_ids(model="v_fixture") == {large_id, 2}
        assert store.count(model="v_fixture") == 2
        results = store.search([1.0, 0.0, 0.0], model="v_fixture", limit=2)
        assert [result.point_id for result in results] == [large_id, 2]
        assert results[0].score > results[1].score
        store.delete_many([large_id])
        assert store.active_ids(model="v_fixture") == {2}
        assert store.count(model="v_fixture") == 1
    finally:
        store.close()


if __name__ == "__main__":
    main()
