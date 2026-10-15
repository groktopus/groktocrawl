#!/usr/bin/env python3
"""Rehearse reversible vector dimension migration on isolated providers."""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent))

from scripts.run_vector_store_evaluation import (
    CORPUS,
    QUERIES,
    PostgresStore,
    QdrantStore,
    QueryCase,
    VectorRecord,
    _manifest,
)
from scripts.run_vector_store_scale_evaluation import _query_gate

SCHEMA_VERSION = "vector-store-migration-evaluation/1"
TARGET_DIMENSION = 4


def _safe_id(run_id: str) -> str:
    return "".join(character if character.isalnum() else "_" for character in run_id)


def _transform_record(record: VectorRecord) -> VectorRecord:
    return replace(record, vector=(*record.vector, 0.0))


def _transform_query(query: QueryCase) -> QueryCase:
    return replace(query, vector=(*query.vector, 0.0))


def _records_with_delete(
    records: list[VectorRecord], record_id: str
) -> list[VectorRecord]:
    return [
        replace(record, deleted=True) if record.record_id == record_id else record
        for record in records
    ]


def _search_evidence(
    store: Any, records: list[VectorRecord], queries: list[QueryCase]
) -> dict[str, Any]:
    searches = []
    gates = {}
    for query in queries:
        actual = store.search(query)
        searches.append(
            {"query_id": query.query_id, "scope": query.scope, "results": actual}
        )
        gates[f"query_{query.query_id}"] = _query_gate(records, query, actual)
        gates[f"scope_{query.query_id}"] = all(
            any(
                record.record_id == row["id"]
                and record.scope == query.scope
                and not record.deleted
                for record in records
            )
            for row in actual
        )
    gates["deleted_doc_absent"] = all(
        row["id"] != "doc-001" for search in searches for row in search["results"]
    )
    return {"searches": searches, "gates": gates, "ok": all(gates.values())}


def _qdrant_route(
    client: Any, models: Any, alias: str, target: str, *, replace_alias: bool
) -> None:
    operations = []
    if replace_alias:
        operations.append(
            models.DeleteAliasOperation(
                delete_alias=models.DeleteAlias(alias_name=alias)
            )
        )
    operations.append(
        models.CreateAliasOperation(
            create_alias=models.CreateAlias(collection_name=target, alias_name=alias)
        )
    )
    client.update_collection_aliases(change_aliases_operations=operations)


def _postgres_route(connection: Any, route: str, target: str) -> None:
    connection.execute(
        """CREATE TABLE IF NOT EXISTS groktocrawl_x_vector_eval.migration_routes (
            route text PRIMARY KEY, active_table text NOT NULL
        )"""
    )
    with connection.transaction():
        connection.execute(
            """INSERT INTO groktocrawl_x_vector_eval.migration_routes (route, active_table)
            VALUES (%s, %s) ON CONFLICT (route) DO UPDATE
            SET active_table = EXCLUDED.active_table""",
            (route, target),
        )


def _postgres_active(connection: Any, route: str) -> str:
    row = connection.execute(
        "SELECT active_table FROM groktocrawl_x_vector_eval.migration_routes WHERE route = %s",
        (route,),
    ).fetchone()
    if row is None:
        raise RuntimeError("migration route is missing")
    return str(row[0])


def _evaluate_qdrant(url: str, safe: str) -> dict[str, Any]:
    source_name = f"groktocrawl_x_migration_{safe}_v1"
    target_name = f"groktocrawl_x_migration_{safe}_v2"
    alias = f"groktocrawl_x_migration_{safe}_active"
    source = QdrantStore(url, source_name)
    target = QdrantStore(url, target_name, dimension=TARGET_DIMENSION)
    started = time.perf_counter()
    try:
        source.upsert(list(CORPUS))
        source.delete("doc-001")
        source_records = _records_with_delete(list(CORPUS), "doc-001")
        target_records = [_transform_record(record) for record in source_records]
        target.upsert(target_records)
        target_info = source._client.get_collection(target_name)
        target_vectors = target_info.config.params.vectors
        target_dimension = getattr(target_vectors, "size", None)
        transformed_queries = [_transform_query(query) for query in QUERIES]
        before_cutover = _search_evidence(target, target_records, transformed_queries)
        _qdrant_route(
            source._client, source._models, alias, source_name, replace_alias=False
        )
        _qdrant_route(
            source._client, source._models, alias, target_name, replace_alias=True
        )
        active = QdrantStore(url, alias, create=False, dimension=TARGET_DIMENSION)
        try:
            after_cutover = _search_evidence(
                active, target_records, transformed_queries
            )
        finally:
            active.close(False)
        source.delete("doc-002")
        target.delete("doc-002")
        source_records = _records_with_delete(source_records, "doc-002")
        target_records = _records_with_delete(target_records, "doc-002")
        deletion_continuity = _search_evidence(
            target, target_records, transformed_queries
        )
        _qdrant_route(
            source._client, source._models, alias, source_name, replace_alias=True
        )
        rolled_back = QdrantStore(url, alias, create=False)
        try:
            after_rollback = _search_evidence(
                rolled_back, source_records, list(QUERIES)
            )
        finally:
            rolled_back.close(False)
        gates = {
            "target_dimension_configured": target_dimension == TARGET_DIMENSION,
            "target_reconciled": before_cutover["ok"],
            "cutover_reconciled": after_cutover["ok"],
            "deletion_continuity": deletion_continuity["ok"],
            "rollback_reconciled": after_rollback["ok"],
        }
        return {
            "provider": "qdrant",
            "source_dimension": 3,
            "target_dimension": TARGET_DIMENSION,
            "target_schema": {
                "configured_dimension": target_dimension,
                "collection_status": str(target_info.status),
            },
            "route_mechanism": "atomic collection alias update",
            "before_cutover": before_cutover,
            "after_cutover": after_cutover,
            "deletion_continuity": deletion_continuity,
            "after_rollback": after_rollback,
            "gates": gates,
            "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            "ok": all(gates.values()),
        }
    finally:
        # Delete the alias before deleting its collections.
        with contextlib.suppress(Exception):
            source._client.update_collection_aliases(
                change_aliases_operations=[
                    source._models.DeleteAliasOperation(
                        delete_alias=source._models.DeleteAlias(alias_name=alias)
                    )
                ]
            )
        target.close(True)
        source.close(True)


def _evaluate_postgres(dsn: str, safe: str) -> dict[str, Any]:
    source_name = f"migration_{safe}_v1"
    target_name = f"migration_{safe}_v2"
    route = f"migration_{safe}_active"
    source = PostgresStore(dsn, source_name)
    target = PostgresStore(dsn, target_name, dimension=TARGET_DIMENSION)
    started = time.perf_counter()
    try:
        source.upsert(list(CORPUS))
        source.delete("doc-001")
        source_records = _records_with_delete(list(CORPUS), "doc-001")
        target_records = [_transform_record(record) for record in source_records]
        target.upsert(target_records)
        vector_type = source._conn.execute(
            """SELECT format_type(attribute.atttypid, attribute.atttypmod)
            FROM pg_attribute attribute
            JOIN pg_class relation ON relation.oid = attribute.attrelid
            JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
            WHERE namespace.nspname = %s AND relation.relname = %s
              AND attribute.attname = 'embedding'""",
            (source._schema, target_name),
        ).fetchone()
        target_indexes = [
            str(row[0])
            for row in source._conn.execute(
                """SELECT indexname FROM pg_indexes
                WHERE schemaname = %s AND tablename = %s ORDER BY indexname""",
                (source._schema, target_name),
            ).fetchall()
        ]
        transformed_queries = [_transform_query(query) for query in QUERIES]
        before_cutover = _search_evidence(target, target_records, transformed_queries)
        _postgres_route(source._conn, route, source_name)
        _postgres_route(source._conn, route, target_name)
        active_name = _postgres_active(source._conn, route)
        active = PostgresStore(
            dsn, active_name, create=False, dimension=TARGET_DIMENSION
        )
        try:
            after_cutover = _search_evidence(
                active, target_records, transformed_queries
            )
        finally:
            active.close(False)
        source.delete("doc-002")
        target.delete("doc-002")
        source_records = _records_with_delete(source_records, "doc-002")
        target_records = _records_with_delete(target_records, "doc-002")
        deletion_continuity = _search_evidence(
            target, target_records, transformed_queries
        )
        _postgres_route(source._conn, route, source_name)
        active_name = _postgres_active(source._conn, route)
        rolled_back = PostgresStore(dsn, active_name, create=False)
        try:
            after_rollback = _search_evidence(
                rolled_back, source_records, list(QUERIES)
            )
        finally:
            rolled_back.close(False)
        gates = {
            "target_dimension_configured": vector_type == ("vector(4)",),
            "target_index_created": f"{target_name}_hnsw" in target_indexes,
            "target_reconciled": before_cutover["ok"],
            "cutover_reconciled": after_cutover["ok"],
            "deletion_continuity": deletion_continuity["ok"],
            "rollback_reconciled": after_rollback["ok"],
        }
        return {
            "provider": "pgvector",
            "source_dimension": 3,
            "target_dimension": TARGET_DIMENSION,
            "target_schema": {
                "vector_type": vector_type[0] if vector_type else None,
                "indexes": target_indexes,
            },
            "route_mechanism": "transactional route record",
            "before_cutover": before_cutover,
            "after_cutover": after_cutover,
            "deletion_continuity": deletion_continuity,
            "after_rollback": after_rollback,
            "gates": gates,
            "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            "ok": all(gates.values()),
        }
    finally:
        source._conn.execute(
            "DELETE FROM groktocrawl_x_vector_eval.migration_routes WHERE route = %s",
            (route,),
        )
        target.close(True)
        source.close(True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=("qdrant", "pgvector"), required=True)
    parser.add_argument("--qdrant-url")
    parser.add_argument("--postgres-dsn")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--allow-isolated-database", action="store_true")
    args = parser.parse_args()
    if not args.allow_isolated_database:
        parser.error("refusing provider access without --allow-isolated-database")
    safe = _safe_id(args.run_id)
    if args.provider == "qdrant":
        if not args.qdrant_url:
            parser.error("--qdrant-url is required for qdrant")
        candidate = _evaluate_qdrant(args.qdrant_url, safe)
    else:
        if not args.postgres_dsn:
            parser.error("--postgres-dsn is required for pgvector")
        candidate = _evaluate_postgres(args.postgres_dsn, safe)
    result = {
        "schema_version": SCHEMA_VERSION,
        "run_id": args.run_id,
        "created_at": datetime.now(UTC).isoformat(),
        "manifest": {
            **_manifest(),
            "target_dimension": TARGET_DIMENSION,
            "transform": "append-zero-coordinate-v1",
            "deletion_ids": ["doc-001", "doc-002"],
        },
        "candidate": candidate,
        "production_change": False,
        "qdrant_removal": False,
        "ok": candidate["ok"],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"output": str(args.output), "ok": result["ok"]}))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
