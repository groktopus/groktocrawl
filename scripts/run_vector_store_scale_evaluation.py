#!/usr/bin/env python3
"""Measure bounded vector-store scale and mixed-load behavior."""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent))

from scripts.run_vector_store_evaluation import (
    PostgresStore,
    QdrantStore,
    QueryCase,
    VectorRecord,
    cosine_similarity,
    reference_search,
)

SCHEMA_VERSION = "vector-store-scale-evaluation/1"


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


def _vector(index: int, dimension: int = 3) -> tuple[float, ...]:
    """Generate deterministic, non-zero vectors without external embeddings."""
    values = tuple(
        (index * (17 + coordinate * 14 + (2 if coordinate >= 2 else 0))) % 101
        + 1
        for coordinate in range(dimension)
    )
    magnitude = math.sqrt(sum(value * value for value in values))
    return tuple(value / magnitude for value in values)


def make_corpus(size: int, dimension: int = 3) -> list[VectorRecord]:
    return [
        VectorRecord(
            f"scale-{index:05d}",
            f"scope-{index % 4}",
            _vector(index, dimension),
        )
        for index in range(size)
    ]


def make_queries(records: list[VectorRecord]) -> list[QueryCase]:
    return [
        QueryCase(f"query-{scope}", scope, records[index].vector)
        for index, scope in enumerate(("scope-0", "scope-1", "scope-2", "scope-3"))
    ]


def _timed(function: Callable[[], Any]) -> tuple[Any, float]:
    started = time.perf_counter()
    result = function()
    return result, (time.perf_counter() - started) * 1000


def _upsert_in_batches(store: Any, records: list[VectorRecord], batch_size: int) -> None:
    for start in range(0, len(records), batch_size):
        store.upsert(records[start : start + batch_size])


def _query_gate(
    records: list[VectorRecord],
    query: QueryCase,
    actual: list[dict[str, Any]],
    tolerance: float = 1e-5,
) -> bool:
    """Accept any valid member of a score tie at the top-k cutoff."""
    expected = reference_search(records, query)
    if len(actual) != len(expected):
        return False
    eligible = {
        record.record_id: cosine_similarity(record.vector, query.vector)
        for record in records
        if record.scope == query.scope and not record.deleted
    }
    actual_ids = [str(row.get("id")) for row in actual]
    if len(set(actual_ids)) != len(actual_ids) or any(
        record_id not in eligible for record_id in actual_ids
    ):
        return False
    for row, record_id in zip(actual, actual_ids, strict=True):
        if not isinstance(row.get("score"), (int, float)):
            return False
        if abs(float(row["score"]) - eligible[record_id]) > tolerance:
            return False
    actual_scores = [float(row["score"]) for row in actual]
    if any(
        later > earlier + tolerance
        for earlier, later in pairwise(actual_scores)
    ):
        return False
    if not expected:
        return True
    cutoff = float(expected[-1]["score"])
    strictly_better = {
        record_id for record_id, score in eligible.items() if score > cutoff + tolerance
    }
    return strictly_better.issubset(actual_ids) and all(
        score >= cutoff - tolerance for score in actual_scores
    )


def _mixed_load(
    factory: Callable[[], Any], records: list[VectorRecord], queries: list[QueryCase], workers: int, operations: int
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []

    def worker(worker_id: int) -> list[dict[str, Any]]:
        store = factory()
        worker_results: list[dict[str, Any]] = []
        try:
            for operation in range(worker_id, operations, workers):
                workload = "upsert" if operation % 4 == 0 else "search"
                record = records[operation % len(records)]
                query = queries[operation % len(queries)]
                try:
                    _, duration = _timed(
                        lambda store=store, record=record, workload=workload, query=query: store.upsert([record])
                        if workload == "upsert"
                        else store.search(query)
                    )
                    worker_results.append({"workload": workload, "duration_ms": duration, "ok": True})
                except Exception as error:  # pragma: no cover - provider-specific
                    worker_results.append({"workload": workload, "duration_ms": None, "ok": False, "error": str(error)})
        finally:
            store.close(False)
        return worker_results

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as executor:
        for worker_results in executor.map(worker, range(workers)):
            results.extend(worker_results)
    elapsed = time.perf_counter() - started
    errors = [result for result in results if not result["ok"]]
    metrics = {
        workload: _percentiles(
            [result["duration_ms"] for result in results if result["ok"] and result["workload"] == workload]
        )
        for workload in ("upsert", "search")
    }
    successful = len(results) - len(errors)
    return {
        "workers": workers,
        "operations": operations,
        "successful_operations": successful,
        "failed_operations": len(errors),
        "throughput_ops_s": successful / elapsed if elapsed else 0.0,
        "latency_summary_ms": metrics,
        "errors": errors,
        "ok": len(results) == operations and not errors,
    }


def _run_provider(
    provider: str,
    factory: Callable[[str, bool], Any],
    sizes: list[int],
    workers: int,
    operations: int,
    cleanup: bool,
    dimension: int = 3,
    batch_size: int = 1000,
) -> dict[str, Any]:
    scale_results: list[dict[str, Any]] = []
    resource_names: list[str] = []
    for size in sizes:
        records = make_corpus(size, dimension)
        queries = make_queries(records)
        resource_suffix = f"size_{size}"
        store = factory(resource_suffix, True)
        resource_names.append(resource_suffix)
        errors: list[dict[str, Any]] = []
        search_latencies: list[float] = []
        query_results: list[dict[str, Any]] = []
        try:
            _, bulk_ms = _timed(
                lambda store=store, records=records: _upsert_in_batches(
                    store, records, batch_size
                )
            )
            _, replay_ms = _timed(
                lambda store=store, records=records, size=size: store.upsert(
                    records[: min(10, size)]
                )
            )
            for query in queries:
                try:
                    result, latency_ms = _timed(
                        lambda store=store, query=query: store.search(query)
                    )
                    search_latencies.append(latency_ms)
                    query_results.append({"query_id": query.query_id, "results": result})
                    if not _query_gate(records, query, result):
                        errors.append({"workload": "filtered_search", "query_id": query.query_id, "error": "ranking mismatch"})
                except Exception as error:  # pragma: no cover - provider-specific
                    errors.append({"workload": "filtered_search", "query_id": query.query_id, "error": str(error)})
            mixed = _mixed_load(
                lambda size=size: factory(f"size_{size}", False),
                records,
                queries,
                workers,
                operations,
            )
            errors.extend(mixed["errors"])
            scale_results.append({
                "size": size,
                "bulk_upsert_ms": bulk_ms,
                "duplicate_replay_ms": replay_ms,
                "search_latency_ms": _percentiles(search_latencies),
                "queries": query_results,
                "mixed_load": mixed,
                "errors": errors,
                "ok": not errors and mixed["ok"],
            })
        finally:
            store.close(cleanup)
    return {
        "provider": provider,
        "resource_suffixes": resource_names,
        "sizes": scale_results,
        "ok": all(result["ok"] for result in scale_results),
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    if not args.allow_isolated_database:
        raise SystemExit("refusing provider access without --allow-isolated-database")
    safe_id = "".join(character if character.isalnum() else "_" for character in args.run_id)
    if args.provider == "qdrant":
        def factory(suffix: str, create: bool) -> Any:
            return QdrantStore(
                args.qdrant_url,
                f"groktocrawl_x_scale_{safe_id}_{suffix}",
                create=create,
                dimension=args.dimension,
            )
    else:
        def factory(suffix: str, create: bool) -> Any:
            return PostgresStore(
                args.postgres_dsn,
                f"scale_{safe_id}_{suffix}",
                create=create,
                dimension=args.dimension,
            )
    result = _run_provider(
        args.provider,
        factory,
        args.sizes,
        args.workers,
        args.operations,
        args.cleanup,
        args.dimension,
        args.batch_size,
    )
    result.update({
        "schema_version": SCHEMA_VERSION,
        "run_id": args.run_id,
        "created_at": datetime.now(UTC).isoformat(),
        "manifest": {
            "sizes": args.sizes,
            "workers": args.workers,
            "operations": args.operations,
            "random_seed": 0,
            "embedding_model": f"deterministic-fixture-vector-{args.dimension}d-v1",
            "dimension": args.dimension,
            "batch_size": args.batch_size,
            "distance": "cosine",
            "corpus_family": "deterministic synthetic scale corpus",
        },
        "decision": "evaluation_only",
        "production_change": False,
        "qdrant_removal": False,
    })
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=("qdrant", "pgvector"), required=True)
    parser.add_argument("--qdrant-url", required=True)
    parser.add_argument("--postgres-dsn", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sizes", default="100,500,1000")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--operations", type=int, default=80)
    parser.add_argument("--dimension", type=int, default=3)
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument("--cleanup", action="store_true")
    parser.add_argument("--allow-isolated-database", action="store_true")
    args = parser.parse_args()
    args.sizes = [int(item) for item in args.sizes.split(",") if item]
    if not args.sizes or any(size < 4 for size in args.sizes):
        parser.error("--sizes must contain integers >= 4")
    if args.workers < 1:
        parser.error("--workers must be positive")
    if args.operations < 1 or args.operations > 100000:
        parser.error("--operations must be between 1 and 100000")
    if args.dimension < 1 or args.dimension > 2000:
        parser.error("--dimension must be between 1 and 2000")
    if args.batch_size < 1 or args.batch_size > 10000:
        parser.error("--batch-size must be between 1 and 10000")
    result = run(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"output": str(args.output), "ok": result["ok"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
