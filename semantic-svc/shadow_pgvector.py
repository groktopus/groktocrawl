"""Fail-open pgvector shadow storage for semantic-service experiments.

Qdrant remains authoritative. This module never selects served results and
records failures through its caller instead of changing request outcomes.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


def _vector_literal(vector: Sequence[float]) -> str:
    return "[" + ",".join(format(float(value), ".17g") for value in vector) + "]"


def should_sample(key: str, rate: float) -> bool:
    """Make stable sampling decisions without retaining the sampled query."""
    if rate <= 0:
        return False
    if rate >= 1:
        return True
    bucket = int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], "big")
    return bucket / (2**64) < rate


@dataclass(frozen=True)
class ShadowConfig:
    mode: str
    dsn: str
    schema: str
    table: str
    sample_rate: float
    score_tolerance: float
    timeout_seconds: float

    @property
    def enabled(self) -> bool:
        return self.mode in {"shadow_pgvector", "pgvector"}

    @property
    def serving(self) -> bool:
        return self.mode == "pgvector"

    @classmethod
    def from_env(cls) -> ShadowConfig:
        mode = os.getenv("VECTOR_STORE_MODE", "qdrant").strip().lower()
        if mode not in {"qdrant", "shadow_pgvector", "pgvector"}:
            raise ValueError(
                "VECTOR_STORE_MODE must be qdrant, shadow_pgvector, or pgvector"
            )
        dsn = os.getenv("PGVECTOR_SHADOW_DSN", "").strip()
        if mode in {"shadow_pgvector", "pgvector"} and not dsn:
            raise ValueError("PGVECTOR_SHADOW_DSN is required when pgvector is enabled")
        sample_rate = float(os.getenv("PGVECTOR_SHADOW_SAMPLE_RATE", "0.1"))
        if not 0.0 <= sample_rate <= 1.0:
            raise ValueError("PGVECTOR_SHADOW_SAMPLE_RATE must be between 0 and 1")
        tolerance = float(os.getenv("PGVECTOR_SHADOW_SCORE_TOLERANCE", "0.0001"))
        if tolerance < 0:
            raise ValueError("PGVECTOR_SHADOW_SCORE_TOLERANCE must be non-negative")
        timeout = float(os.getenv("PGVECTOR_SHADOW_TIMEOUT_SECONDS", "2"))
        if timeout <= 0:
            raise ValueError("PGVECTOR_SHADOW_TIMEOUT_SECONDS must be positive")
        return cls(
            mode=mode,
            dsn=dsn,
            schema="groktocrawl_x_semantic_shadow",
            table="pages",
            sample_rate=sample_rate,
            score_tolerance=tolerance,
            timeout_seconds=timeout,
        )


@dataclass(frozen=True)
class ShadowSearchResult:
    point_id: int
    url: str
    title: str
    score: float
    description: str = ""
    indexed_at: str | None = None


@dataclass(frozen=True)
class ShadowRecord:
    point_id: int
    url: str
    title: str
    vector: Sequence[float]
    model: str
    payload: Mapping[str, Any]


@dataclass(frozen=True)
class ShadowComparison:
    matches: bool
    authoritative_ids: tuple[int, ...]
    shadow_ids: tuple[int, ...]
    maximum_score_delta: float | None


def compare_results(
    authoritative: Sequence[tuple[int, float]],
    shadow: Sequence[ShadowSearchResult],
    *,
    score_tolerance: float,
) -> ShadowComparison:
    authoritative_ids = tuple(point_id for point_id, _ in authoritative)
    shadow_ids = tuple(result.point_id for result in shadow)
    deltas = [
        abs(authoritative_score - shadow_result.score)
        for (authoritative_id, authoritative_score), shadow_result in zip(
            authoritative, shadow, strict=False
        )
        if authoritative_id == shadow_result.point_id
    ]
    maximum_delta = max(deltas) if deltas else None
    matches = (
        authoritative_ids == shadow_ids
        and len(authoritative) == len(shadow)
        and maximum_delta is not None
        and maximum_delta <= score_tolerance
    ) or (not authoritative and not shadow)
    return ShadowComparison(
        matches=matches,
        authoritative_ids=authoritative_ids,
        shadow_ids=shadow_ids,
        maximum_score_delta=maximum_delta,
    )


class PgvectorShadowStore:
    """Small synchronous adapter invoked off the semantic-service event loop."""

    def __init__(self, config: ShadowConfig, dimension: int) -> None:
        self.config = config
        self.dimension = dimension
        self._connection: Any | None = None
        self._lock = threading.Lock()
        self._schema_ready = False

    def _connect(self) -> Any:
        if (
            self._connection is None
            or getattr(self._connection, "closed", False)
            or getattr(self._connection, "broken", False)
        ):
            if self._connection is not None:
                self._connection.close()
            import psycopg

            connect_timeout = max(1, math.ceil(self.config.timeout_seconds))
            statement_timeout_ms = max(1, int(self.config.timeout_seconds * 1000))
            self._connection = psycopg.connect(
                self.config.dsn,
                autocommit=True,
                connect_timeout=connect_timeout,
                options=f"-c statement_timeout={statement_timeout_ms}",
            )
        return self._connection

    def ensure_schema(self) -> None:
        if not self.config.enabled:
            return
        with self._lock:
            if self._schema_ready:
                return
            connection = self._connect()
            connection.execute("CREATE EXTENSION IF NOT EXISTS vector")
            connection.execute(f"CREATE SCHEMA IF NOT EXISTS {self.config.schema}")
            connection.execute(
                f"""CREATE TABLE IF NOT EXISTS {self.config.schema}.{self.config.table} (
                    point_id numeric(20, 0) PRIMARY KEY,
                    url text NOT NULL,
                    title text NOT NULL,
                    embedding vector({self.dimension}) NOT NULL,
                    model text NOT NULL,
                    payload jsonb NOT NULL,
                    deleted boolean NOT NULL DEFAULT false,
                    updated_at timestamptz NOT NULL DEFAULT now()
                )"""
            )
            connection.execute(
                f"CREATE INDEX IF NOT EXISTS {self.config.table}_embedding_hnsw "
                f"ON {self.config.schema}.{self.config.table} "
                "USING hnsw (embedding vector_cosine_ops)"
            )
            self._schema_ready = True

    def upsert(
        self,
        *,
        point_id: int,
        url: str,
        title: str,
        vector: Sequence[float],
        model: str,
        payload: Mapping[str, Any],
    ) -> None:
        self.upsert_many([ShadowRecord(point_id, url, title, vector, model, payload)])

    def upsert_many(self, records: Sequence[ShadowRecord]) -> None:
        if not records:
            return
        if any(len(record.vector) != self.dimension for record in records):
            raise ValueError(
                "shadow vector dimension does not match configured dimension"
            )
        values_sql = ",".join(
            ["(%s, %s, %s, %s::vector, %s, %s::jsonb, false, now())"] * len(records)
        )
        parameters: list[Any] = []
        for record in records:
            parameters.extend(
                (
                    record.point_id,
                    record.url,
                    record.title,
                    _vector_literal(record.vector),
                    record.model,
                    json.dumps(
                        dict(record.payload), separators=(",", ":"), sort_keys=True
                    ),
                )
            )
        with self._lock:
            connection = self._connect()
            connection.execute(
                f"""INSERT INTO {self.config.schema}.{self.config.table}
                    (point_id, url, title, embedding, model, payload, deleted, updated_at)
                    VALUES {values_sql}
                    ON CONFLICT (point_id) DO UPDATE SET
                      url=EXCLUDED.url, title=EXCLUDED.title,
                      embedding=EXCLUDED.embedding, model=EXCLUDED.model,
                      payload=EXCLUDED.payload, deleted=false, updated_at=now()""",
                parameters,
            )

    def delete(self, point_id: int) -> None:
        self.delete_many([point_id])

    def delete_many(self, point_ids: Sequence[int]) -> None:
        if not point_ids:
            return
        with self._lock:
            self._connect().execute(
                f"""UPDATE {self.config.schema}.{self.config.table}
                SET deleted=true, updated_at=now() WHERE point_id = ANY(%s)""",
                (list(point_ids),),
            )

    def active_ids(self, *, model: str) -> set[int]:
        with self._lock:
            rows = self._connect().execute(
                f"""SELECT point_id FROM {self.config.schema}.{self.config.table}
                WHERE model=%s AND deleted=false""",
                (model,),
            )
            return {int(row[0]) for row in rows.fetchall()}

    def count(self, *, model: str) -> int:
        with self._lock:
            row = (
                self._connect()
                .execute(
                    f"""SELECT count(*) FROM {self.config.schema}.{self.config.table}
                WHERE model=%s AND deleted=false""",
                    (model,),
                )
                .fetchone()
            )
            return int(row[0])

    def search(
        self, vector: Sequence[float], *, model: str, limit: int
    ) -> list[ShadowSearchResult]:
        if len(vector) != self.dimension:
            raise ValueError(
                "shadow query dimension does not match configured dimension"
            )
        with self._lock:
            rows = (
                self._connect()
                .execute(
                    f"""SELECT point_id, url, title,
                    1 - (embedding <=> %s::vector) AS score,
                    COALESCE(payload->>'content_excerpt', ''),
                    payload->>'last_indexed_at'
                FROM {self.config.schema}.{self.config.table}
                WHERE model=%s AND deleted=false
                ORDER BY embedding <=> %s::vector, point_id
                LIMIT %s""",
                    (_vector_literal(vector), model, _vector_literal(vector), limit),
                )
                .fetchall()
            )
        return [
            ShadowSearchResult(
                int(row[0]),
                str(row[1]),
                str(row[2]),
                float(row[3]),
                str(row[4]),
                str(row[5]) if row[5] is not None else None,
            )
            for row in rows
        ]

    def close(self) -> None:
        with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None
                self._schema_ready = False
