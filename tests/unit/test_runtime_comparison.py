"""Equivalent-runtime conformance and negative-control fixtures."""

import asyncio
import importlib.util

import pytest
from agent.experimental.controller import OperationSpec, ScriptResult
from agent.experimental.execution import Budget
from agent.experimental.runtime_comparison import (
    GraphCandidateRuntime,
    ImperativeRuntime,
    LangGraphRuntime,
    LangGraphUnavailableError,
    RuntimeNode,
    RuntimePlan,
    compare_outcomes,
)


def spec(identity: str, *, sources: int = 1) -> OperationSpec:
    return OperationSpec(
        operation_id=identity,
        input_digest=(identity.encode().hex() + "0" * 64)[:64],
        output_id=f"out-{identity}",
        reservation=Budget(sources=sources),
    )


def node(identity: str, *, delay: float = 0, depends_on=(), sources: int = 1):
    async def execute() -> ScriptResult:
        if delay:
            await asyncio.sleep(delay)
        return ScriptResult(output_id=f"out-{identity}", actual=Budget(sources=sources))

    return RuntimeNode(spec(identity, sources=sources), execute, tuple(depends_on))


def plan(*nodes: RuntimeNode, sources: int = 10) -> RuntimePlan:
    return RuntimePlan(
        run_id="runtime-run-1",
        policy_version="fixture-policy/1",
        nodes=tuple(nodes),
        budget=Budget(sources=sources),
        max_operations=10,
    )


@pytest.mark.asyncio
async def test_imperative_and_graph_candidate_have_equivalent_accounting():
    current = plan(
        node("acquire"),
        node("construct", depends_on=("acquire",)),
        node("render", depends_on=("construct",)),
    )
    imperative = await ImperativeRuntime().run(current)
    graph = await GraphCandidateRuntime().run(current)
    assert compare_outcomes((imperative, graph)) == ()
    assert imperative.fingerprint == graph.fingerprint
    assert [event.kind for event in graph.events].count("terminal") == 1


@pytest.mark.asyncio
async def test_graph_join_is_stable_when_branches_finish_in_reverse_order():
    current = plan(
        node("slow", delay=0.02),
        node("fast", delay=0.001),
        node("join", depends_on=("slow", "fast")),
    )
    outcome = await GraphCandidateRuntime().run(current)
    assert outcome.accounting.state == "completed"
    assert outcome.outputs == (
        ("fast", "out-fast"),
        ("join", "out-join"),
        ("slow", "out-slow"),
    )
    completed = [
        event.operation_id
        for event in outcome.events
        if event.kind == "completed"
    ]
    assert completed[:2] == ["fast", "slow"]


@pytest.mark.asyncio
@pytest.mark.parametrize("runtime", [ImperativeRuntime, GraphCandidateRuntime])
async def test_cancellation_keeps_pending_budget_and_blocks_late_completion(runtime):
    started = asyncio.Event()
    release = asyncio.Event()

    async def work() -> ScriptResult:
        started.set()
        await release.wait()
        return ScriptResult(output_id="out-wait", actual=Budget(sources=1))

    current = plan(RuntimeNode(spec("wait"), work))
    cancel = asyncio.Event()
    task = asyncio.create_task(runtime().run(current, cancel=cancel))
    await started.wait()
    cancel.set()
    outcome = await task
    release.set()
    assert outcome.accounting.state == "cancelled"
    assert outcome.accounting.reserved.sources == 1
    assert outcome.outputs == ()
    assert all(event.kind != "completed" for event in outcome.events)


def test_runtime_plan_rejects_cycles():
    current = plan(node("a", depends_on=("b",)), node("b", depends_on=("a",)))
    with pytest.raises(ValueError, match="cycle"):
        current.ordered()


@pytest.mark.asyncio
async def test_conflicting_output_is_a_failed_terminal_result():
    async def overrun() -> ScriptResult:
        return ScriptResult(output_id="out-a", actual=Budget(sources=2))

    current = plan(RuntimeNode(spec("a"), overrun))
    outcome = await ImperativeRuntime().run(current)
    assert outcome.accounting.state == "failed"
    assert outcome.accounting.reserved.sources == 1
    assert any(event.kind == "failed" for event in outcome.events)


@pytest.mark.asyncio
@pytest.mark.skipif(
    importlib.util.find_spec("langgraph") is None,
    reason="LangGraph is optional until W4 measurement is authorized",
    owner="repository-maintainer",
    issue="#110",
    classification="retained",
    environment="LangGraph package is not installed in the default test lane",
)
async def test_langgraph_runtime_requires_the_optional_comparison_dependency():
    current = plan(node("acquire"))
    outcome = await LangGraphRuntime().run(current)
    assert outcome.accounting.state == "completed"
    assert outcome.outputs == (("acquire", "out-acquire"),)


@pytest.mark.asyncio
@pytest.mark.skipif(
    importlib.util.find_spec("langgraph") is None,
    reason="LangGraph is optional until W4 measurement is authorized",
    owner="repository-maintainer",
    issue="#110",
    classification="retained",
    environment="LangGraph package is not installed in the default test lane",
)
async def test_langgraph_runtime_matches_reference_for_parallel_join():
    current = plan(
        node("slow", delay=0.02),
        node("fast", delay=0.001),
        node("join", depends_on=("slow", "fast")),
    )
    imperative = await ImperativeRuntime().run(current)
    langgraph = await LangGraphRuntime().run(current)
    assert compare_outcomes((imperative, langgraph)) == ()
    assert langgraph.outputs == (
        ("fast", "out-fast"),
        ("join", "out-join"),
        ("slow", "out-slow"),
    )


@pytest.mark.asyncio
@pytest.mark.skipif(
    importlib.util.find_spec("langgraph") is None,
    reason="LangGraph is optional until W4 measurement is authorized",
    owner="repository-maintainer",
    issue="#110",
    classification="retained",
    environment="LangGraph package is not installed in the default test lane",
)
async def test_langgraph_runtime_cancellation_preserves_unsettled_reservation():
    started = asyncio.Event()
    release = asyncio.Event()

    async def work() -> ScriptResult:
        started.set()
        await release.wait()
        return ScriptResult(output_id="out-wait", actual=Budget(sources=1))

    current = plan(RuntimeNode(spec("wait"), work))
    cancel = asyncio.Event()
    task = asyncio.create_task(LangGraphRuntime().run(current, cancel=cancel))
    await started.wait()
    cancel.set()
    outcome = await task
    release.set()
    assert outcome.accounting.state == "cancelled"
    assert outcome.accounting.reserved.sources == 1
    assert outcome.outputs == ()
    assert all(event.kind != "completed" for event in outcome.events)


@pytest.mark.asyncio
async def test_langgraph_runtime_reports_a_missing_dependency_without_mutation():
    if importlib.util.find_spec("langgraph") is None:
        with pytest.raises(LangGraphUnavailableError):
            await LangGraphRuntime().run(plan(node("acquire")))
