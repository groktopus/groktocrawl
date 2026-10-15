"""Future-capability comparison for bounded adaptive replanning."""

import importlib.util

import pytest
from agent.experimental.adaptive_runtime import (
    AdaptiveObservation,
    AdaptivePolicy,
    ImperativeAdaptiveRuntime,
    LangGraphAdaptiveRuntime,
    compare_adaptive_outcomes,
)


class ScriptedEvidence:
    def __init__(self, signals):
        self.signals = iter(signals)
        self.calls = []

    async def __call__(self, query):
        self.calls.append(query)
        signal = next(self.signals)
        return AdaptiveObservation(f"evidence-{len(self.calls)}", signal)


@pytest.mark.asyncio
async def test_imperative_replans_weak_evidence_and_stops_when_adequate():
    adapter = ScriptedEvidence(["weak", "adequate"])
    outcome, _ = await ImperativeAdaptiveRuntime().run(
        "imperative-run", "broad question", AdaptivePolicy(), adapter
    )
    assert outcome.stop_reason == "adequate"
    assert outcome.accounting.state == "completed"
    assert outcome.queries == (
        "broad question",
        "broad question :: resolve-weak-1",
    )
    assert outcome.accounting.spent.searches == 2


@pytest.mark.asyncio
async def test_imperative_preserves_contradiction_and_bounds_replanning():
    adapter = ScriptedEvidence(["contradictory", "weak", "weak"])
    outcome, _ = await ImperativeAdaptiveRuntime().run(
        "bounded-run", "question", AdaptivePolicy(max_replans=2), adapter
    )
    assert outcome.stop_reason == "replan_limit"
    assert outcome.accounting.state == "failed"
    assert outcome.contradictions_preserved
    assert len(adapter.calls) == 3


@pytest.mark.asyncio
async def test_imperative_stops_before_dispatch_when_time_limit_is_exhausted():
    adapter = ScriptedEvidence(["adequate"])
    outcome, _ = await ImperativeAdaptiveRuntime().run(
        "timed-run", "question", AdaptivePolicy(max_elapsed_ms=0), adapter
    )
    assert outcome.stop_reason == "time_limit"
    assert outcome.accounting.state == "failed"
    assert adapter.calls == []


@pytest.mark.asyncio
async def test_replay_uses_receipts_without_repeating_adapter_calls():
    first_adapter = ScriptedEvidence(["weak", "adequate"])
    runtime = ImperativeAdaptiveRuntime()
    first, receipts = await runtime.run(
        "first-run", "question", AdaptivePolicy(), first_adapter
    )
    replay_adapter = ScriptedEvidence([])
    replay, _ = await runtime.run(
        "replay-run", "question", AdaptivePolicy(), replay_adapter, receipts=receipts
    )
    assert compare_adaptive_outcomes(first, replay) == ()
    assert replay.adapter_calls == 0
    assert replay_adapter.calls == []


@pytest.mark.asyncio
@pytest.mark.skipif(
    importlib.util.find_spec("langgraph") is None,
    reason="LangGraph is installed only in the optional W4 experiment lane",
    owner="repository-maintainer",
    issue="#255",
    classification="retained",
    environment="default test lane excludes the optional LangGraph dependency",
)  # type: ignore[call-arg]
@pytest.mark.parametrize(
    "signals",
    [
        ("adequate",),
        ("weak", "adequate"),
        ("contradictory", "weak", "adequate"),
        ("weak", "weak", "weak"),
    ],
)
async def test_real_langgraph_adaptive_loop_matches_reference(signals):
    policy = AdaptivePolicy(max_replans=2)
    imperative_adapter = ScriptedEvidence(signals)
    graph_adapter = ScriptedEvidence(signals)
    imperative, _ = await ImperativeAdaptiveRuntime().run(
        "imperative-run", "question", policy, imperative_adapter
    )
    graph, _ = await LangGraphAdaptiveRuntime().run(
        "graph-run", "question", policy, graph_adapter
    )
    assert compare_adaptive_outcomes(imperative, graph) == ()
    assert graph.adapter_calls == imperative.adapter_calls


@pytest.mark.asyncio
@pytest.mark.skipif(
    importlib.util.find_spec("langgraph") is None,
    reason="LangGraph is installed only in the optional W4 experiment lane",
    owner="repository-maintainer",
    issue="#255",
    classification="retained",
    environment="default test lane excludes the optional LangGraph dependency",
)  # type: ignore[call-arg]
async def test_real_langgraph_respects_time_limit_before_dispatch():
    adapter = ScriptedEvidence(["adequate"])
    outcome, _ = await LangGraphAdaptiveRuntime().run(
        "timed-graph", "question", AdaptivePolicy(max_elapsed_ms=0), adapter
    )
    assert outcome.stop_reason == "time_limit"
    assert outcome.accounting.state == "failed"
    assert adapter.calls == []


@pytest.mark.asyncio
@pytest.mark.skipif(
    importlib.util.find_spec("langgraph") is None,
    reason="LangGraph is installed only in the optional W4 experiment lane",
    owner="repository-maintainer",
    issue="#255",
    classification="retained",
    environment="default test lane excludes the optional LangGraph dependency",
)  # type: ignore[call-arg]
async def test_real_langgraph_replay_reuses_completed_receipts():
    runtime = LangGraphAdaptiveRuntime()
    first_adapter = ScriptedEvidence(["weak", "adequate"])
    first, receipts = await runtime.run(
        "first-graph", "question", AdaptivePolicy(), first_adapter
    )
    replay_adapter = ScriptedEvidence([])
    replay, _ = await runtime.run(
        "replay-graph",
        "question",
        AdaptivePolicy(),
        replay_adapter,
        receipts=receipts,
    )
    assert compare_adaptive_outcomes(first, replay) == ()
    assert replay.adapter_calls == 0
    assert replay_adapter.calls == []
