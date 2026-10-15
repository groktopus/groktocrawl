"""Tests for the representative W11 workflow-composition journey."""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "run_w11_composition", ROOT / "scripts" / "run_w11_composition.py"
)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def call_tool(self, name: str, arguments: dict):
        self.calls.append((name, arguments))
        source = {"contract": "slopsearx.artifact_ref", "version": 1, "kind": "snapshot", "id": "snap-1"}
        job_artifact = {
            "contract": "slopsearx.artifact_ref",
            "version": 1,
            "kind": "research_job",
            "id": "job-1",
        }
        if name == "slopsearx_search":
            return {"meta": {"artifact": source}}
        if name == "slopsearx_start_research":
            return {"job_id": "job-1", "artifact": job_artifact, "source": {"artifact": source}}
        if name == "slopsearx_get_job":
            return {
                "job_id": "job-1",
                "state": "succeeded",
                "artifact": job_artifact,
                "queries": [{"attempts": [{"state": "done"}]}],
            }
        if name == "slopsearx_export_research_manifest":
            return {
                "source": {"artifact": job_artifact},
                "items": [{"result_id": "snap-2:0"}],
                "observations_verified": False,
            }
        if name == "slopsearx_get_artifact_lineage":
            return {"edges": [{"relation": "derived_from", "to": source}]}
        raise AssertionError(name)


def test_representative_composition_preserves_lineage_and_replay():
    client = FakeClient()
    public, private = module.run_case(
        client,
        query="research question",
        followup_query="bounded followup",
        engines=["wikipedia"],
        idempotency_key="compose-1",
        timeout_seconds=1,
    )

    assert public["hard_gate_passed"] is True
    assert public["query_sha256"] != "research question"
    assert public["attempt_count"] == 1
    assert private["search"]["meta"]["artifact"]["kind"] == "snapshot"
    starts = [arguments for name, arguments in client.calls if name == "slopsearx_start_research"]
    assert len(starts) == 2
    assert starts[0] == starts[1]


def test_require_rejects_tool_error_without_echoing_message():
    try:
        module.require({"error": {"code": "source_expired", "message": "private detail"}}, "compose")
    except RuntimeError as error:
        assert str(error) == "compose failed: source_expired"
    else:
        raise AssertionError("tool error was accepted")
