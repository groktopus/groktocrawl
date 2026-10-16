"""Future-capability comparison: dynamic specialist research branches."""

from __future__ import annotations

import asyncio
import hashlib
import json
import operator
from dataclasses import dataclass
from typing import Annotated, Literal, Protocol, TypedDict

from .execution import Budget, ExecutionLedger, ExecutionState

Stance = Literal["supports", "challenges"]
SpecialistRuntimeName = Literal["imperative", "langgraph"]


@dataclass(frozen=True)
class SpecialistAssignment:
    specialist_id: str
    question: str


@dataclass(frozen=True)
class SpecialistFinding:
    specialist_id: str
    output_id: str
    stance: Stance
    source_ids: tuple[str, ...]


@dataclass(frozen=True)
class SpecialistOutcome:
    runtime: SpecialistRuntimeName
    accounting: ExecutionState
    findings: tuple[SpecialistFinding, ...]
    synthesis_digest: str | None
    adapter_calls: int


class SpecialistAdapter(Protocol):
    async def __call__(self, assignment: SpecialistAssignment) -> SpecialistFinding: ...


def _operation(assignment: SpecialistAssignment) -> tuple[str, str]:
    raw = json.dumps(
        {"id": assignment.specialist_id, "question": assignment.question},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    digest = hashlib.sha256(raw).hexdigest()
    return f"specialist-{assignment.specialist_id}-{digest[:10]}", digest


def _synthesis(findings: tuple[SpecialistFinding, ...]) -> str:
    canonical = [
        {
            "specialist_id": item.specialist_id,
            "output_id": item.output_id,
            "stance": item.stance,
            "source_ids": item.source_ids,
        }
        for item in sorted(findings, key=lambda value: value.specialist_id)
    ]
    return hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class _SpecialistExecution:
    def __init__(self, run_id: str, assignments: tuple[SpecialistAssignment, ...]):
        self.ledger = ExecutionLedger(
            run_id=run_id,
            policy_version="dynamic-specialists/1",
            limit=Budget(searches=len(assignments)),
            max_operations=max(1, len(assignments)),
        )
        self.lock = asyncio.Lock()
        self.adapter_calls = 0

    async def invoke(
        self,
        assignment: SpecialistAssignment,
        adapter: SpecialistAdapter,
        cancel: asyncio.Event | None,
    ) -> SpecialistFinding:
        operation_id, digest = _operation(assignment)
        async with self.lock:
            self.ledger.reserve(
                operation_id=operation_id,
                input_digest=digest,
                budget=Budget(searches=1),
                expected_revision=self.ledger.state.revision,
            )
        if cancel is not None and cancel.is_set():
            raise asyncio.CancelledError()
        self.adapter_calls += 1
        finding = await adapter(assignment)
        if finding.specialist_id != assignment.specialist_id:
            raise ValueError("specialist output identity differs from assignment")
        if cancel is not None and cancel.is_set():
            raise asyncio.CancelledError()
        async with self.lock:
            self.ledger.complete(
                operation_id=operation_id,
                input_digest=digest,
                output_id=finding.output_id,
                actual=Budget(searches=1),
                expected_revision=self.ledger.state.revision,
            )
        return finding

    def finish(self, completed: bool) -> None:
        if self.ledger.state.state != "running":
            return
        if completed:
            self.ledger.finish(
                outcome="completed", expected_revision=self.ledger.state.revision
            )
        else:
            self.ledger.cancel(expected_revision=self.ledger.state.revision)


async def _await_branches(
    tasks: tuple[asyncio.Task[SpecialistFinding], ...],
    cancel: asyncio.Event | None,
) -> tuple[SpecialistFinding, ...]:
    if cancel is None:
        return tuple(await asyncio.gather(*tasks))
    group = asyncio.gather(*tasks)
    cancel_task = asyncio.create_task(cancel.wait())
    try:
        done, _ = await asyncio.wait(
            {group, cancel_task}, return_when=asyncio.FIRST_COMPLETED
        )  # type: ignore[type-var]
        if cancel_task in done:
            group.cancel()
            await asyncio.gather(group, return_exceptions=True)
            raise asyncio.CancelledError()
        return tuple(group.result())
    finally:
        cancel_task.cancel()
        await asyncio.gather(cancel_task, return_exceptions=True)


class ImperativeSpecialistRuntime:
    async def run(
        self,
        run_id: str,
        assignments: tuple[SpecialistAssignment, ...],
        adapter: SpecialistAdapter,
        *,
        cancel: asyncio.Event | None = None,
    ) -> SpecialistOutcome:
        execution = _SpecialistExecution(run_id, assignments)
        tasks = tuple(
            asyncio.create_task(execution.invoke(item, adapter, cancel))
            for item in assignments
        )
        try:
            findings = await _await_branches(tasks, cancel)
        except BaseException:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            execution.finish(False)
            return SpecialistOutcome(
                "imperative", execution.ledger.state, (), None, execution.adapter_calls
            )
        ordered = tuple(sorted(findings, key=lambda item: item.specialist_id))
        execution.finish(True)
        return SpecialistOutcome(
            "imperative",
            execution.ledger.state,
            ordered,
            _synthesis(ordered),
            execution.adapter_calls,
        )


class _SpecialistGraphState(TypedDict, total=False):
    assignments: tuple[SpecialistAssignment, ...]
    assignment: SpecialistAssignment
    findings: Annotated[tuple[SpecialistFinding, ...], operator.add]
    synthesis_digest: str


class LangGraphSpecialistRuntime:
    async def run(
        self,
        run_id: str,
        assignments: tuple[SpecialistAssignment, ...],
        adapter: SpecialistAdapter,
        *,
        cancel: asyncio.Event | None = None,
    ) -> SpecialistOutcome:
        try:
            from langgraph.graph import END, START, StateGraph
            from langgraph.types import Send
        except ImportError as error:
            raise RuntimeError("LangGraph is required for this experiment") from error

        execution = _SpecialistExecution(run_id, assignments)
        graph = StateGraph(_SpecialistGraphState)

        async def orchestrate(state: _SpecialistGraphState) -> dict[str, object]:
            del state
            return {}

        def dispatch(state: _SpecialistGraphState):
            return [
                Send("specialist", {"assignment": item, "findings": ()})
                for item in state["assignments"]
            ]

        async def specialist(state: _SpecialistGraphState) -> dict[str, object]:
            finding = await execution.invoke(state["assignment"], adapter, cancel)
            return {"findings": (finding,)}

        async def synthesize(state: _SpecialistGraphState) -> dict[str, object]:
            return {"synthesis_digest": _synthesis(state["findings"])}

        graph.add_node("orchestrate", orchestrate)
        graph.add_node("specialist", specialist)
        graph.add_node("synthesize", synthesize)
        graph.add_edge(START, "orchestrate")
        graph.add_conditional_edges("orchestrate", dispatch, ["specialist"])
        graph.add_edge("specialist", "synthesize")
        graph.add_edge("synthesize", END)
        compiled = graph.compile()
        graph_task = asyncio.create_task(
            compiled.ainvoke({"assignments": assignments, "findings": ()})
        )
        cancel_task = asyncio.create_task(cancel.wait()) if cancel is not None else None
        try:
            if cancel_task is None:
                state = await graph_task
            else:
                done, _ = await asyncio.wait(
                    {graph_task, cancel_task}, return_when=asyncio.FIRST_COMPLETED
                )
                if cancel_task in done:
                    raise asyncio.CancelledError()
                state = graph_task.result()
        except BaseException:
            graph_task.cancel()
            await asyncio.gather(graph_task, return_exceptions=True)
            execution.finish(False)
            return SpecialistOutcome(
                "langgraph", execution.ledger.state, (), None, execution.adapter_calls
            )
        finally:
            if cancel_task is not None:
                cancel_task.cancel()
                await asyncio.gather(cancel_task, return_exceptions=True)
        ordered = tuple(sorted(state["findings"], key=lambda item: item.specialist_id))
        execution.finish(True)
        return SpecialistOutcome(
            "langgraph",
            execution.ledger.state,
            ordered,
            state["synthesis_digest"],
            execution.adapter_calls,
        )


def compare_specialist_outcomes(
    reference: SpecialistOutcome, candidate: SpecialistOutcome
) -> tuple[str, ...]:
    failures = []
    if reference.findings != candidate.findings:
        failures.append("findings differ")
    if reference.synthesis_digest != candidate.synthesis_digest:
        failures.append("synthesis differs")
    left = reference.accounting.model_dump(exclude={"run_id"})
    right = candidate.accounting.model_dump(exclude={"run_id"})
    if left != right:
        failures.append("accounting differs")
    return tuple(failures)
