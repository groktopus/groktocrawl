"""Acceptance semantics must reject material changes, even with identical IDs."""

import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "exact_gate", Path(__file__).parents[2] / "scripts/accept_exact_rank_migration.py"
)
assert SPEC and SPEC.loader
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)


def fixture():
    scores = {key: 1 - key / 20 for key in range(10)}
    return list(scores.items()), scores


def test_tied_permutation_and_replacement():
    hits, scores = fixture()
    scores[8] = scores[9]
    hits = [(key, scores[key]) for key in range(10)]
    gate.validate_results(hits, hits[:8] + hits[8:][::-1], scores)
    replacement = [*hits[:9], (10, scores[9])]
    gate.validate_results(hits, replacement, {**scores, 10: scores[9]})


@pytest.mark.parametrize(
    "kind",
    [
        "miss",
        "reverse",
        "same_unsorted",
        "nan",
        "inf",
        "duplicate",
        "missing",
        "missing_score",
    ],
)
def test_rejects_invalid_results(kind):
    hits, scores = fixture()
    projected = hits.copy()
    exact = hits.copy()
    if kind == "miss":
        scores[10] = 0.1
        projected[-1] = (10, 0.1)
    elif kind == "reverse":
        projected.reverse()
    elif kind == "same_unsorted":
        projected.reverse()
        exact = projected.copy()
    elif kind in {"nan", "inf"}:
        projected[0] = (0, float(kind))
    elif kind == "duplicate":
        projected[-1] = projected[0]
    elif kind == "missing":
        projected.pop()
    else:
        scores.pop(0)
    with pytest.raises(ValueError):
        gate.validate_results(exact, projected, scores)


@pytest.mark.parametrize(
    "body",
    [
        "",
        '# TYPE groktocrawl_pgvector_shadow_operations_total counter\n# TYPE groktocrawl_pgvector_shadow_comparisons_total counter\ngroktocrawl_pgvector_shadow_operations_total{operation="upsert",outcome="failure"} NaN',
    ],
)
def test_metrics_fail_closed(body):
    with pytest.raises(ValueError):
        gate.parse_metrics(body)


def test_metrics_present_zero():
    assert gate.parse_metrics(
        '# TYPE groktocrawl_pgvector_shadow_operations_total counter\n# TYPE groktocrawl_pgvector_shadow_comparisons_total counter\ngroktocrawl_pgvector_shadow_operations_total{operation="upsert",outcome="failure"} 0\ngroktocrawl_pgvector_shadow_comparisons_total{outcome="failure"} 0'
    ) == {"write_failure": 0, "comparison_failure": 0, "comparison_observed": True}


def test_nonfinite_recomputed_score():
    hits, scores = fixture()
    scores[0] = float("nan")
    with pytest.raises(ValueError):
        gate.validate_results(hits, hits, scores)


def test_joined_score_disagreement():
    hits, scores = fixture()
    scores[0] -= 0.01
    with pytest.raises(ValueError):
        gate.validate_results(hits, hits, scores)


def test_declared_lazy_families_are_zero():
    assert gate.parse_metrics(
        "# TYPE groktocrawl_pgvector_shadow_operations_total counter\n# TYPE groktocrawl_pgvector_shadow_comparisons_total counter"
    ) == {"write_failure": 0, "comparison_failure": 0, "comparison_observed": True}


@pytest.mark.parametrize(
    "suffix",
    [
        "groktocrawl_pgvector_shadow_operations_total broken",
        'groktocrawl_pgvector_shadow_operations_total{operation="upsert",outcome="failure"} 0\ngroktocrawl_pgvector_shadow_operations_total{operation="upsert",outcome="failure"} 0',
        'groktocrawl_pgvector_shadow_operations_total{operation="upsert",outcome="success"} inf',
    ],
)
def test_declared_metrics_still_reject_bad_series(suffix):
    body = (
        "# TYPE groktocrawl_pgvector_shadow_operations_total counter\n# TYPE groktocrawl_pgvector_shadow_comparisons_total counter\n"
        + suffix
    )
    with pytest.raises(ValueError):
        gate.parse_metrics(body)


def test_comparison_family_can_be_unregistered():
    result = gate.parse_metrics(
        '# TYPE groktocrawl_pgvector_shadow_operations_total counter\ngroktocrawl_pgvector_shadow_operations_total{operation="upsert",outcome="success"} 2'
    )
    assert result["comparison_observed"] is False
    assert result["write_failure"] == 0


def test_delete_failures_are_counted():
    result = gate.parse_metrics(
        '# TYPE groktocrawl_pgvector_shadow_operations_total counter\ngroktocrawl_pgvector_shadow_operations_total{operation="delete",outcome="failure"} 2'
    )
    assert result["write_failure"] == 2
