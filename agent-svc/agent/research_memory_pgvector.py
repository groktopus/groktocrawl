"""PostgreSQL projection for research-memory embeddings.

Artifacts remain in Valkey under the ADR-0041 key/TTL contract. This table
contains only searchable vectors and lookup metadata, so it can be rebuilt
from Qdrant without changing artifact identities or payloads.
"""

from __future__ import annotations

import math
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

SCHEMA = "research_memory"
TABLE = "vectors"


def vector_literal(vector: Sequence[float]) -> str:
    if not vector or not all(math.isfinite(float(value)) for value in vector):
        raise ValueError("embedding vector must contain finite values")
    return "[" + ",".join(format(float(value), ".17g") for value in vector) + "]"


@dataclass(frozen=True)
class MemoryVector:
    memory_id: str
    query: str
    user_id: str | None
    fingerprint: str | None
    created_at: datetime
    expires_at: datetime
    embedding: Sequence[float]


class PgvectorResearchMemoryStore:
    """Small synchronous psycopg adapter; callers run it in a worker thread."""

    def __init__(self, dsn: str, dimension: int = 1024, timeout_seconds: float = 3):
        self.dsn = dsn
        self.dimension = dimension
        self.timeout_seconds = timeout_seconds
        self._schema_ready = False
        self._schema_lock = threading.Lock()

    def _connect(self):
        import psycopg

        return psycopg.connect(
            self.dsn,
            autocommit=True,
            connect_timeout=max(1, int(self.timeout_seconds)),
            options=f"-c statement_timeout={max(1, int(self.timeout_seconds * 1000))}",
        )

    def ensure_schema(self) -> None:
        with self._schema_lock:
            if self._schema_ready:
                return
            with self._connect() as conn:
                conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
                conn.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
                conn.execute(
                    f"CREATE TABLE IF NOT EXISTS {SCHEMA}.schema_version "
                    "(version integer PRIMARY KEY, installed_at timestamptz NOT NULL DEFAULT now())"
                )
                conn.execute(
                    f"INSERT INTO {SCHEMA}.schema_version(version) VALUES (1) "
                    "ON CONFLICT (version) DO NOTHING"
                )
                versions = conn.execute(
                    f"SELECT version FROM {SCHEMA}.schema_version ORDER BY version"
                ).fetchall()
                if [int(row[0]) for row in versions] != [1]:
                    raise RuntimeError(
                        "unsupported research-memory pgvector schema version"
                    )
                conn.execute(
                    f"""CREATE TABLE IF NOT EXISTS {SCHEMA}.{TABLE} (
                        memory_id uuid PRIMARY KEY,
                        query text NOT NULL,
                        user_id text,
                        fingerprint text,
                        created_at timestamptz NOT NULL,
                        expires_at timestamptz NOT NULL,
                        embedding vector({self.dimension}) NOT NULL
                    )"""
                )
                conn.execute(
                    f"CREATE INDEX IF NOT EXISTS research_memory_vectors_embedding_hnsw "
                    f"ON {SCHEMA}.{TABLE} USING hnsw (embedding vector_cosine_ops)"
                )
                conn.execute(
                    f"CREATE INDEX IF NOT EXISTS research_memory_vectors_expiry "
                    f"ON {SCHEMA}.{TABLE} (expires_at)"
                )
            self._schema_ready = True

    def upsert(self, row: MemoryVector) -> None:
        if len(row.embedding) != self.dimension:
            raise ValueError(
                "embedding dimension does not match research memory schema"
            )
        with self._connect() as conn:
            conn.execute(
                f"""INSERT INTO {SCHEMA}.{TABLE}
                    (memory_id, query, user_id, fingerprint, created_at, expires_at, embedding)
                    VALUES (%s, %s, %s, %s, %s, %s, %s::vector)
                    ON CONFLICT (memory_id) DO UPDATE SET
                      query=EXCLUDED.query, user_id=EXCLUDED.user_id,
                      fingerprint=EXCLUDED.fingerprint, created_at=EXCLUDED.created_at,
                      expires_at=EXCLUDED.expires_at, embedding=EXCLUDED.embedding""",
                (
                    row.memory_id,
                    row.query,
                    row.user_id,
                    row.fingerprint,
                    row.created_at,
                    row.expires_at,
                    vector_literal(row.embedding),
                ),
            )

    def search(
        self, embedding: Sequence[float], *, user_id: str | None, limit: int = 5
    ) -> list[dict[str, Any]]:
        if len(embedding) != self.dimension:
            raise ValueError("query dimension does not match research memory schema")
        user_filter = "AND user_id = %s" if user_id else ""
        params: list[Any] = [vector_literal(embedding)]
        if user_id:
            params.append(user_id)
        params.extend([vector_literal(embedding), limit])
        with self._connect() as conn:
            rows = conn.execute(
                f"""SELECT memory_id::text, query, user_id, fingerprint,
                    created_at, expires_at,
                    1 - (embedding <=> %s::vector) AS score
                FROM {SCHEMA}.{TABLE}
                WHERE expires_at > now() {user_filter}
                ORDER BY embedding <=> %s::vector, memory_id
                LIMIT %s""",
                params,
            ).fetchall()
        return [
            {
                "memory_id": str(r[0]),
                "query": r[1],
                "user_id": r[2],
                "fingerprint": r[3],
                "created_at": r[4],
                "expires_at": r[5],
                "score": float(r[6]),
            }
            for r in rows
        ]

    def delete(self, memory_id: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                f"DELETE FROM {SCHEMA}.{TABLE} WHERE memory_id=%s", (memory_id,)
            )
            return cur.rowcount > 0

    def list_ids(self, *, limit: int = 1000, offset: int = 0) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT memory_id::text FROM {SCHEMA}.{TABLE} "
                "ORDER BY memory_id LIMIT %s OFFSET %s",
                (limit, offset),
            ).fetchall()
        return [str(row[0]) for row in rows]

    def snapshot(self) -> dict[str, dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                f"""SELECT memory_id::text, query, user_id, fingerprint,
                    created_at, expires_at, embedding::text FROM {SCHEMA}.{TABLE}
                    ORDER BY memory_id"""
            ).fetchall()
        result: dict[str, dict[str, Any]] = {}
        for row in rows:
            vector_text = str(row[6]).strip("[]")
            result[str(row[0])] = {
                "query": row[1],
                "user_id": row[2],
                "fingerprint": row[3],
                "created_at": row[4],
                "expires_at": row[5],
                "embedding": [float(v) for v in vector_text.split(",") if v],
            }
        return result

    def count(self) -> int:
        with self._connect() as conn:
            return int(
                conn.execute(f"SELECT count(*) FROM {SCHEMA}.{TABLE}").fetchone()[0]
            )

    def delete_expired(self) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                f"DELETE FROM {SCHEMA}.{TABLE} WHERE expires_at <= now()"
            )
            return cur.rowcount
