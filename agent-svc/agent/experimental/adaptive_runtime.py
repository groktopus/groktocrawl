"""Future-capability comparison: bounded adaptive research replanning."""

from __future__ import annotations

import hashlib
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal, Protocol, TypedDict, cast

from .execution import Budget, ExecutionLedger, ExecutionState

EvidenceSignal = Literal["adequate", "weak", "contradictory"]
AdaptiveRuntimeName = Literal["imperative", "langgraph"]
StopReason = Literal["adequate", "replan_limit", "time_limit", "failed"]


@dataclass(frozen=True)
class AdaptiveObservation:
    output_id: str
    signal: EvidenceSignal
    actual: Budget = field(default_factory=lambda: Budget(searches=1))


@dataclass(frozen=True)
class AdaptiveReceipt:
    operation_id: str
    input_digest: str
    query: str
    observation: AdaptiveObservation


@dataclass(frozen=True)
class AdaptivePolicy:
    policy_version: str = "adaptive-research/1"
    max_replans: int = 2
    max_operations: int = 3
    max_elapsed_ms: int = 30_000
    budget: Budget = field(default_factory=lambda: Budget(searches=3))

    def follow_up(self, query: str, signal: EvidenceSignal, depth: int) -> str:
        return f"{query} :: resolve-{signal}-{depth + 1}"


@dataclass(frozen=True)
class AdaptiveOutcome:
    runtime: AdaptiveRuntimeName
    accounting: ExecutionState
    queries: tuple[str, ...]
    signals: tuple[EvidenceSignal, ...]
    outputs: tuple[str, ...]
    stop_reason: StopReason
    adapter_calls: int

    @property
    def contradictions_preserved(self) -> bool:
        return "contradictory" in self.signals


class AdaptiveAdapter(Protocol):
    async def __call__(self, query: str) -> AdaptiveObservation: ...


def _identity(query: str, depth: int) -> tuple[str, str]:
    digest = hashlib.sha256(query.encode()).hexdigest()
    return f"search-{depth}-{digest[:12]}", digest


class _AdaptiveExecution:
    def __init__(
        self,
        run_id: str,
        policy: AdaptivePolicy,
        adapter: AdaptiveAdapter,
        receipts: Mapping[str, AdaptiveReceipt] | None,
    ) -> None:
        self.ledger = ExecutionLedger(
            run_id=run_id,
            policy_version=policy.policy_version,
            limit=policy.budget,
            max_operations=policy.max_operations,
        )
        self.adapter = adapter
        self.receipts = dict(receipts or {})
        self.adapter_calls = 0

    async def observe(self, query: str, depth: int) -> AdaptiveReceipt:
        operation_id, digest = _identity(query, depth)
        before = self.ledger.state.revision
        self.ledger.reserve(
            operation_id=operation_id,
            input_digest=digest,
            budget=Budget(searches=1),
            expected_revision=before,
        )
        receipt = self.receipts.get(operation_id)
        if receipt is not None:
            if receipt.input_digest != digest or receipt.query != query:
                raise ValueError("adaptive receipt conflicts with query")
        else:
            observation = await self.adapter(query)
            self.adapter_calls += 1
            receipt = AdaptiveReceipt(operation_id, digest, query, observation)
            self.receipts[operation_id] = receipt
        self.ledger.complete(
            operation_id=operation_id,
            input_digest=digest,
            output_id=receipt.observation.output_id,
            actual=receipt.observation.actual,
            expected_revision=self.ledger.state.revision,
        )
        return receipt

    def finish(self, completed: bool) -> ExecutionState:
        return self.ledger.finish(
            outcome="completed" if completed else "failed",
            expected_revision=self.ledger.state.revision,
        )


class ImperativeAdaptiveRuntime:
    async def run(
        self,
        run_id: str,
        query: str,
        policy: AdaptivePolicy,
        adapter: AdaptiveAdapter,
        *,
        receipts: Mapping[str, AdaptiveReceipt] | None = None,
    ) -> tuple[AdaptiveOutcome, dict[str, AdaptiveReceipt]]:
        execution = _AdaptiveExecution(run_id, policy, adapter, receipts)
        queries: list[str] = []
        signals: list[EvidenceSignal] = []
        outputs: list[str] = []
        current = query
        reason: StopReason = "failed"
        deadline = time.monotonic() + (policy.max_elapsed_ms / 1000)
        for depth in range(policy.max_replans + 1):
            if time.monotonic() >= deadline:
                execution.finish(False)
                reason = "time_limit"
                break
            try:
                receipt = await execution.observe(current, depth)
            except (ValueError, RuntimeError):
                execution.finish(False)
                return (
                    AdaptiveOutcome(
                        "imperative",
                        execution.ledger.state,
                        tuple(queries),
                        tuple(signals),
                        tuple(outputs),
                        "failed",
                        execution.adapter_calls,
                    ),
                    execution.receipts,
                )
            queries.append(current)
            signals.append(receipt.observation.signal)
            outputs.append(receipt.observation.output_id)
            if receipt.observation.signal == "adequate":
                execution.finish(True)
                reason = "adequate"
                break
            if depth == policy.max_replans:
                execution.finish(False)
                reason = "replan_limit"
                break
            current = policy.follow_up(current, receipt.observation.signal, depth)
        return (
            AdaptiveOutcome(
                "imperative",
                execution.ledger.state,
                tuple(queries),
                tuple(signals),
                tuple(outputs),
                reason,
                execution.adapter_calls,
            ),
            execution.receipts,
        )


class _AdaptiveGraphState(TypedDict):
    query: str
    depth: int
    queries: tuple[str, ...]
    signals: tuple[EvidenceSignal, ...]
    outputs: tuple[str, ...]
    stop_reason: Literal["", "adequate", "replan_limit", "time_limit", "failed"]


class LangGraphAdaptiveRuntime:
    async def run(
        self,
        run_id: str,
        query: str,
        policy: AdaptivePolicy,
        adapter: AdaptiveAdapter,
        *,
        receipts: Mapping[str, AdaptiveReceipt] | None = None,
    ) -> tuple[AdaptiveOutcome, dict[str, AdaptiveReceipt]]:
        try:
            from langgraph.graph import END, START, StateGraph
        except ImportError as error:
            raise RuntimeError("LangGraph is required for this experiment") from error

        execution = _AdaptiveExecution(run_id, policy, adapter, receipts)
        deadline = time.monotonic() + (policy.max_elapsed_ms / 1000)
        graph = StateGraph(_AdaptiveGraphState)

        async def research(state: _AdaptiveGraphState) -> dict[str, object]:
            if time.monotonic() >= deadline:
                return {"stop_reason": "time_limit"}
            try:
                receipt = await execution.observe(state["query"], state["depth"])
            except (ValueError, RuntimeError):
                return {"stop_reason": "failed"}
            signal = receipt.observation.signal
            update: dict[str, object] = {
                "queries": (*state["queries"], state["query"]),
                "signals": (*state["signals"], signal),
                "outputs": (*state["outputs"], receipt.observation.output_id),
            }
            if signal == "adequate":
                update["stop_reason"] = "adequate"
            elif state["depth"] >= policy.max_replans:
                update["stop_reason"] = "replan_limit"
            else:
                update["query"] = policy.follow_up(
                    state["query"], signal, state["depth"]
                )
                update["depth"] = state["depth"] + 1
            return update

        def route(state: _AdaptiveGraphState) -> str:
            return "stop" if state["stop_reason"] else "replan"

        graph.add_node("research", research)
        graph.add_edge(START, "research")
        graph.add_conditional_edges(
            "research", route, {"replan": "research", "stop": END}
        )
        compiled = graph.compile()
        state = await compiled.ainvoke(
            {
                "query": query,
                "depth": 0,
                "queries": (),
                "signals": (),
                "outputs": (),
                "stop_reason": "",
            }
        )
        completed = state["stop_reason"] == "adequate"
        execution.finish(completed)
        return (
            AdaptiveOutcome(
                "langgraph",
                execution.ledger.state,
                state["queries"],
                state["signals"],
                state["outputs"],
                cast(StopReason, state["stop_reason"]),
                execution.adapter_calls,
            ),
            execution.receipts,
        )


def compare_adaptive_outcomes(
    reference: AdaptiveOutcome, candidate: AdaptiveOutcome
) -> tuple[str, ...]:
    failures = []
    for attribute in ("queries", "signals", "outputs", "stop_reason"):
        if getattr(reference, attribute) != getattr(candidate, attribute):
            failures.append(f"{attribute} differs")
    reference_state = reference.accounting.model_dump(exclude={"run_id"})
    candidate_state = candidate.accounting.model_dump(exclude={"run_id"})
    if reference_state != candidate_state:
        failures.append("accounting differs")
    return tuple(failures)
