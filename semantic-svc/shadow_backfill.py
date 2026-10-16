"""Reconcile the experimental pgvector shadow from authoritative Qdrant data."""

from __future__ import annotations

import hashlib
import json
import math
import os
import sys
import time
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any

from qdrant_client import QdrantClient
from shadow_pgvector import PgvectorShadowStore, ShadowConfig, ShadowRecord


@dataclass(frozen=True)
class BackfillResult:
    collection: str
    model: str
    authoritative_count: int
    shadow_count: int
    upserted_count: int
    stale_deleted_count: int
    id_set_sha256: str
    duration_ms: int
    parity: bool


def _point_to_record(point: Any, model: str) -> ShadowRecord:
    vectors = point.vector
    if not isinstance(vectors, Mapping) or model not in vectors:
        raise ValueError(f"Qdrant point {point.id} is missing named vector {model}")
    payload = dict(point.payload or {})
    return ShadowRecord(
        point_id=int(point.id),
        url=str(payload.get("url", "")),
        title=str(payload.get("title", "")),
        vector=vectors[model],
        model=model,
        payload=payload,
    )


def reconcile_shadow(
    qdrant: Any,
    shadow: PgvectorShadowStore,
    *,
    collection: str,
    model: str,
    batch_size: int = 256,
) -> BackfillResult:
    """Copy all authoritative points and tombstone rows absent from Qdrant."""
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    started = time.monotonic()
    shadow.ensure_schema()
    authoritative_ids: set[int] = set()
    upserted = 0
    offset = None

    while True:
        points, next_offset = qdrant.scroll(
            collection_name=collection,
            limit=batch_size,
            offset=offset,
            with_payload=True,
            with_vectors=[model],
        )
        records = [_point_to_record(point, model) for point in points]
        duplicate_ids = authoritative_ids.intersection(
            record.point_id for record in records
        )
        if duplicate_ids:
            raise RuntimeError(
                f"Qdrant scroll repeated point IDs: {sorted(duplicate_ids)}"
            )
        authoritative_ids.update(record.point_id for record in records)
        shadow.upsert_many(records)
        upserted += len(records)
        if next_offset is None:
            break
        offset = next_offset

    stale_ids = shadow.active_ids(model=model) - authoritative_ids
    shadow.delete_many(sorted(stale_ids))
    shadow_ids = shadow.active_ids(model=model)
    digest = hashlib.sha256(
        ",".join(str(point_id) for point_id in sorted(authoritative_ids)).encode()
    ).hexdigest()
    return BackfillResult(
        collection=collection,
        model=model,
        authoritative_count=len(authoritative_ids),
        shadow_count=len(shadow_ids),
        upserted_count=upserted,
        stale_deleted_count=len(stale_ids),
        id_set_sha256=digest,
        duration_ms=round((time.monotonic() - started) * 1000),
        parity=authoritative_ids == shadow_ids,
    )


def main() -> int:
    config = ShadowConfig.from_env()
    if not config.enabled:
        raise SystemExit("VECTOR_STORE_MODE must be shadow_pgvector for backfill")
    dimension = int(os.getenv("EMBED_DIM", "1024"))
    collection = os.getenv("QDRANT_COLLECTION", "groktocrawl_pages")
    model = os.getenv("ACTIVE_EMBED_MODEL", "v_bge-m3")
    batch_size = int(os.getenv("PGVECTOR_SHADOW_BACKFILL_BATCH_SIZE", "256"))
    qdrant_timeout = math.ceil(float(os.getenv("QDRANT_CLIENT_TIMEOUT", "10")))
    qdrant = QdrantClient(
        url=os.getenv("QDRANT_URL", "http://qdrant:6333"), timeout=qdrant_timeout
    )
    shadow = PgvectorShadowStore(config, dimension)
    try:
        result = reconcile_shadow(
            qdrant,
            shadow,
            collection=collection,
            model=model,
            batch_size=batch_size,
        )
        sys.stdout.write(json.dumps(asdict(result), sort_keys=True) + "\n")
        return 0 if result.parity else 1
    finally:
        qdrant.close()
        shadow.close()


if __name__ == "__main__":
    raise SystemExit(main())
