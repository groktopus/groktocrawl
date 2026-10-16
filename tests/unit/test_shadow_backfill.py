"""Unit tests for authoritative Qdrant to pgvector shadow reconciliation."""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SEMANTIC_SVC = Path(__file__).parents[2] / "semantic-svc"
sys.path.insert(0, str(SEMANTIC_SVC))

from shadow_backfill import reconcile_shadow, restore_qdrant_from_pgvector
from shadow_pgvector import ShadowRecord


def _point(point_id, vector, *, url="https://example.com"):
    return SimpleNamespace(
        id=point_id,
        vector={"v_bge-m3": vector},
        payload={"url": url, "title": str(point_id)},
    )


class _Shadow:
    def __init__(self):
        self.ids = {99}
        self.batches = []
        self.deleted = []
        self.schema_ready = False

    def ensure_schema(self):
        self.schema_ready = True

    def upsert_many(self, records):
        self.batches.append(records)
        self.ids.update(record.point_id for record in records)

    def active_ids(self, *, model):
        assert model == "v_bge-m3"
        return set(self.ids)

    def delete_many(self, point_ids):
        self.deleted.extend(point_ids)
        self.ids.difference_update(point_ids)


def test_reconcile_backfills_pages_and_tombstones_stale_rows():
    class _Qdrant:
        def scroll(self, **kwargs):
            if kwargs["offset"] is None:
                return [_point(1, [0.1, 0.2])], "next"
            return [_point(2, [0.3, 0.4])], None

    shadow = _Shadow()

    result = reconcile_shadow(
        _Qdrant(),
        shadow,
        collection="pages",
        model="v_bge-m3",
        batch_size=1,
    )

    assert shadow.schema_ready is True
    assert [[record.point_id for record in batch] for batch in shadow.batches] == [
        [1],
        [2],
    ]
    assert shadow.deleted == [99]
    assert result.authoritative_count == 2
    assert result.shadow_count == 2
    assert result.upserted_count == 2
    assert result.stale_deleted_count == 1
    assert result.parity is True


def test_reconcile_rejects_duplicate_scroll_ids():
    class _Qdrant:
        def scroll(self, **kwargs):
            return (
                ([_point(1, [0.1, 0.2])], "next")
                if kwargs["offset"] is None
                else ([_point(1, [0.1, 0.2])], None)
            )

    with pytest.raises(RuntimeError, match="repeated point IDs"):
        reconcile_shadow(
            _Qdrant(),
            _Shadow(),
            collection="pages",
            model="v_bge-m3",
            batch_size=1,
        )


def test_reconcile_rejects_missing_named_vector():
    class _Qdrant:
        def scroll(self, **kwargs):
            return [SimpleNamespace(id=1, vector={}, payload={})], None

    with pytest.raises(ValueError, match="missing named vector"):
        reconcile_shadow(
            _Qdrant(),
            _Shadow(),
            collection="pages",
            model="v_bge-m3",
        )


def test_reconcile_rejects_nonpositive_batch_size():
    with pytest.raises(ValueError, match="must be positive"):
        reconcile_shadow(
            object(),
            _Shadow(),
            collection="pages",
            model="v_bge-m3",
            batch_size=0,
        )


def test_restore_qdrant_reconciles_active_postgres_projection():
    class _Qdrant:
        def __init__(self):
            self.points = {
                1: SimpleNamespace(
                    id=1,
                    vector={"v_bge-m3": [0.1], "v_old-model": [0.9]},
                    payload={"url": "https://one", "old_metadata": "preserve"},
                ),
                9: SimpleNamespace(
                    id=9,
                    vector={"v_bge-m3": [0.9], "v_old-model": [0.8]},
                    payload={"url": "https://old", "other": "preserve"},
                ),
                10: SimpleNamespace(
                    id=10,
                    vector={"v_bge-m3": [0.7]},
                    payload={"url": "https://stale"},
                ),
            }
            self.deleted = []
            self.deleted_vectors = []
            self.payload_updates = []
            self.vector_updates = []
            self.upserts = []

        def scroll(self, **kwargs):
            return ([self.points[point_id] for point_id in sorted(self.points)], None)

        def delete(self, **kwargs):
            points = kwargs["points_selector"]
            self.deleted.append(points)
            for point_id in points:
                self.points.pop(point_id, None)

        def delete_vectors(self, **kwargs):
            point_ids = kwargs["points"]
            vector_names = kwargs["vectors"]
            self.deleted_vectors.append((point_ids, vector_names))
            for point_id in point_ids:
                for vector_name in vector_names:
                    self.points[point_id].vector.pop(vector_name, None)

        def set_payload(self, **kwargs):
            self.payload_updates.append(kwargs)
            for point_id in kwargs["points"]:
                self.points[point_id].payload.update(kwargs["payload"])

        def update_vectors(self, **kwargs):
            self.vector_updates.extend(kwargs["points"])
            for update in kwargs["points"]:
                self.points[update.id].vector.update(update.vector)

        def upsert(self, **kwargs):
            self.upserts.extend(kwargs["points"])
            for point in kwargs["points"]:
                self.points[int(point.id)] = SimpleNamespace(
                    id=int(point.id), vector=point.vector, payload=point.payload
                )

    class _Store:
        def active_ids(self, *, model):
            return {1, 2}

        def records(self, *, model, limit, offset):
            if offset:
                return []
            return [
                ShadowRecord(
                    1, "https://one", "One", [0.1, 0.2], model, {"url": "https://one"}
                ),
                ShadowRecord(
                    2, "https://two", "Two", [0.3, 0.4], model, {"url": "https://two"}
                ),
            ]

    qdrant = _Qdrant()
    result = restore_qdrant_from_pgvector(
        qdrant,
        _Store(),
        collection="pages",
        model="v_bge-m3",
        batch_size=10,
        prune_stale=True,
    )

    assert qdrant.deleted == [[10]]
    assert qdrant.deleted_vectors == [([9], ["v_bge-m3"])]
    assert qdrant.points[9].vector == {"v_old-model": [0.8]}
    assert qdrant.points[9].payload == {"url": "https://old", "other": "preserve"}
    assert qdrant.points[1].vector["v_old-model"] == [0.9]
    assert qdrant.points[1].payload["old_metadata"] == "preserve"
    assert 10 not in qdrant.points
    assert len(qdrant.upserts) == 1
    assert len(qdrant.vector_updates) == 1
    assert result["parity"] is True
    assert result["qdrant_missing_ids"] == result["qdrant_extra_ids"] == 0


def test_restore_qdrant_preserves_stale_points_without_explicit_prune():
    class _Qdrant:
        points = {
            1: SimpleNamespace(id=1, vector={"v_bge-m3": [0.1]}, payload={}),
            9: SimpleNamespace(id=9, vector={"v_other-model": [0.9]}, payload={}),
        }

        def scroll(self, **kwargs):
            return ([self.points[point_id] for point_id in sorted(self.points)], None)

        def upsert(self, **kwargs):
            for point in kwargs["points"]:
                self.points[int(point.id)] = SimpleNamespace(
                    id=int(point.id), vector=point.vector, payload=point.payload
                )

        def update_vectors(self, **kwargs):
            for update in kwargs["points"]:
                self.points[update.id].vector.update(update.vector)

        def set_payload(self, **kwargs):
            for point_id in kwargs["points"]:
                self.points[point_id].payload.update(kwargs["payload"])

        def delete(self, **kwargs):
            pytest.fail("restore must not delete Qdrant rows without prune_stale")

    class _Store:
        def active_ids(self, *, model):
            return {1, 2}

        def records(self, *, model, limit, offset):
            if offset:
                return []
            return [
                ShadowRecord(
                    point_id, f"https://{point_id}", str(point_id), [0.1, 0.2], model, {}
                )
                for point_id in (1, 2)
            ]

    qdrant = _Qdrant()
    result = restore_qdrant_from_pgvector(
        qdrant, _Store(), collection="pages", model="v_bge-m3", batch_size=10
    )

    assert set(qdrant.points) == {1, 2, 9}
    assert qdrant.points[9].vector == {"v_other-model": [0.9]}
    assert result["parity"] is True
    assert result["qdrant_stale_candidate_count"] == 1
    assert result["qdrant_stale_deleted_count"] == 0
