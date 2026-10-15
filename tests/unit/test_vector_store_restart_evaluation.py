"""Deterministic checks for the isolated vector-store restart harness."""

import importlib.util
import sys
from pathlib import Path

from scripts.run_vector_store_evaluation import reference_search

SCRIPT = Path(__file__).parents[2] / "scripts" / "run_vector_store_restart_evaluation.py"
SPEC = importlib.util.spec_from_file_location("vector_restart_eval", SCRIPT)
assert SPEC and SPEC.loader
vector_restart_eval = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = vector_restart_eval
SPEC.loader.exec_module(vector_restart_eval)


def test_safe_id_is_stable_and_shell_safe():
    assert vector_restart_eval._safe_id("run/2026-09-08T08:00:00Z") == (
        "run_2026_09_08T08_00_00Z"
    )


def test_restart_evidence_keeps_deletion_gate_after_reconnect():
    class FakeStore:
        name = "fixture"

        def __init__(self):
            self.deleted = False

        def search(self, query):
            records = list(vector_restart_eval.CORPUS)
            if self.deleted:
                records[0] = vector_restart_eval.CORPUS[0].__class__(
                    "doc-001", "alpha", (1.0, 0.0, 0.0), deleted=True
                )
            return reference_search(records, query)

        def delete(self, record_id):
            assert record_id == "doc-001"
            self.deleted = True

        def close(self, cleanup):
            del cleanup

    evidence = vector_restart_eval._search_evidence(FakeStore(), include_delete=True)

    assert evidence["ok"] is True
    assert evidence["gates"]["deleted_doc_absent"] is True
    assert all(evidence["gates"].values())
