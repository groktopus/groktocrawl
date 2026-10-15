"""Deterministic checks for the isolated vector-store evaluation harness."""

import importlib.util
import sys
from pathlib import Path

SCRIPT = Path(__file__).parents[2] / "scripts" / "run_vector_store_evaluation.py"
SPEC = importlib.util.spec_from_file_location("vector_eval", SCRIPT)
assert SPEC and SPEC.loader
vector_eval = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = vector_eval
SPEC.loader.exec_module(vector_eval)


def test_manifest_is_pinned_and_deterministic():
    first = vector_eval._manifest()
    second = vector_eval._manifest()

    assert first == second
    assert first["schema_version"] == "vector-store-evaluation/3"
    assert first["dimension"] == 3
    assert first["record_count"] == 6
    assert len(first["corpus_sha256"]) == 64


def test_round_mode_is_explicit_in_cli_contract():
    assert "--fresh-each-round" in vector_eval.main.__code__.co_consts


def test_qdrant_point_ids_are_supported_and_stable():
    first = vector_eval._qdrant_point_id("doc-001")
    second = vector_eval._qdrant_point_id("doc-002")

    assert first == vector_eval._qdrant_point_id("doc-001")
    assert first != second
    assert 0 <= first < 2**64


def test_reference_search_filters_scope_and_ranks_cosine():
    results = vector_eval.reference_search(list(vector_eval.CORPUS), vector_eval.QUERIES[0])

    assert [item["id"] for item in results] == ["doc-001", "doc-002", "doc-003"]
    assert all(item["id"] not in {"doc-004", "doc-005", "doc-006"} for item in results)


def test_reference_and_in_memory_store_enforce_soft_delete():
    store = vector_eval.InMemoryReference()
    store.upsert(list(vector_eval.CORPUS))
    store.delete("doc-001")

    result_ids = [item["id"] for item in store.search(vector_eval.QUERIES[0])]
    assert result_ids == ["doc-002", "doc-003"]


def test_postgres_upsert_uses_one_native_multi_row_statement():
    calls = []

    class Connection:
        def execute(self, statement, parameters):
            calls.append((statement, parameters))

    store = vector_eval.PostgresStore.__new__(vector_eval.PostgresStore)
    store._conn = Connection()
    store._schema = "evaluation"
    store._table = "vectors"
    records = list(vector_eval.CORPUS[:3])

    store.upsert(records)

    assert len(calls) == 1
    statement, parameters = calls[0]
    assert "INSERT INTO evaluation.vectors" in statement
    assert statement.count("(%s, %s, %s::vector, %s)") == len(records)
    assert len(parameters) == len(records) * 4
    assert parameters[0] == records[0].record_id


def test_gates_fail_closed_for_missing_or_wrong_results():
    evidence = {
        "ok": True,
        "errors": [],
        "searches": [
            {"query_id": vector_eval.QUERIES[0].query_id, "results": [{"id": "doc-004"}]}
        ],
        "post_delete": {"results": [{"id": "doc-001"}]},
    }

    gates = vector_eval._check_gates(evidence, list(vector_eval.CORPUS))

    assert gates["provider_ok"] is True
    assert gates["top_k_alpha-x"] is False
    assert gates["scope_alpha-x"] is False
    assert gates["deleted_doc_absent"] is False


def test_ranking_gate_allows_order_changes_inside_score_ties():
    expected = [
        {"id": "doc-004", "score": 1.0},
        {"id": "doc-005", "score": 0.0},
        {"id": "doc-006", "score": 0.0},
    ]
    actual = [
        {"id": "doc-004", "score": 1.0},
        {"id": "doc-006", "score": 0.0},
        {"id": "doc-005", "score": 0.0},
    ]

    assert vector_eval._ranking_matches(expected, actual)
    assert not vector_eval._ranking_matches(expected, [actual[1], actual[0], actual[2]])


def test_repeated_round_summary_fails_closed_when_any_round_fails():
    round_one = {
        "round": 1,
        "errors": [],
        "metrics_ms": {"filtered_search": [1.0]},
        "searches": [],
        "gates": {"provider_ok": True, "scope_alpha-x": True},
        "post_delete": {},
    }
    round_two = {
        "round": 2,
        "errors": [{"workload": "filtered_search", "error": "timeout"}],
        "metrics_ms": {"filtered_search": [2.0]},
        "searches": [],
        "gates": {"provider_ok": False, "scope_alpha-x": False},
        "post_delete": {},
    }

    summary = vector_eval._summarize_evidence("fixture", [round_one, round_two])

    assert summary["ok"] is False
    assert summary["gates"] == {"provider_ok": False, "scope_alpha-x": False}
    assert summary["latency_summary_ms"]["filtered_search"]["p50"] == 2.0
    assert summary["errors"] == [
        {"round": 2, "workload": "filtered_search", "error": "timeout"}
    ]


def test_concurrency_workload_uses_independent_clients_and_reports_mix():
    class FakeStore:
        name = "fixture"

        def upsert(self, records):
            del records

        def search(self, query):
            return vector_eval.reference_search(list(vector_eval.CORPUS), query)

        def close(self, cleanup):
            del cleanup

    result = vector_eval.evaluate_concurrency(
        FakeStore, list(vector_eval.CORPUS), workers=2, operations=8
    )

    assert result["ok"] is True
    assert result["successful_operations"] == 8
    assert result["failed_operations"] == 0
    assert result["metrics_ms"].keys() == {"upsert", "search"}
    assert result["throughput_ops_s"] > 0
    assert result["latency_summary_ms"]["search"]["p99"] >= 0
