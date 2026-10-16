"""Deterministic checks for the provider fault replay harness."""

import importlib.util
import sys
from pathlib import Path

from scripts.run_vector_store_evaluation import reference_search

SCRIPT = Path(__file__).parents[2] / "scripts" / "run_vector_store_provider_fault_evaluation.py"
SPEC = importlib.util.spec_from_file_location("vector_provider_fault_eval", SCRIPT)
assert SPEC and SPEC.loader
vector_provider_fault_eval = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = vector_provider_fault_eval
SPEC.loader.exec_module(vector_provider_fault_eval)


class FakeStore:
    def __init__(self, name: str):
        self.name = name
        self.records = {row.record_id: row for row in vector_provider_fault_eval.CORPUS}

    def upsert(self, records):
        self.records.update({record.record_id: record for record in records})

    def search(self, query):
        return reference_search(list(self.records.values()), query)

    def delete(self, record_id):
        self.records.pop(record_id)

    def close(self, cleanup):
        del cleanup


def test_manifest_is_pinned_and_includes_all_provider_scenarios():
    first = vector_provider_fault_eval._manifest()
    second = vector_provider_fault_eval._manifest()

    assert first == second
    assert first["schema_version"] == "vector-store-provider-fault-evaluation/1"
    assert len(first["corpus_sha256"]) == 64
    assert first["scenarios"] == [scenario.name for scenario in vector_provider_fault_eval.SCENARIOS]


def test_malformed_results_are_rejected_before_exposure():
    assert vector_provider_fault_eval._valid_results(
        [{"id": "doc-001", "score": 1.0}]
    )
    assert not vector_provider_fault_eval._valid_results([{"unexpected": "shape"}])


def test_fake_provider_passes_all_failure_contracts():
    def factory(suffix):
        return FakeStore(suffix)

    results = [
        vector_provider_fault_eval.run_scenario(factory, scenario, "fake")
        for scenario in vector_provider_fault_eval.SCENARIOS
    ]

    assert all(result["gate"] for result in results)
    assert results[1]["observed"] == "failed"
    assert any(row["id"] == "partial-doc" for row in results[1]["after"])
