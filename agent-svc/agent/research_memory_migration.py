"""Explicit Qdrant-to-pgvector migration and parity check for research memory.

Run from the agent image with the same private Valkey and PostgreSQL settings
as agent-svc. The commands never delete or mutate Qdrant or Valkey records.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from typing import Any

import httpx
from redis import Redis

from .research_memory_pgvector import MemoryVector, PgvectorResearchMemoryStore


def _dt(value: Any) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError("missing timestamp in Qdrant memory payload")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(normalized)
    return (
        parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
    )


def _source_rows(qdrant_url: str, redis_url: str) -> tuple[list[MemoryVector], int]:
    redis = Redis.from_url(redis_url, decode_responses=True)
    rows: list[MemoryVector] = []
    skipped = 0
    offset: str | int | None = None
    now = datetime.now(UTC)
    with httpx.Client(timeout=30) as client:
        while True:
            payload: dict[str, Any] = {
                "limit": 100,
                "with_payload": True,
                "with_vector": True,
            }
            if offset is not None:
                payload["offset"] = offset
            response = client.post(
                f"{qdrant_url.rstrip('/')}/collections/research_memory/points/scroll",
                json=payload,
            )
            if response.status_code == 404:
                return rows, skipped
            response.raise_for_status()
            result = response.json().get("result", {})
            points = result.get("points", [])
            for point in points:
                point_payload = point.get("payload") or {}
                memory_id = str(point_payload.get("memory_id") or point.get("id") or "")
                if not memory_id:
                    raise ValueError("Qdrant research memory point is missing its ID")
                raw_artifact = redis.get(f"memory:{memory_id}:data")
                if raw_artifact is None:
                    skipped += 1
                    continue
                artifact = json.loads(raw_artifact)
                created_at = _dt(
                    artifact.get("created_at") or point_payload.get("timestamp")
                )
                expires_at = _dt(
                    artifact.get("expires_at") or point_payload.get("expires_at")
                )
                if expires_at <= now:
                    skipped += 1
                    continue
                vector = point.get("vector")
                if isinstance(vector, dict):
                    raise ValueError(
                        "research_memory collection unexpectedly uses named vectors"
                    )
                if not isinstance(vector, list) or len(vector) != 1024:
                    raise ValueError(
                        f"invalid embedding dimensions for memory {memory_id}"
                    )
                rows.append(
                    MemoryVector(
                        memory_id=memory_id,
                        query=str(
                            artifact.get("query") or point_payload.get("query") or ""
                        ),
                        user_id=artifact.get("user_id", point_payload.get("user_id")),
                        fingerprint=artifact.get(
                            "fingerprint", point_payload.get("fingerprint")
                        ),
                        created_at=created_at,
                        expires_at=expires_at,
                        embedding=vector,
                    )
                )
            offset = result.get("next_page_offset")
            if offset is None or not points:
                break
    return rows, skipped


def run(command: str) -> dict[str, Any]:
    dsn = os.getenv("RESEARCH_MEMORY_PGVECTOR_DSN", "").strip()
    if not dsn:
        dsn = os.getenv("DURABLE_RESEARCH_POSTGRES_DSN", "").strip()
    if not dsn:
        raise ValueError(
            "RESEARCH_MEMORY_PGVECTOR_DSN or DURABLE_RESEARCH_POSTGRES_DSN is required"
        )
    redis_url = os.getenv("VALKEY_URL", "redis://valkey:6379/0")
    qdrant_url = os.getenv("QDRANT_URL", "http://qdrant:6333")
    store = PgvectorResearchMemoryStore(dsn)
    store.ensure_schema()
    if command == "init":
        return {"schema_ready": True, "qdrant_changed": False, "valkey_changed": False}

    if command == "restore-qdrant":
        snapshot_rows = store.snapshot()
        redis = Redis.from_url(redis_url, decode_responses=True)
        restored = 0
        with httpx.Client(timeout=30) as client:
            collection_url = f"{qdrant_url.rstrip('/')}/collections/research_memory"
            collection = client.get(collection_url)
            if collection.status_code == 404:
                created = client.put(
                    collection_url,
                    json={"vectors": {"size": 1024, "distance": "Cosine"}},
                )
                created.raise_for_status()
            elif collection.status_code >= 400:
                collection.raise_for_status()
            batch: list[dict[str, Any]] = []
            for memory_id, snapshot_row in snapshot_rows.items():
                if not redis.exists(f"memory:{memory_id}:data") or snapshot_row[
                    "expires_at"
                ] <= datetime.now(UTC):
                    continue
                payload = {
                    "memory_id": memory_id,
                    "query": snapshot_row["query"],
                    "user_id": snapshot_row["user_id"],
                    "timestamp": snapshot_row["created_at"].isoformat(),
                    "expires_at": snapshot_row["expires_at"].isoformat(),
                }
                if snapshot_row["fingerprint"]:
                    payload["fingerprint"] = snapshot_row["fingerprint"]
                batch.append(
                    {
                        "id": memory_id,
                        "vector": snapshot_row["embedding"],
                        "payload": payload,
                    }
                )
                if len(batch) == 100:
                    client.put(
                        f"{collection_url}/points", json={"points": batch}
                    ).raise_for_status()
                    restored += len(batch)
                    batch.clear()
            if batch:
                client.put(
                    f"{collection_url}/points", json={"points": batch}
                ).raise_for_status()
                restored += len(batch)
        return {
            "restored_live_points": restored,
            "pgvector_rows": len(snapshot_rows),
            "qdrant_changed": True,
            "valkey_changed": False,
        }

    source_rows, skipped = _source_rows(qdrant_url, redis_url)
    if command == "backfill":
        for source_row in source_rows:
            store.upsert(source_row)
        return {
            "source_live_points": len(source_rows),
            "expired_or_missing_artifact_skipped": skipped,
            "pgvector_rows": store.count(),
            "qdrant_changed": False,
            "valkey_changed": False,
        }

    now = datetime.now(UTC)
    redis = Redis.from_url(redis_url, decode_responses=True)
    pg_rows = {
        memory_id: row
        for memory_id, row in store.snapshot().items()
        if row["expires_at"] > now
        and redis.exists(f"memory:{memory_id}:data")
    }
    source_by_id = {row.memory_id: row for row in source_rows}
    missing = sorted(set(source_by_id) - set(pg_rows))
    extra = sorted(set(pg_rows) - set(source_by_id))
    vector_mismatches: list[str] = []
    metadata_mismatches: list[str] = []
    for memory_id in sorted(set(source_by_id) & set(pg_rows)):
        source = source_by_id[memory_id]
        target = pg_rows[memory_id]
        if any(
            abs(float(a) - float(b)) > 1e-6
            for a, b in zip(source.embedding, target["embedding"], strict=True)
        ):
            vector_mismatches.append(memory_id)
        if (source.query, source.user_id, source.fingerprint) != (
            target["query"],
            target["user_id"],
            target["fingerprint"],
        ):
            metadata_mismatches.append(memory_id)
    return {
        "source_live_points": len(source_by_id),
        "pgvector_rows": len(pg_rows),
        "expired_or_missing_artifact_skipped": skipped,
        "missing_ids": len(missing),
        "extra_ids": len(extra),
        "vector_mismatches": len(vector_mismatches),
        "metadata_mismatches": len(metadata_mismatches),
        "parity": not (missing or extra or vector_mismatches or metadata_mismatches),
        "qdrant_changed": False,
        "valkey_changed": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=("init", "backfill", "verify", "restore-qdrant")
    )
    args = parser.parse_args(argv)
    try:
        result = run(args.command)
    except Exception as exc:
        sys.stderr.write(
            json.dumps({"ok": False, "error_type": type(exc).__name__}) + "\n"
        )
        return 1
    sys.stdout.write(json.dumps(result, sort_keys=True) + "\n")
    if args.command == "verify" and not result["parity"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
