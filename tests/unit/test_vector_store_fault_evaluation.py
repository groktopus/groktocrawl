"""Deterministic checks for vector-store failure and rollback contracts."""

import importlib.util
import sys
from pathlib import Path

SCRIPT = Path(__file__).parents[2] / "scripts" / "run_vector_store_fault_evaluation.py"
SPEC = importlib.util.spec_from_file_location("vector_fault_eval", SCRIPT)
assert SPEC and SPEC.loader
vector_fault_eval = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = vector_fault_eval
SPEC.loader.exec_module(vector_fault_eval)


def test_manifest_is_pinned_and_covers_all_failure_families():
    first = vector_fault_eval.manifest()
    second = vector_fault_eval.manifest()

    assert first == second
    assert first["schema_version"] == "vector-store-fault-evaluation/1"
    assert len(first["corpus_sha256"]) == 64
    assert first["scenarios"] == [scenario.name for scenario in vector_fault_eval.SCENARIOS]


def test_all_fixture_failure_gates_pass():
    result = vector_fault_eval.run()

    assert result["all_gates_passed"] is True
    assert all(scenario["gate"] for scenario in result["scenarios"])
    assert result["provider_backed"] is False
    assert result["production_change"] is False


def test_write_timeout_preserves_state():
    result = vector_fault_eval.run_scenario(vector_fault_eval.SCENARIOS[0])

    assert result["observed"] == "failed"
    assert result["before"]["record_ids"] == result["after"]["record_ids"]
    assert result["gate"] is True


def test_partial_write_is_not_reported_as_success():
    result = vector_fault_eval.run_scenario(vector_fault_eval.SCENARIOS[1])

    assert result["observed"] == "failed"
    assert result["expected"] == "reconcile_required"
    assert result["gate"] is True


def test_restore_and_migration_keep_active_version_on_interrupt():
    for scenario in vector_fault_eval.SCENARIOS[3:5]:
        result = vector_fault_eval.run_scenario(scenario)
        assert result["observed"] == "failed"
        assert result["before"]["active_version"] == result["after"]["active_version"]
        assert result["after"]["staged_version"] is None
        assert result["gate"] is True
