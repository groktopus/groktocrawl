#!/usr/bin/env python3
"""Compare Qdrant and PostgreSQL + pgvector on one isolated fixture.

This is an evidence-producing experiment, not a service migration.  The
command requires an explicit safety flag because it creates a private table
and collection in the supplied endpoints.  It never touches the application
collection or the inherited Compose stack.

Example (with the opt-in vector-evaluation Compose file):

    python scripts/run_vector_store_evaluation.py \
      --qdrant-url http://127.0.0.1:16333 \
      --postgres-dsn 'postgresql://eval:secret@127.0.0.1:15432/eval' \
      --allow-isolated-database --cleanup --output vector-evaluation.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

SCHEMA_VERSION = "vector-store-evaluation/3"
DIMENSION = 3


@dataclass(frozen=True)
class VectorRecord:
    record_id: str
    scope: str
    vector: tuple[float, ...]
    deleted: bool = False


@dataclass(frozen=True)
class QueryCase:
    query_id: str
    scope: str
    vector: tuple[float, ...]
    limit: int = 3


CORPUS = (
    VectorRecord("doc-001", "alpha", (1.0, 0.0, 0.0)),
    VectorRecord("doc-002", "alpha", (0.9, 0.1, 0.0)),
    VectorRecord("doc-003", "alpha", (0.0, 1.0, 0.0)),
    VectorRecord("doc-004", "beta", (1.0, 0.0, 0.0)),
    VectorRecord("doc-005", "beta", (0.0, 0.0, 1.0)),
    VectorRecord("doc-006", "beta", (0.0, 1.0, 0.0)),
)
QUERIES = (
    QueryCase("alpha-x", "alpha", (1.0, 0.0, 0.0)),
    QueryCase("alpha-y", "alpha", (0.0, 1.0, 0.0)),
    QueryCase("beta-x", "beta", (1.0, 0.0, 0.0)),
    QueryCase("beta-z", "beta", (0.0, 0.0, 1.0)),
)


class VectorStore(Protocol):
    name: str

    def upsert(self, records: list[VectorRecord]) -> None: ...

    def search(self, query: QueryCase) -> list[dict[str, Any]]: ...

    def delete(self, record_id: str) -> None: ...

    def close(self, cleanup: bool) -> None: ...


def cosine_similarity(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    """Return cosine similarity for the normalized fixture vectors."""
    if len(left) != len(right):
        raise ValueError("vectors must have the same dimension")
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return sum(a * b for a, b in zip(left, right, strict=True)) / (
        left_norm * right_norm
    )


def reference_search(
    records: list[VectorRecord], query: QueryCase
) -> list[dict[str, Any]]:
    """Compute exact eligible results used only as a fixture reference."""
    eligible = [
        record
        for record in records
        if record.scope == query.scope and not record.deleted
    ]
    ranked = sorted(
        eligible,
        key=lambda record: (-cosine_similarity(record.vector, query.vector), record.record_id),
    )
    return [
        {"id": record.record_id, "score": cosine_similarity(record.vector, query.vector)}
        for record in ranked[: query.limit]
    ]


def _vector_literal(vector: tuple[float, ...]) -> str:
    return "[" + ",".join(format(value, ".17g") for value in vector) + "]"


def _qdrant_point_id(record_id: str) -> int:
    """Map a fixture ID to Qdrant's supported unsigned integer point ID."""
    return int.from_bytes(hashlib.sha256(record_id.encode()).digest()[:8], "big")


def _manifest() -> dict[str, Any]:
    corpus_payload = [
        {
            "id": record.record_id,
            "scope": record.scope,
            "vector": list(record.vector),
        }
        for record in CORPUS
    ]
    corpus_json = json.dumps(corpus_payload, separators=(",", ":"), sort_keys=True)
    corpus_hash = hashlib.sha256(corpus_json.encode()).hexdigest()
    return {
        "schema_version": SCHEMA_VERSION,
        "corpus_sha256": corpus_hash,
        "embedding_model": "fixture-vector-3d-v1",
        "dimension": DIMENSION,
        "distance": "cosine",
        "record_count": len(CORPUS),
        "query_count": len(QUERIES),
        "scopes": sorted({record.scope for record in CORPUS}),
        "duplicate_replay_count": 2,
        "random_seed": 0,
        "workloads": ["bulk_upsert", "duplicate_replay", "filtered_search", "delete"],
    }


class InMemoryReference:
    """Tiny deterministic store used to verify the fixture and comparison logic."""

    name = "reference"

    def __init__(self) -> None:
        self.records: dict[str, VectorRecord] = {}

    def upsert(self, records: list[VectorRecord]) -> None:
        self.records.update({record.record_id: record for record in records})

    def search(self, query: QueryCase) -> list[dict[str, Any]]:
        return reference_search(list(self.records.values()), query)

    def delete(self, record_id: str) -> None:
        record = self.records[record_id]
        self.records[record_id] = VectorRecord(
            record.record_id, record.scope, record.vector, deleted=True
        )

    def close(self, cleanup: bool) -> None:
        del cleanup


class QdrantStore:
    """Qdrant adapter using a unique collection for one evaluation run."""

    name = "qdrant"

    def __init__(
        self,
        url: str,
        collection: str,
        *,
        create: bool = True,
        dimension: int = DIMENSION,
    ) -> None:
        from qdrant_client import QdrantClient, models

        self._models = models
        self._client = QdrantClient(url=url)
        self._collection = collection
        if create:
            self._client.recreate_collection(
                collection_name=collection,
                vectors_config=models.VectorParams(
                    size=dimension, distance=models.Distance.COSINE
                ),
            )

    def upsert(self, records: list[VectorRecord]) -> None:
        self._client.upsert(
            collection_name=self._collection,
            points=[
                self._models.PointStruct(
                    id=_qdrant_point_id(record.record_id),
                    vector=list(record.vector),
                    payload={
                        "record_id": record.record_id,
                        "scope": record.scope,
                        "deleted": record.deleted,
                    },
                )
                for record in records
            ],
            wait=True,
        )

    def search(self, query: QueryCase) -> list[dict[str, Any]]:
        response = self._client.query_points(
            collection_name=self._collection,
            query=list(query.vector),
            query_filter=self._models.Filter(
                must=[
                    self._models.FieldCondition(
                        key="scope", match=self._models.MatchValue(value=query.scope)
                    ),
                    self._models.FieldCondition(
                        key="deleted", match=self._models.MatchValue(value=False)
                    ),
                ]
            ),
            limit=query.limit,
        )
        return [
            {
                "id": str((point.payload or {}).get("record_id", point.id)),
                "score": float(point.score),
            }
            for point in response.points
        ]

    def delete(self, record_id: str) -> None:
        self._client.set_payload(
            collection_name=self._collection,
            payload={"deleted": True},
            points=[_qdrant_point_id(record_id)],
            wait=True,
        )

    def close(self, cleanup: bool) -> None:
        if cleanup:
            self._client.delete_collection(self._collection)
        self._client.close()


class PostgresStore:
    """PostgreSQL + pgvector adapter using a private schema and table."""

    name = "pgvector"

    def __init__(
        self,
        dsn: str,
        table: str,
        *,
        create: bool = True,
        dimension: int = DIMENSION,
    ) -> None:
        import psycopg

        self._conn = psycopg.connect(dsn, autocommit=True)
        self._schema = "groktocrawl_x_vector_eval"
        self._table = table
        if create:
            self._conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            self._conn.execute(f"CREATE SCHEMA IF NOT EXISTS {self._schema}")
            self._conn.execute(
                f"""CREATE TABLE {self._schema}.{self._table} (
                    id text PRIMARY KEY,
                    scope text NOT NULL,
                    embedding vector({dimension}) NOT NULL,
                    deleted boolean NOT NULL DEFAULT false
                )"""
            )
            self._conn.execute(
                f"CREATE INDEX {self._table}_hnsw ON {self._schema}.{self._table} "
                "USING hnsw (embedding vector_cosine_ops)"
            )

    def upsert(self, records: list[VectorRecord]) -> None:
        if not records:
            return
        values = ", ".join("(%s, %s, %s::vector, %s)" for _ in records)
        statement = f"""INSERT INTO {self._schema}.{self._table} (id, scope, embedding, deleted)
            VALUES {values}
            ON CONFLICT (id) DO UPDATE SET scope = EXCLUDED.scope,
                embedding = EXCLUDED.embedding, deleted = EXCLUDED.deleted"""
        parameters = tuple(
            value
            for record in records
            for value in (
                record.record_id,
                record.scope,
                _vector_literal(record.vector),
                record.deleted,
            )
        )
        self._conn.execute(statement, parameters)

    def search(self, query: QueryCase) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            f"""SELECT id, 1 - (embedding <=> %s::vector) AS score
            FROM {self._schema}.{self._table}
            WHERE scope = %s AND deleted = false
            ORDER BY embedding <=> %s::vector, id
            LIMIT %s""",
            (_vector_literal(query.vector), query.scope, _vector_literal(query.vector), query.limit),
        ).fetchall()
        return [{"id": row[0], "score": float(row[1])} for row in rows]

    def delete(self, record_id: str) -> None:
        self._conn.execute(
            f"UPDATE {self._schema}.{self._table} SET deleted = true WHERE id = %s",
            (record_id,),
        )

    def close(self, cleanup: bool) -> None:
        if cleanup:
            self._conn.execute(f"DROP TABLE IF EXISTS {self._schema}.{self._table}")
        self._conn.close()


def _timed(function: Any) -> tuple[Any, float]:
    started = time.perf_counter()
    result = function()
    return result, (time.perf_counter() - started) * 1000


def evaluate_store(store: VectorStore, records: list[VectorRecord]) -> dict[str, Any]:
    """Run every workload and preserve failures as evidence."""
    evidence: dict[str, Any] = {
        "name": store.name,
        "errors": [],
        "metrics_ms": {},
        "searches": [],
    }

    def attempt(label: str, function: Any) -> Any:
        try:
            result, duration = _timed(function)
            evidence["metrics_ms"].setdefault(label, []).append(duration)
            return result
        except Exception as error:  # pragma: no cover - provider-specific failures
            evidence["errors"].append({"workload": label, "error": str(error)})
            return None

    attempt("bulk_upsert", lambda: store.upsert(records))
    attempt("duplicate_replay", lambda: store.upsert(records[:2]))
    for query in QUERIES:
        results = attempt(
            "filtered_search", lambda query=query: store.search(query)
        )
        if results is not None:
            evidence["searches"].append(
                {"query_id": query.query_id, "scope": query.scope, "results": results}
            )
    attempt("delete", lambda: store.delete("doc-001"))
    deleted_query = QUERIES[0]
    deleted_results = attempt("post_delete_search", lambda: store.search(deleted_query))
    if deleted_results is not None:
        evidence["post_delete"] = {
            "query_id": deleted_query.query_id,
            "results": deleted_results,
        }
    evidence["ok"] = not evidence["errors"]
    return evidence


def evaluate_concurrency(
    store_factory: Any,
    records: list[VectorRecord],
    workers: int,
    operations: int,
) -> dict[str, Any]:
    """Run a deterministic mixed search/upsert workload with one client per worker."""
    started = time.perf_counter()
    results: list[dict[str, Any]] = []

    def worker(worker_id: int) -> list[dict[str, Any]]:
        store = store_factory()
        worker_results: list[dict[str, Any]] = []
        try:
            for operation in range(worker_id, operations, workers):
                workload = "upsert" if operation % 4 == 0 else "search"
                record = records[operation % len(records)]
                query = QUERIES[operation % len(QUERIES)]
                try:
                    _, duration = _timed(
                        lambda record=record, workload=workload, query=query: store.upsert(
                            [record]
                        )
                        if workload == "upsert"
                        else store.search(query)
                    )
                    worker_results.append(
                        {"workload": workload, "duration_ms": duration, "ok": True}
                    )
                except Exception as error:  # pragma: no cover - provider-specific
                    worker_results.append(
                        {
                            "workload": workload,
                            "duration_ms": None,
                            "ok": False,
                            "error": str(error),
                        }
                    )
        finally:
            store.close(False)
        return worker_results

    with ThreadPoolExecutor(max_workers=workers) as executor:
        for worker_results in executor.map(worker, range(workers)):
            results.extend(worker_results)

    metrics: dict[str, list[float]] = {}
    errors: list[dict[str, Any]] = []
    for result in results:
        if result["ok"]:
            metrics.setdefault(result["workload"], []).append(result["duration_ms"])
        else:
            errors.append(
                {"workload": result["workload"], "error": result["error"]}
            )
    elapsed = time.perf_counter() - started
    successful = sum(1 for result in results if result["ok"])
    return {
        "workers": workers,
        "operations": operations,
        "successful_operations": successful,
        "failed_operations": len(errors),
        "throughput_ops_s": successful / elapsed if elapsed else 0.0,
        "errors": errors,
        "metrics_ms": metrics,
        "latency_summary_ms": {
            workload: _percentiles(values) for workload, values in metrics.items()
        },
        "ok": not errors and successful == operations,
    }


def _check_gates(
    evidence: dict[str, Any], records: list[VectorRecord]
) -> dict[str, bool]:
    by_query = {item["query_id"]: item["results"] for item in evidence["searches"]}
    checks: dict[str, bool] = {}
    for query in QUERIES:
        expected = reference_search(records, query)
        actual = by_query.get(query.query_id, [])
        expected_ids = [item["id"] for item in expected]
        checks[f"top_k_{query.query_id}"] = _ranking_matches(expected, actual)
        checks[f"scope_{query.query_id}"] = all(
            item["id"] in expected_ids for item in actual
        )
    post_delete = evidence.get("post_delete", {}).get("results", [])
    checks["deleted_doc_absent"] = all(item["id"] != "doc-001" for item in post_delete)
    checks["provider_ok"] = bool(evidence["ok"])
    return checks


def _percentiles(values: list[float]) -> dict[str, float] | None:
    if not values:
        return None
    ordered = sorted(values)
    return {
        "p50": ordered[len(ordered) // 2],
        "p95": ordered[min(len(ordered) - 1, math.ceil(len(ordered) * 0.95) - 1)],
        "p99": ordered[min(len(ordered) - 1, math.ceil(len(ordered) * 0.99) - 1)],
        "mean": statistics.fmean(ordered),
    }


def _ranking_matches(
    expected: list[dict[str, Any]], actual: list[dict[str, Any]], tolerance: float = 1e-5
) -> bool:
    """Compare ranked results while allowing arbitrary order inside score ties."""
    if len(expected) != len(actual):
        return False

    def groups(results: list[dict[str, Any]]) -> list[tuple[float, set[str]]]:
        output: list[tuple[float, set[str]]] = []
        for result in results:
            score = float(result["score"])
            if output and abs(score - output[-1][0]) <= tolerance:
                output[-1][1].add(str(result["id"]))
            else:
                output.append((score, {str(result["id"])}))
        return output

    expected_groups = groups(expected)
    actual_groups = groups(actual)
    return len(expected_groups) == len(actual_groups) and all(
        abs(expected_group[0] - actual_group[0]) <= tolerance
        and expected_group[1] == actual_group[1]
        for expected_group, actual_group in zip(
            expected_groups, actual_groups, strict=True
        )
    )


def _summarize_evidence(
    store_name: str, rounds: list[dict[str, Any]]
) -> dict[str, Any]:
    """Combine repeated rounds without hiding an individual round failure."""
    metrics: dict[str, list[float]] = {}
    errors: list[dict[str, Any]] = []
    for round_evidence in rounds:
        for workload, values in round_evidence["metrics_ms"].items():
            metrics.setdefault(workload, []).extend(values)
        errors.extend(
            {"round": round_evidence["round"], **error}
            for error in round_evidence["errors"]
        )
    gates = {
        gate: all(round_evidence["gates"].get(gate, False) for round_evidence in rounds)
        for gate in rounds[0]["gates"]
    }
    return {
        "name": store_name,
        "ok": not errors and all(gates.values()),
        "errors": errors,
        "rounds": rounds,
        "gates": gates,
        "metrics_ms": metrics,
        "latency_summary_ms": {
            key: _percentiles(values) for key, values in metrics.items()
        },
        "searches": rounds[-1]["searches"],
        "post_delete": rounds[-1].get("post_delete", {}),
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    if not args.allow_isolated_database:
        raise SystemExit("refusing provider access without --allow-isolated-database")
    run_id = args.run_id or datetime.now(UTC).strftime("run_%Y%m%dT%H%M%SZ")
    safe_id = "".join(character if character.isalnum() else "_" for character in run_id)
    records = list(CORPUS)
    if args.fresh_each_round and args.concurrency_workers:
        raise SystemExit("--concurrency-workers requires warm_reuse resources")
    stores: list[VectorStore] = []
    rounds_by_name: dict[str, list[dict[str, Any]]] = {"qdrant": [], "pgvector": []}
    resource_names: dict[str, str] = {}

    def run_round(round_number: int, current_stores: list[VectorStore]) -> None:
        for store in current_stores:
            round_evidence = evaluate_store(store, records)
            round_evidence["round"] = round_number
            round_evidence["gates"] = _check_gates(round_evidence, records)
            round_evidence["latency_summary_ms"] = {
                key: _percentiles(values)
                for key, values in round_evidence["metrics_ms"].items()
            }
            rounds_by_name[store.name].append(round_evidence)

    def create_stores(round_number: int) -> list[VectorStore]:
        suffix = f"_{round_number}" if args.fresh_each_round else ""
        resource_names.update(
            qdrant=f"groktocrawl_x_eval_{safe_id}{suffix}",
            pgvector=f"vectors_{safe_id}{suffix}",
        )
        return [
            QdrantStore(args.qdrant_url, resource_names["qdrant"]),
            PostgresStore(args.postgres_dsn, resource_names["pgvector"]),
        ]

    try:
        if args.fresh_each_round:
            for round_number in range(1, args.rounds + 1):
                stores = create_stores(round_number)
                try:
                    run_round(round_number, stores)
                finally:
                    for store in stores:
                        store.close(args.cleanup)
                    stores = []
        else:
            stores = create_stores(0)
            for round_number in range(1, args.rounds + 1):
                run_round(round_number, stores)
        concurrency_by_name: dict[str, dict[str, Any]] = {}
        if args.concurrency_workers:
            factories = {
                "qdrant": lambda: QdrantStore(
                    args.qdrant_url, resource_names["qdrant"], create=False
                ),
                "pgvector": lambda: PostgresStore(
                    args.postgres_dsn, resource_names["pgvector"], create=False
                ),
            }
            for name, factory in factories.items():
                concurrency_by_name[name] = evaluate_concurrency(
                    factory, records, args.concurrency_workers, args.concurrency_operations
                )
        candidates = [
            _summarize_evidence(name, rounds)
            for name, rounds in rounds_by_name.items()
        ]
        for candidate in candidates:
            if candidate["name"] in concurrency_by_name:
                candidate["concurrency"] = concurrency_by_name[candidate["name"]]
        return {
            "schema_version": SCHEMA_VERSION,
            "run_id": run_id,
            "created_at": datetime.now(UTC).isoformat(),
            "manifest": _manifest(),
            "rounds": args.rounds,
            "round_mode": "fresh" if args.fresh_each_round else "warm_reuse",
            "concurrency": {
                "workers": args.concurrency_workers,
                "operations": args.concurrency_operations,
            }
            if args.concurrency_workers
            else None,
            "candidates": candidates,
            "decision": "evaluation_only",
            "production_change": False,
            "qdrant_removal": False,
        }
    finally:
        for store in stores:
            store.close(args.cleanup)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qdrant-url", required=True)
    parser.add_argument("--postgres-dsn", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--run-id")
    parser.add_argument(
        "--rounds",
        type=int,
        default=1,
        help="number of repeated rounds per provider (first round starts from a fresh store)",
    )
    parser.add_argument(
        "--fresh-each-round",
        action="store_true",
        help="recreate each provider resource before every round for cold-start evidence",
    )
    parser.add_argument(
        "--concurrency-workers",
        type=int,
        default=0,
        help="number of independent clients for the optional mixed workload (0 disables it)",
    )
    parser.add_argument(
        "--concurrency-operations",
        type=int,
        default=40,
        help="total mixed search/upsert operations per provider",
    )
    parser.add_argument("--cleanup", action="store_true")
    parser.add_argument("--allow-isolated-database", action="store_true")
    args = parser.parse_args()
    if args.rounds < 1:
        parser.error("--rounds must be at least 1")
    if args.concurrency_workers < 0:
        parser.error("--concurrency-workers cannot be negative")
    if args.concurrency_operations < 1:
        parser.error("--concurrency-operations must be at least 1")
    result = run(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"output": str(args.output), "decision": result["decision"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
