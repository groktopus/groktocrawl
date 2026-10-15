"""Unit tests for the fail-open pgvector shadow adapter."""

import importlib.util
import sys
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).parents[2] / "semantic-svc" / "shadow_pgvector.py"
SPEC = importlib.util.spec_from_file_location("shadow_pgvector_under_test", MODULE_PATH)
assert SPEC and SPEC.loader
shadow = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = shadow
SPEC.loader.exec_module(shadow)


def test_default_mode_keeps_shadow_disabled(monkeypatch):
    monkeypatch.delenv("VECTOR_STORE_MODE", raising=False)
    monkeypatch.delenv("PGVECTOR_SHADOW_DSN", raising=False)

    config = shadow.ShadowConfig.from_env()

    assert config.mode == "qdrant"
    assert config.enabled is False


def test_shadow_mode_requires_dsn(monkeypatch):
    monkeypatch.setenv("VECTOR_STORE_MODE", "shadow_pgvector")
    monkeypatch.delenv("PGVECTOR_SHADOW_DSN", raising=False)

    with pytest.raises(ValueError, match="PGVECTOR_SHADOW_DSN"):
        shadow.ShadowConfig.from_env()


def test_pgvector_mode_is_enabled_for_serving(monkeypatch):
    monkeypatch.setenv("VECTOR_STORE_MODE", "pgvector")
    monkeypatch.setenv("PGVECTOR_SHADOW_DSN", "postgresql://serving")

    config = shadow.ShadowConfig.from_env()

    assert config.enabled is True
    assert config.serving is True


def test_shadow_sample_rate_is_bounded(monkeypatch):
    monkeypatch.setenv("VECTOR_STORE_MODE", "shadow_pgvector")
    monkeypatch.setenv("PGVECTOR_SHADOW_DSN", "postgresql://shadow")
    monkeypatch.setenv("PGVECTOR_SHADOW_SAMPLE_RATE", "1.1")

    with pytest.raises(ValueError, match="between 0 and 1"):
        shadow.ShadowConfig.from_env()


def test_shadow_timeout_must_be_positive(monkeypatch):
    monkeypatch.setenv("VECTOR_STORE_MODE", "shadow_pgvector")
    monkeypatch.setenv("PGVECTOR_SHADOW_DSN", "postgresql://shadow")
    monkeypatch.setenv("PGVECTOR_SHADOW_TIMEOUT_SECONDS", "0")

    with pytest.raises(ValueError, match="must be positive"):
        shadow.ShadowConfig.from_env()


def test_sampling_is_stable_and_honors_bounds():
    assert shadow.should_sample("same query", 0) is False
    assert shadow.should_sample("same query", 1) is True
    assert shadow.should_sample("same query", 0.5) == shadow.should_sample(
        "same query", 0.5
    )


def test_comparison_reports_identity_and_score_match():
    candidate = [
        shadow.ShadowSearchResult(11, "https://one", "One", 0.9),
        shadow.ShadowSearchResult(12, "https://two", "Two", 0.8),
    ]

    result = shadow.compare_results(
        [(11, 0.90001), (12, 0.79999)], candidate, score_tolerance=0.0001
    )

    assert result.matches is True
    assert result.maximum_score_delta == pytest.approx(0.00001)


def test_comparison_rejects_reordered_identity_even_when_scores_match():
    candidate = [
        shadow.ShadowSearchResult(12, "https://two", "Two", 0.9),
        shadow.ShadowSearchResult(11, "https://one", "One", 0.8),
    ]

    result = shadow.compare_results(
        [(11, 0.9), (12, 0.8)], candidate, score_tolerance=0.0001
    )

    assert result.matches is False
    assert result.authoritative_ids == (11, 12)
    assert result.shadow_ids == (12, 11)


def test_vector_literal_preserves_full_float_precision():
    assert shadow._vector_literal([0.1, -0.25, 1]) == "[0.10000000000000001,-0.25,1]"


def test_batch_upsert_uses_one_database_statement():
    class _Connection:
        def __init__(self):
            self.calls = []

        def execute(self, statement, parameters=None):
            self.calls.append((statement, parameters))

    config = shadow.ShadowConfig(
        mode="shadow_pgvector",
        dsn="postgresql://shadow",
        schema="shadow",
        table="pages",
        sample_rate=0.1,
        score_tolerance=0.0001,
        timeout_seconds=2,
    )
    store = shadow.PgvectorShadowStore(config, dimension=2)
    connection = _Connection()
    store._connection = connection
    records = [
        shadow.ShadowRecord(1, "https://one", "One", [0.1, 0.2], "v1", {"x": 1}),
        shadow.ShadowRecord(2, "https://two", "Two", [0.3, 0.4], "v1", {"x": 2}),
    ]

    store.upsert_many(records)

    assert len(connection.calls) == 1
    statement, parameters = connection.calls[0]
    assert statement.count("(%s, %s, %s, %s::vector, %s, %s::jsonb") == 2
    assert len(parameters) == 12


def test_schema_initialization_runs_once_per_connection():
    class _Connection:
        def __init__(self):
            self.calls = []

        def execute(self, statement, parameters=None):
            self.calls.append((statement, parameters))

    config = shadow.ShadowConfig(
        mode="shadow_pgvector",
        dsn="postgresql://shadow",
        schema="shadow",
        table="pages",
        sample_rate=0.1,
        score_tolerance=0.0001,
        timeout_seconds=2,
    )
    store = shadow.PgvectorShadowStore(config, dimension=2)
    connection = _Connection()
    store._connection = connection

    store.ensure_schema()
    store.ensure_schema()

    assert len(connection.calls) == 4
    assert "point_id numeric(20, 0)" in connection.calls[2][0]


def test_active_ids_and_batch_delete_share_one_connection():
    class _Rows:
        def fetchall(self):
            return [(1,), (2,)]

    class _Connection:
        def __init__(self):
            self.calls = []

        def execute(self, statement, parameters=None):
            self.calls.append((statement, parameters))
            return _Rows()

    config = shadow.ShadowConfig(
        mode="shadow_pgvector",
        dsn="postgresql://shadow",
        schema="shadow",
        table="pages",
        sample_rate=0.1,
        score_tolerance=0.0001,
        timeout_seconds=2,
    )
    store = shadow.PgvectorShadowStore(config, dimension=2)
    connection = _Connection()
    store._connection = connection

    assert store.active_ids(model="v1") == {1, 2}
    store.delete_many([1, 2])

    assert connection.calls[0][1] == ("v1",)
    assert connection.calls[1][1] == ([1, 2],)
