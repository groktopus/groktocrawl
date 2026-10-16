#!/usr/bin/env python3
"""Seed or verify an isolated vector-store restart evaluation."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent))

from scripts.run_vector_store_evaluation import (
    CORPUS,
    QUERIES,
    PostgresStore,
    QdrantStore,
    _check_gates,
    _manifest,
)

SCHEMA_VERSION = "vector-store-restart-evaluation/1"


def _safe_id(run_id: str) -> str:
    return "".join(character if character.isalnum() else "_" for character in run_id)


def _stores(args: argparse.Namespace, create: bool):
    safe_id = _safe_id(args.run_id)
    return [
        QdrantStore(
            args.qdrant_url,
            f"groktocrawl_x_restart_{safe_id}",
            create=create,
        ),
        PostgresStore(args.postgres_dsn, f"restart_{safe_id}", create=create),
    ]


def _search_evidence(store, include_delete: bool) -> dict:
    searches = [
        {
            "query_id": query.query_id,
            "scope": query.scope,
            "results": store.search(query),
        }
        for query in QUERIES
    ]
    evidence = {"name": store.name, "errors": [], "searches": searches}
    if include_delete:
        store.delete("doc-001")
        evidence["post_delete"] = {
            "query_id": QUERIES[0].query_id,
            "results": store.search(QUERIES[0]),
        }
    evidence["ok"] = True
    evidence["gates"] = _check_gates(evidence, list(CORPUS))
    return evidence


def run(args: argparse.Namespace) -> dict:
    if not args.allow_isolated_database:
        raise SystemExit("refusing provider access without --allow-isolated-database")
    stores = _stores(args, create=args.phase == "seed")
    try:
        if args.phase == "seed":
            for store in stores:
                store.upsert(list(CORPUS))
            candidates = [_search_evidence(store, include_delete=False) for store in stores]
        else:
            candidates = [_search_evidence(store, include_delete=True) for store in stores]
        return {
            "schema_version": SCHEMA_VERSION,
            "phase": args.phase,
            "run_id": args.run_id,
            "created_at": datetime.now(UTC).isoformat(),
            "manifest": _manifest(),
            "expected_restart": args.phase == "verify",
            "candidates": candidates,
            "decision": "evaluation_only",
            "production_change": False,
            "qdrant_removal": False,
        }
    finally:
        for store in stores:
            store.close(args.cleanup if args.phase == "verify" else False)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("seed", "verify"), required=True)
    parser.add_argument("--qdrant-url", required=True)
    parser.add_argument("--postgres-dsn", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cleanup", action="store_true")
    parser.add_argument("--allow-isolated-database", action="store_true")
    args = parser.parse_args()
    result = run(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"output": str(args.output), "phase": args.phase}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
