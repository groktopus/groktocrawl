"""Pinned model/search capability evolution for both W4 runtime arms."""

import importlib.util
import json

import pytest
from agent.experimental.capability_runtime import (
    CapabilityRegistry,
    CapabilitySet,
    ImperativeCapabilityRuntime,
    QuarantinedRunError,
    build_capability_graph,
)


class Adapter:
    def __init__(self, name):
        self.name, self.calls = name, []

    def __call__(self, value):
        self.calls.append(value)
        return f"{self.name}:{value}"


def capabilities():
    adapters = {
        name: Adapter(name)
        for name in ("search-1", "model-1", "search-2", "specialist-2", "model-2")
    }
    first = CapabilitySet(
        "capability/1",
        "policy/1",
        "state/1",
        "receipt/1",
        "model/1",
        "search/1",
        adapters["search-1"],
        adapters["model-1"],
    )
    second = CapabilitySet(
        "capability/2",
        "policy/2",
        "state/2",
        "receipt/2",
        "model/2",
        "search/2",
        adapters["search-2"],
        adapters["model-2"],
        adapters["specialist-2"],
    )
    return first, second, adapters


def test_imperative_old_run_resumes_pinned_and_new_run_uses_upgrade():
    first, second, adapters = capabilities()
    registry = CapabilityRegistry((first, second), first.capability_id)
    runtime = ImperativeCapabilityRuntime(registry)
    old = runtime.search(registry.admit("old"))
    registry.select_default(second.capability_id)
    old = runtime.finish(old)
    new = runtime.finish(runtime.search(registry.admit("new")))
    old_artifact, new_artifact = (
        json.loads(old["artifact_json"]),
        json.loads(new["artifact_json"]),
    )
    assert old["capability_id"] == first.capability_id
    assert new["capability_id"] == second.capability_id
    assert old_artifact.keys() == new_artifact.keys()
    assert old_artifact["schema"] == new_artifact["schema"] == "research/1"
    assert len(adapters["search-1"].calls) == 1
    assert len(adapters["specialist-2"].calls) == 1
    registry.select_default(first.capability_id)
    assert registry.admit("rollback")["capability_id"] == first.capability_id


def test_missing_or_incompatible_pinned_capability_is_quarantined_without_calls():
    first, second, adapters = capabilities()
    original = CapabilityRegistry((first,), first.capability_id)
    retained = ImperativeCapabilityRuntime(original).search(original.admit("old"))
    replacement = CapabilityRegistry((second,), second.capability_id)
    with pytest.raises(QuarantinedRunError, match="unavailable"):
        ImperativeCapabilityRuntime(replacement).finish(retained)
    assert adapters["model-2"].calls == []
    retained["capability_id"] = second.capability_id
    with pytest.raises(QuarantinedRunError, match="differs"):
        ImperativeCapabilityRuntime(replacement).finish(retained)
    assert adapters["model-2"].calls == []


@pytest.mark.skipif(
    importlib.util.find_spec("langgraph") is None
    or importlib.util.find_spec("langgraph.checkpoint.sqlite") is None,
    reason="capability resume is isolated to the optional W4 LangGraph lane",
    owner="repository-maintainer",
    issue="#264",
    classification="retained",
    environment="default test lane excludes optional LangGraph persistence",
)  # type: ignore[call-arg]
def test_real_langgraph_old_checkpoint_keeps_pinned_semantics(tmp_path):
    from langgraph.checkpoint.sqlite import SqliteSaver

    first, second, adapters = capabilities()
    registry = CapabilityRegistry((first, second), first.capability_id)
    with SqliteSaver.from_conn_string(str(tmp_path / "graph.sqlite")) as saver:
        graph = build_capability_graph(registry, saver)
        old_config = {"configurable": {"thread_id": "old"}}
        graph.invoke(registry.admit("old"), old_config, interrupt_after=["search"])
        registry.select_default(second.capability_id)
        old = graph.invoke(None, old_config)
        new = graph.invoke(
            registry.admit("new"), {"configurable": {"thread_id": "new"}}
        )
    assert old["capability_id"] == first.capability_id
    assert new["capability_id"] == second.capability_id
    assert json.loads(old["artifact_json"])["schema"] == "research/1"
    assert json.loads(new["artifact_json"])["schema"] == "research/1"
    assert len(adapters["search-1"].calls) == 1
    assert len(adapters["specialist-2"].calls) == 1
