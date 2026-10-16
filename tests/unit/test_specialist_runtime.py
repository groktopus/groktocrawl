"""Future-capability comparison for dynamic specialist fan-out."""

import asyncio
import importlib.util

import pytest
from agent.experimental.specialist_runtime import (
    ImperativeSpecialistRuntime,
    LangGraphSpecialistRuntime,
    SpecialistAssignment,
    SpecialistFinding,
    compare_specialist_outcomes,
)


class ScriptedSpecialists:
    def __init__(self, delays=None):
        self.delays = delays or {}
        self.calls = []

    async def __call__(self, assignment):
        self.calls.append(assignment.specialist_id)
        await asyncio.sleep(self.delays.get(assignment.specialist_id, 0))
        return SpecialistFinding(
            assignment.specialist_id,
            f"finding-{assignment.specialist_id}",
            "challenges" if assignment.specialist_id == "risk" else "supports",
            (f"source-{assignment.specialist_id}",),
        )


def assignments(*names):
    return tuple(SpecialistAssignment(name, f"Investigate {name}") for name in names)


@pytest.mark.asyncio
@pytest.mark.parametrize("names", [("technical",), ("technical", "risk", "user")])
async def test_imperative_dynamic_specialists_preserve_lineage(names):
    adapter = ScriptedSpecialists()
    outcome = await ImperativeSpecialistRuntime().run(
        "imperative-run", assignments(*names), adapter
    )
    assert outcome.accounting.state == "completed"
    assert {finding.specialist_id for finding in outcome.findings} == set(names)
    assert all(finding.source_ids for finding in outcome.findings)
    assert outcome.adapter_calls == len(names)


@pytest.mark.asyncio
async def test_imperative_synthesis_is_stable_when_completion_order_reverses():
    names = ("technical", "risk", "user")
    slow_first = ScriptedSpecialists({"technical": 0.03, "risk": 0.02})
    slow_last = ScriptedSpecialists({"risk": 0.02, "user": 0.03})
    first = await ImperativeSpecialistRuntime().run(
        "first-run", assignments(*names), slow_first
    )
    second = await ImperativeSpecialistRuntime().run(
        "second-run", assignments(*names), slow_last
    )
    assert compare_specialist_outcomes(first, second) == ()
    assert first.synthesis_digest == second.synthesis_digest


@pytest.mark.asyncio
async def test_imperative_cancellation_blocks_late_specialist_publication():
    started = asyncio.Event()
    release = asyncio.Event()

    async def blocked(assignment):
        started.set()
        await release.wait()
        return SpecialistFinding(
            assignment.specialist_id, "late", "supports", ("source-late",)
        )

    cancel = asyncio.Event()
    task = asyncio.create_task(
        ImperativeSpecialistRuntime().run(
            "cancelled-run", assignments("technical", "risk"), blocked, cancel=cancel
        )
    )
    await started.wait()
    cancel.set()
    outcome = await task
    release.set()
    assert outcome.accounting.state == "cancelled"
    assert outcome.findings == ()
    assert outcome.synthesis_digest is None


@pytest.mark.asyncio
@pytest.mark.skipif(
    importlib.util.find_spec("langgraph") is None,
    reason="LangGraph is installed only in the optional W4 experiment lane",
    owner="repository-maintainer",
    issue="#257",
    classification="retained",
    environment="default test lane excludes the optional LangGraph dependency",
)  # type: ignore[call-arg]
@pytest.mark.parametrize("names", [("technical",), ("technical", "risk", "user")])
async def test_real_langgraph_send_fanout_matches_reference(names):
    delays = {"technical": 0.03, "risk": 0.02}
    imperative_adapter = ScriptedSpecialists(delays)
    graph_adapter = ScriptedSpecialists(delays)
    current = assignments(*names)
    imperative = await ImperativeSpecialistRuntime().run(
        "imperative-run", current, imperative_adapter
    )
    graph = await LangGraphSpecialistRuntime().run("graph-run", current, graph_adapter)
    assert compare_specialist_outcomes(imperative, graph) == ()
    assert graph.adapter_calls == len(names)


@pytest.mark.asyncio
@pytest.mark.skipif(
    importlib.util.find_spec("langgraph") is None,
    reason="LangGraph is installed only in the optional W4 experiment lane",
    owner="repository-maintainer",
    issue="#257",
    classification="retained",
    environment="default test lane excludes the optional LangGraph dependency",
)  # type: ignore[call-arg]
async def test_real_langgraph_cancellation_blocks_synthesis():
    started = asyncio.Event()
    release = asyncio.Event()

    async def blocked(assignment):
        started.set()
        await release.wait()
        return SpecialistFinding(
            assignment.specialist_id, "late", "supports", ("source-late",)
        )

    cancel = asyncio.Event()
    task = asyncio.create_task(
        LangGraphSpecialistRuntime().run(
            "graph-cancel", assignments("technical", "risk"), blocked, cancel=cancel
        )
    )
    await started.wait()
    cancel.set()
    outcome = await task
    release.set()
    assert outcome.accounting.state == "cancelled"
    assert outcome.synthesis_digest is None
