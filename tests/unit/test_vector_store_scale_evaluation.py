"""Deterministic checks for the bounded vector-store scale harness."""

import importlib.util
import sys
from pathlib import Path

SCRIPT = Path(__file__).parents[2] / "scripts" / "run_vector_store_scale_evaluation.py"
WORKFLOW = Path(__file__).parents[2] / ".github/workflows/vector-store-scale-evaluation.yml"
SPEC = importlib.util.spec_from_file_location("vector_scale_eval", SCRIPT)
assert SPEC and SPEC.loader
vector_scale_eval = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = vector_scale_eval
SPEC.loader.exec_module(vector_scale_eval)


def test_hosted_workflow_runs_paired_repetitions_by_default():
    workflow = WORKFLOW.read_text()

    assert "default: 3" in workflow
    assert 'for round in $(seq 1 "$VECTOR_EVAL_ROUNDS")' in workflow
    assert '${provider}-round-${round}.json' in workflow
    assert 'rounds = int(os.environ["VECTOR_EVAL_ROUNDS"])' in workflow
    assert "assert len(paths) == rounds" in workflow
    assert 'VECTOR_EVAL_OPERATIONS: ${{ inputs.operations || 80 }}' in workflow
    assert "docker-stats-series.csv" in workflow
    assert "workload-phases.csv" in workflow
    assert "assert len(phases) == rounds * 4" in workflow


class FakeStore:
    def __init__(self, suffix: str, create: bool = True):
        del suffix, create
        self.records = {}

    def upsert(self, records):
        self.records.update({record.record_id: record for record in records})

    def search(self, query):
        return vector_scale_eval.reference_search(list(self.records.values()), query)

    def close(self, cleanup):
        del cleanup


def test_corpus_and_queries_are_deterministic():
    first = vector_scale_eval.make_corpus(20)
    second = vector_scale_eval.make_corpus(20)

    assert first == second
    assert len(first) == 20
    assert len(vector_scale_eval.make_queries(first)) == 4
    assert all(len(record.vector) == 3 for record in first)


def test_corpus_supports_inherited_embedding_dimension():
    records = vector_scale_eval.make_corpus(4, dimension=1024)

    assert all(len(record.vector) == 1024 for record in records)
    assert records == vector_scale_eval.make_corpus(4, dimension=1024)
    assert all(abs(sum(value * value for value in record.vector) - 1.0) < 1e-9 for record in records)


def test_fake_provider_passes_bounded_scale_and_mixed_load():
    result = vector_scale_eval._run_provider(
        "fake", FakeStore, [20], workers=2, operations=8, cleanup=True
    )

    assert result["ok"] is True
    size_result = result["sizes"][0]
    assert size_result["ok"] is True
    assert size_result["mixed_load"]["successful_operations"] == 8
    assert size_result["mixed_load"]["failed_operations"] == 0


def test_percentiles_keep_tail_measurements():
    summary = vector_scale_eval._percentiles([1.0, 2.0, 3.0, 4.0])

    assert summary["p50"] == 3.0
    assert summary["p95"] == 4.0
    assert summary["p99"] == 4.0


def test_bulk_upsert_uses_equal_bounded_record_batches():
    batch_sizes = []

    class Store:
        def upsert(self, records):
            batch_sizes.append(len(records))

    vector_scale_eval._upsert_in_batches(
        Store(), vector_scale_eval.make_corpus(2501), batch_size=1000
    )

    assert batch_sizes == [1000, 1000, 501]


def test_query_gate_allows_provider_order_inside_exact_score_ties():
    records = [
        vector_scale_eval.VectorRecord("a", "scope", (1.0, 0.0, 0.0)),
        vector_scale_eval.VectorRecord("b", "scope", (1.0, 0.0, 0.0)),
        vector_scale_eval.VectorRecord("c", "scope", (0.0, 1.0, 0.0)),
    ]
    query = vector_scale_eval.QueryCase("q", "scope", (1.0, 0.0, 0.0))
    actual = [
        {"id": "b", "score": 1.0},
        {"id": "a", "score": 1.0},
        {"id": "c", "score": 0.0},
    ]

    assert vector_scale_eval._query_gate(records, query, actual)


def test_query_gate_allows_equivalent_item_at_tied_cutoff():
    records = [
        vector_scale_eval.VectorRecord("a", "scope", (1.0, 0.0, 0.0)),
        vector_scale_eval.VectorRecord("b", "scope", (0.8, 0.2, 0.0)),
        vector_scale_eval.VectorRecord("c", "scope", (0.0, 1.0, 0.0)),
        vector_scale_eval.VectorRecord("d", "scope", (0.0, 1.0, 0.0)),
    ]
    query = vector_scale_eval.QueryCase("q", "scope", (1.0, 0.0, 0.0), limit=3)
    actual = [
        {"id": "a", "score": 1.0},
        {"id": "b", "score": vector_scale_eval.cosine_similarity(records[1].vector, query.vector)},
        {"id": "d", "score": 0.0},
    ]

    assert vector_scale_eval._query_gate(records, query, actual)


def test_query_gate_rejects_wrong_scope_or_missing_strictly_better_result():
    records = [
        vector_scale_eval.VectorRecord("a", "scope", (1.0, 0.0, 0.0)),
        vector_scale_eval.VectorRecord("b", "scope", (0.8, 0.2, 0.0)),
        vector_scale_eval.VectorRecord("c", "scope", (0.0, 1.0, 0.0)),
        vector_scale_eval.VectorRecord("foreign", "other", (1.0, 0.0, 0.0)),
    ]
    query = vector_scale_eval.QueryCase("q", "scope", (1.0, 0.0, 0.0), limit=3)

    assert not vector_scale_eval._query_gate(
        records,
        query,
        [
            {"id": "foreign", "score": 1.0},
            {"id": "b", "score": 0.97},
            {"id": "c", "score": 0.0},
        ],
    )
