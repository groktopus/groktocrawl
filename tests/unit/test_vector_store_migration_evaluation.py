"""Deterministic checks for the isolated vector migration harness."""

import importlib.util
import sys
from pathlib import Path

from scripts.run_vector_store_evaluation import CORPUS, QUERIES, reference_search

SCRIPT = (
    Path(__file__).parents[2] / "scripts" / "run_vector_store_migration_evaluation.py"
)
WORKFLOW = (
    Path(__file__).parents[2]
    / ".github/workflows/vector-store-migration-evaluation.yml"
)
SPEC = importlib.util.spec_from_file_location("vector_migration_eval", SCRIPT)
assert SPEC and SPEC.loader
vector_migration_eval = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = vector_migration_eval
SPEC.loader.exec_module(vector_migration_eval)


class FakeStore:
    def __init__(self, records):
        self.records = records

    def search(self, query):
        return reference_search(self.records, query)


def test_dimension_transform_preserves_search_and_deletion_contract():
    records = vector_migration_eval._records_with_delete(list(CORPUS), "doc-001")
    transformed = [
        vector_migration_eval._transform_record(record) for record in records
    ]
    queries = [vector_migration_eval._transform_query(query) for query in QUERIES]

    evidence = vector_migration_eval._search_evidence(
        FakeStore(transformed), transformed, queries
    )

    assert all(len(record.vector) == 4 for record in transformed)
    assert all(len(query.vector) == 4 for query in queries)
    assert evidence["ok"] is True
    assert evidence["gates"]["deleted_doc_absent"] is True


def test_run_id_is_restricted_to_provider_safe_identifier():
    assert vector_migration_eval._safe_id("migration/a:b c") == "migration_a_b_c"


def test_workflow_is_manual_isolated_and_enforces_both_providers():
    workflow = WORKFLOW.read_text()

    assert "workflow_dispatch:" in workflow
    assert "compose.vector-evaluation.yml" in workflow
    assert "for provider in qdrant pgvector" in workflow
    assert "--allow-isolated-database" in workflow
    assert 'assert result["production_change"] is False' in workflow
    assert 'assert result["qdrant_removal"] is False' in workflow
