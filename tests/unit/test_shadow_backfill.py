"""Unit tests for authoritative Qdrant to pgvector shadow reconciliation."""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SEMANTIC_SVC = Path(__file__).parents[2] / "semantic-svc"
sys.path.insert(0, str(SEMANTIC_SVC))

from shadow_backfill import reconcile_shadow


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
