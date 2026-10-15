#!/usr/bin/env python3
"""Replay vector-store failure contracts against isolated providers."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Callable
from dataclasses import dataclass
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
    VectorRecord,
)

SCHEMA_VERSION = "vector-store-provider-fault-evaluation/1"


class InjectedFailureError(RuntimeError):
    """A planned fault in one provider operation."""


@dataclass(frozen=True)
class Scenario:
    name: str
    operation: str
    expected: str


SCENARIOS = (
    Scenario("write-timeout", "write", "unchanged"),
    Scenario("partial-write", "partial_write", "reconcile_required"),
    Scenario("malformed-search", "malformed_search", "rejected"),
    Scenario("interrupted-restore", "restore", "active_preserved"),
    Scenario("interrupted-migration", "migration", "active_preserved"),
    Scenario("cleanup-timeout", "cleanup", "deletion_unconfirmed"),
)


def _safe_id(value: str) -> str:
    return "".join(character if character.isalnum() else "_" for character in value)


def _manifest() -> dict[str, Any]:
    payload = [
        {"id": row.record_id, "scope": row.scope, "vector": list(row.vector)}
        for row in CORPUS
    ]
    digest = hashlib.sha256(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()
    return {
        "schema_version": SCHEMA_VERSION,
        "corpus_sha256": digest,
        "embedding_model": "fixture-vector-3d-v1",
        "dimension": 3,
        "distance": "cosine",
        "record_count": len(CORPUS),
        "query_count": len(QUERIES),
        "random_seed": 0,
        "scenarios": [scenario.name for scenario in SCENARIOS],
        "failure_policy": "no success claim after an injected failure",
    }


def _valid_results(results: list[dict[str, Any]]) -> bool:
    return all(
        isinstance(row.get("id"), str) and isinstance(row.get("score"), (int, float))
        for row in results
    )


class FaultAdapter:
    """Apply one planned fault around a real isolated provider adapter."""

    def __init__(self, store: Any, fault: str) -> None:
        self.store = store
        self.fault = fault

    def seed(self) -> None:
        self.store.upsert(list(CORPUS))

    def write(self) -> None:
        if self.fault == "write":
            raise InjectedFailureError("write timeout before commit acknowledgement")
        if self.fault == "partial_write":
            self.store.upsert(
                [VectorRecord("partial-doc", "alpha", (0.8, 0.2, 0.0))]
            )
            raise InjectedFailureError("write interrupted after one record")
        self.store.upsert(list(CORPUS))

    def search(self) -> list[dict[str, Any]]:
        if self.fault == "malformed_search":
            return [{"unexpected": "shape"}]
        return self.store.search(QUERIES[0])

    def delete(self) -> None:
        if self.fault == "cleanup":
            raise InjectedFailureError("deletion acknowledgement timed out")
        self.store.delete("doc-001")


def _resource_factory(
    provider: str,
    qdrant_url: str,
    postgres_dsn: str,
    run_id: str,
) -> tuple[Callable[[str], Any], str]:
    safe_id = _safe_id(run_id)
    if provider == "qdrant":
        return (
            lambda suffix: QdrantStore(
                qdrant_url, f"groktocrawl_x_fault_{safe_id}_{suffix}"
            ),
            "qdrant",
        )
    return (
        lambda suffix: PostgresStore(postgres_dsn, f"fault_{safe_id}_{suffix}"),
        "pgvector",
    )


def run_scenario(factory: Callable[[str], Any], scenario: Scenario, name: str) -> dict[str, Any]:
    store = factory(f"active_{scenario.operation}")
    try:
        store.upsert(list(CORPUS))
        before = store.search(QUERIES[0])
        adapter = FaultAdapter(store, scenario.operation)
        try:
            if scenario.operation in {"write", "partial_write"}:
                adapter.write()
                observed = "committed"
            elif scenario.operation == "malformed_search":
                observed = "accepted" if _valid_results(adapter.search()) else "rejected"
            elif scenario.operation in {"restore", "migration"}:
                staged = factory(f"staged_{scenario.operation}")
                try:
                    staged.upsert(list(CORPUS))
                    raise InjectedFailureError(
                        f"{scenario.operation} interrupted before activation"
                    )
                finally:
                    staged.close(True)
            else:
                adapter.delete()
            failed = False
            error = None
        except InjectedFailureError as failure:
            failed = True
            error = str(failure)
            observed = "failed"
        after = store.search(QUERIES[0])
        if scenario.operation == "write":
            gate = failed and after == before
        elif scenario.operation == "partial_write":
            gate = failed and any(row["id"] == "partial-doc" for row in after)
        elif scenario.operation == "malformed_search":
            gate = observed == "rejected"
        elif scenario.operation in {"restore", "migration"}:
            gate = failed and after == before
        else:
            gate = failed and any(row["id"] == "doc-001" for row in after)
        return {
            "name": name,
            "scenario": scenario.name,
            "operation": scenario.operation,
            "expected": scenario.expected,
            "observed": observed,
            "error": error,
            "before": before,
            "after": after,
            "gate": gate,
        }
    finally:
        store.close(True)


def run(args: argparse.Namespace) -> dict[str, Any]:
    if not args.allow_isolated_database:
        raise SystemExit("refusing provider access without --allow-isolated-database")
    results: list[dict[str, Any]] = []
    factory, name = _resource_factory(
        args.provider, args.qdrant_url, args.postgres_dsn, args.run_id
    )
    for scenario in SCENARIOS:
        results.append(run_scenario(factory, scenario, name))
    return {
        "schema_version": SCHEMA_VERSION,
        "created_at": datetime.now(UTC).isoformat(),
        "run_id": args.run_id,
        "provider": name,
        "manifest": _manifest(),
        "scenarios": results,
        "all_gates_passed": all(result["gate"] for result in results),
        "decision": "evaluation_only",
        "provider_backed": True,
        "production_change": False,
        "qdrant_removal": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=("qdrant", "pgvector"), required=True)
    parser.add_argument("--qdrant-url", required=True)
    parser.add_argument("--postgres-dsn", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--allow-isolated-database", action="store_true")
    args = parser.parse_args()
    result = run(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"output": str(args.output), "all_gates_passed": result["all_gates_passed"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
