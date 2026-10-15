#!/usr/bin/env python3
"""Exercise fail-closed vector-store failure and rollback contracts.

This is a deterministic, provider-independent companion to the isolated
PostgreSQL/Qdrant comparison.  It models the boundary conditions that a
provider-backed run must reproduce without touching production services or
the held-out quality packet.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent))

from scripts.run_vector_store_evaluation import CORPUS, QUERIES, reference_search

SCHEMA_VERSION = "vector-store-fault-evaluation/1"
EMBEDDING_MODEL = "fixture-vector-3d-v1"


class InjectedFailureError(RuntimeError):
    """A planned failure in an isolated fixture operation."""


@dataclass(frozen=True)
class FaultScenario:
    name: str
    operation: str
    expected: str


SCENARIOS = (
    FaultScenario("write-timeout", "write", "unchanged"),
    FaultScenario("partial-write", "partial_write", "reconcile_required"),
    FaultScenario("malformed-search", "malformed_search", "rejected"),
    FaultScenario("interrupted-restore", "restore", "active_preserved"),
    FaultScenario("interrupted-migration", "migration", "active_preserved"),
    FaultScenario("cleanup-timeout", "cleanup", "deletion_unconfirmed"),
)


def _corpus_sha256() -> str:
    payload = [
        {"id": row.record_id, "scope": row.scope, "vector": list(row.vector)}
        for row in CORPUS
    ]
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def manifest() -> dict[str, Any]:
    """Return the pinned inputs for every deterministic scenario."""
    return {
        "schema_version": SCHEMA_VERSION,
        "corpus_sha256": _corpus_sha256(),
        "embedding_model": EMBEDDING_MODEL,
        "dimension": 3,
        "distance": "cosine",
        "record_count": len(CORPUS),
        "query_count": len(QUERIES),
        "random_seed": 0,
        "scenarios": [scenario.name for scenario in SCENARIOS],
        "failure_policy": "no success claim after an injected failure",
    }


class FaultStore:
    """Small durable-state model used to check failure boundaries."""

    def __init__(self, fault: str | None = None) -> None:
        self.fault = fault
        self.records = {row.record_id: row for row in CORPUS}
        self.active_version = "v1"
        self.staged_version: str | None = None

    def write(self) -> dict[str, Any]:
        if self.fault == "write":
            raise InjectedFailureError("write timeout before commit acknowledgement")
        if self.fault == "partial_write":
            self.records["partial-doc"] = CORPUS[0]
            raise InjectedFailureError("write interrupted after one record")
        return {"committed": True, "record_count": len(self.records)}

    def search(self) -> list[dict[str, Any]]:
        results = reference_search(list(self.records.values()), QUERIES[0])
        if self.fault == "malformed_search":
            return [{"unexpected": "shape"}]
        return results

    def restore(self) -> dict[str, Any]:
        self.staged_version = "restored-v2"
        if self.fault == "restore":
            self.staged_version = None
            raise InjectedFailureError("restore interrupted before activation")
        self.active_version = self.staged_version
        self.staged_version = None
        return {"active_version": self.active_version}

    def migrate(self) -> dict[str, Any]:
        self.staged_version = "migrated-v2"
        if self.fault == "migration":
            self.staged_version = None
            raise InjectedFailureError("migration interrupted before cutover")
        self.active_version = self.staged_version
        self.staged_version = None
        return {"active_version": self.active_version}

    def cleanup(self) -> dict[str, Any]:
        if self.fault == "cleanup":
            raise InjectedFailureError("deletion acknowledgement timed out")
        self.records.pop("doc-001", None)
        return {"deleted": True}


def _malformed_result_rejected(results: list[dict[str, Any]]) -> bool:
    """Validate the minimal result shape before exposing it to a caller."""
    return all(
        isinstance(row.get("id"), str) and isinstance(row.get("score"), float)
        for row in results
    )


def run_scenario(scenario: FaultScenario) -> dict[str, Any]:
    store = FaultStore(scenario.operation)
    before_version = store.active_version
    before_ids = sorted(store.records)
    try:
        if scenario.operation in {"write", "partial_write"}:
            store.write()
            observed = "committed"
        elif scenario.operation == "malformed_search":
            observed = "accepted" if _malformed_result_rejected(store.search()) else "rejected"
        elif scenario.operation == "restore":
            store.restore()
            observed = "activated"
        elif scenario.operation == "migration":
            store.migrate()
            observed = "activated"
        else:
            store.cleanup()
            observed = "deleted_confirmed"
        failed = False
        error = None
    except InjectedFailureError as failure:
        failed = True
        observed = "failed"
        error = str(failure)

    after_ids = sorted(store.records)
    if scenario.operation == "write":
        gate = failed and after_ids == before_ids
    elif scenario.operation == "partial_write":
        gate = failed and "partial-doc" in after_ids
    elif scenario.operation == "malformed_search":
        gate = observed == "rejected"
    elif scenario.operation in {"restore", "migration"}:
        gate = failed and store.active_version == before_version and store.staged_version is None
    else:
        gate = failed and "doc-001" in after_ids

    return {
        "name": scenario.name,
        "operation": scenario.operation,
        "expected": scenario.expected,
        "observed": observed,
        "error": error,
        "before": {"active_version": before_version, "record_ids": before_ids},
        "after": {
            "active_version": store.active_version,
            "record_ids": after_ids,
            "staged_version": store.staged_version,
        },
        "gate": gate,
    }


def run() -> dict[str, Any]:
    results = [run_scenario(scenario) for scenario in SCENARIOS]
    return {
        "schema_version": SCHEMA_VERSION,
        "created_at": datetime.now(UTC).isoformat(),
        "manifest": manifest(),
        "scenarios": results,
        "all_gates_passed": all(result["gate"] for result in results),
        "decision": "evaluation_only",
        "provider_backed": False,
        "production_change": False,
        "qdrant_removal": False,
        "follow_up": "Repeat these scenarios against each isolated provider before any migration decision.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"output": str(args.output), "all_gates_passed": result["all_gates_passed"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
