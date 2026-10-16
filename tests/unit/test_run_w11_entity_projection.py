from typing import Any

from scripts.run_w11_entity_projection import run_case


class FakeClient:
    def __init__(self, *, mutate_flat: bool = False) -> None:
        self.mutate_flat = mutate_flat
        self.flat_reads = 0

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name == "slopsearx_search":
            return {
                "results": [{"result_id": "snap:0"}, {"result_id": "snap:1"}],
                "meta": {"cursor": "snap"},
            }
        if name == "slopsearx_read_results":
            self.flat_reads += 1
            return {"results": [1, 2 + (1 if self.mutate_flat and self.flat_reads > 1 else 0)]}
        if name == "slopsearx_read_entities":
            return {
                "entities": [
                    {
                        "entity_id": "entity-1",
                        "namespace": "cve",
                        "result_ids": ["snap:0", "snap:1"],
                        "conflicting_fields": ["description"],
                    }
                ],
                "relationships": [],
                "meta": {"has_more": False},
            }
        raise AssertionError(name)


CASE = {"case_id": "case", "query": "q", "engines": ["nvd"], "max_results": 2}


def test_projection_conserves_results_without_authorizing_fetch_suppression() -> None:
    public, private = run_case(FakeClient(), CASE)
    assert public["hard_gate_passed"] is True
    assert public["potential_repeated_acquisitions"] == 1
    assert public["automatic_fetch_suppression_authorized"] is False
    assert public["entities"][0]["conflicting_fields"] == ["description"]
    assert private["case"] == CASE


def test_projection_fails_when_flat_snapshot_changes() -> None:
    public, _ = run_case(FakeClient(mutate_flat=True), CASE)
    assert public["flat_snapshot_unchanged"] is False
    assert public["hard_gate_passed"] is False
