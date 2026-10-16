"""Future-capability fixture for version-pinned model and search substitution."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Protocol, TypedDict


class CapabilityError(RuntimeError):
    """A capability transition is invalid."""


class QuarantinedRunError(CapabilityError):
    """A retained run cannot safely resume on this worker."""


class Adapter(Protocol):
    def __call__(self, value: str) -> str: ...


@dataclass(frozen=True)
class CapabilitySet:
    capability_id: str
    policy_version: str
    state_version: str
    receipt_version: str
    model_id: str
    search_id: str
    search: Adapter
    model: Adapter
    specialist: Adapter | None = None


class CapabilityRegistry:
    def __init__(self, capabilities: tuple[CapabilitySet, ...], default_id: str):
        self._capabilities = {item.capability_id: item for item in capabilities}
        self.default_id = default_id
        self.resolve(default_id)

    def resolve(self, capability_id: str) -> CapabilitySet:
        try:
            return self._capabilities[capability_id]
        except KeyError as error:
            raise QuarantinedRunError(
                f"pinned capability {capability_id} is unavailable"
            ) from error

    def select_default(self, capability_id: str) -> None:
        self.resolve(capability_id)
        self.default_id = capability_id

    def admit(self, run_id: str) -> CapabilityState:
        selected = self.resolve(self.default_id)
        return CapabilityState(
            run_id=run_id,
            capability_id=selected.capability_id,
            policy_version=selected.policy_version,
            state_version=selected.state_version,
            receipt_version=selected.receipt_version,
            model_id=selected.model_id,
            search_id=selected.search_id,
            search_receipt="",
            specialist_receipt="",
            artifact_json="",
        )


class CapabilityState(TypedDict):
    run_id: str
    capability_id: str
    policy_version: str
    state_version: str
    receipt_version: str
    model_id: str
    search_id: str
    search_receipt: str
    specialist_receipt: str
    artifact_json: str


def _receipt(kind: str, capability: CapabilitySet, value: str, output: str) -> str:
    material = {
        "kind": kind,
        "capability_id": capability.capability_id,
        "receipt_version": capability.receipt_version,
        "input": value,
        "output": output,
    }
    return hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()


def validate_state(state: CapabilityState, capability: CapabilitySet) -> None:
    expected = (
        capability.policy_version,
        capability.state_version,
        capability.receipt_version,
        capability.model_id,
        capability.search_id,
    )
    actual = (
        state["policy_version"],
        state["state_version"],
        state["receipt_version"],
        state["model_id"],
        state["search_id"],
    )
    if actual != expected:
        raise QuarantinedRunError("retained state differs from its pinned capability")


def build_capability_graph(registry: CapabilityRegistry, checkpointer: Any) -> Any:
    try:
        from langgraph.graph import END, START, StateGraph
    except ImportError as error:
        raise CapabilityError("LangGraph is required for this experiment") from error
    graph = StateGraph(CapabilityState)

    def search(state: CapabilityState) -> dict[str, str]:
        capability = registry.resolve(state["capability_id"])
        validate_state(state, capability)
        output = capability.search("research question")
        return {
            "search_receipt": _receipt(
                "search", capability, "research question", output
            )
        }

    def specialist(state: CapabilityState) -> dict[str, str]:
        capability = registry.resolve(state["capability_id"])
        validate_state(state, capability)
        if capability.specialist is None:
            return {}
        output = capability.specialist(state["search_receipt"])
        return {
            "specialist_receipt": _receipt(
                "specialist", capability, state["search_receipt"], output
            )
        }

    def synthesize(state: CapabilityState) -> dict[str, str]:
        capability = registry.resolve(state["capability_id"])
        validate_state(state, capability)
        evidence = state["specialist_receipt"] or state["search_receipt"]
        answer = capability.model(evidence)
        artifact = {
            "schema": "research/1",
            "answer": answer,
            "sources": [state["search_receipt"]],
        }
        return {
            "artifact_json": json.dumps(artifact, sort_keys=True, separators=(",", ":"))
        }

    graph.add_node("search", search)
    graph.add_node("specialist", specialist)
    graph.add_node("synthesize", synthesize)
    graph.add_edge(START, "search")
    graph.add_edge("search", "specialist")
    graph.add_edge("specialist", "synthesize")
    graph.add_edge("synthesize", END)
    return graph.compile(checkpointer=checkpointer)


class ImperativeCapabilityRuntime:
    def __init__(self, registry: CapabilityRegistry):
        self.registry = registry

    def search(self, state: CapabilityState) -> CapabilityState:
        capability = self.registry.resolve(state["capability_id"])
        validate_state(state, capability)
        output = capability.search("research question")
        return CapabilityState(
            **{
                **state,
                "search_receipt": _receipt(
                    "search", capability, "research question", output
                ),
            }
        )

    def finish(self, state: CapabilityState) -> CapabilityState:
        capability = self.registry.resolve(state["capability_id"])
        validate_state(state, capability)
        specialist_receipt = state["specialist_receipt"]
        if capability.specialist is not None:
            output = capability.specialist(state["search_receipt"])
            specialist_receipt = _receipt(
                "specialist", capability, state["search_receipt"], output
            )
        evidence = specialist_receipt or state["search_receipt"]
        artifact = {
            "schema": "research/1",
            "answer": capability.model(evidence),
            "sources": [state["search_receipt"]],
        }
        return CapabilityState(
            **{
                **state,
                "specialist_receipt": specialist_receipt,
                "artifact_json": json.dumps(
                    artifact, sort_keys=True, separators=(",", ":")
                ),
            }
        )
