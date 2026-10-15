"""Future-capability fixture for branching a retained investigation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, TypedDict, cast


class ForkError(RuntimeError):
    """A fork or receipt-reuse rule failed closed."""


@dataclass(frozen=True)
class ForkPolicy:
    policy_version: str = "fork-policy/1"
    model_id: str = "model/1"
    search_id: str = "search/1"

    @property
    def fingerprint(self) -> str:
        value = f"{self.policy_version}:{self.model_id}:{self.search_id}"
        return hashlib.sha256(value.encode()).hexdigest()


class EvidenceAdapter(Protocol):
    def __call__(self, question: str) -> str: ...


class ForkState(TypedDict):
    run_id: str
    scope_id: str
    fingerprint: str
    hypothesis: str
    evidence_receipt_id: str
    result_receipt_id: str


class ForkJournal:
    """Application-owned ancestry and immutable operation receipts."""

    schema_version = "fork-journal/1"

    def __init__(self, path: Path):
        self.path = path
        if not path.exists():
            self._write(
                {"schema_version": self.schema_version, "runs": {}, "receipts": {}}
            )

    def _read(self) -> dict[str, Any]:
        value = json.loads(self.path.read_text())
        if value.get("schema_version") != self.schema_version:
            raise ForkError("unsupported fork journal version")
        return value

    def _write(self, value: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        pending = self.path.with_suffix(self.path.suffix + ".pending")
        pending.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")))
        pending.replace(self.path)

    def admit(self, run_id: str, scope_id: str, policy: ForkPolicy) -> None:
        value = self._read()
        value["runs"][run_id] = {
            "scope_id": scope_id,
            "fingerprint": policy.fingerprint,
            "parent_run_id": None,
            "state": "running",
        }
        self._write(value)

    def fork(
        self, parent_run_id: str, child_run_id: str, scope_id: str, policy: ForkPolicy
    ) -> None:
        value = self._read()
        parent = value["runs"].get(parent_run_id)
        if parent is None or parent["scope_id"] != scope_id:
            raise ForkError("parent is missing or belongs to another scope")
        if parent["state"] in {"deleted", "cancelled"}:
            raise ForkError(f"parent is {parent['state']}")
        if parent["fingerprint"] != policy.fingerprint:
            raise ForkError("fork policy, model, or search identity is incompatible")
        if child_run_id in value["runs"]:
            raise ForkError("child run already exists")
        value["runs"][child_run_id] = {
            "scope_id": scope_id,
            "fingerprint": policy.fingerprint,
            "parent_run_id": parent_run_id,
            "state": "running",
        }
        self._write(value)

    def receipt(
        self,
        run_id: str,
        operation_id: str,
        input_value: str,
        adapter: EvidenceAdapter,
        *,
        reusable: bool,
    ) -> tuple[str, bool]:
        value = self._read()
        run = value["runs"].get(run_id)
        if run is None or run["state"] != "running":
            raise ForkError("run is unavailable")
        digest = hashlib.sha256(input_value.encode()).hexdigest()
        identity = f"{run['scope_id']}:{run['fingerprint']}:{operation_id}:{digest}"
        receipt_id = hashlib.sha256(identity.encode()).hexdigest()
        prior = value["receipts"].get(receipt_id)
        if reusable and prior is not None:
            return receipt_id, True
        output = adapter(input_value)
        if prior is not None and prior["output"] != output:
            raise ForkError("operation receipt conflicts with retained output")
        value["receipts"][receipt_id] = {
            "operation_id": operation_id,
            "input_digest": digest,
            "output": output,
        }
        self._write(value)
        return receipt_id, False

    def output(self, receipt_id: str) -> str:
        receipt = self._read()["receipts"].get(receipt_id)
        if receipt is None:
            raise ForkError("receipt is missing")
        return str(receipt["output"])

    def terminal(self, run_id: str, state: str) -> None:
        value = self._read()
        run = value["runs"].get(run_id)
        if run is None:
            raise ForkError("run is missing")
        run["state"] = state
        self._write(value)

    def run(self, run_id: str) -> dict[str, Any]:
        return dict(self._read()["runs"][run_id])


def build_fork_graph(
    journal: ForkJournal,
    evidence: EvidenceAdapter,
    analyze: EvidenceAdapter,
    checkpointer: Any,
) -> Any:
    try:
        from langgraph.graph import END, START, StateGraph
    except ImportError as error:
        raise ForkError("LangGraph is required for this experiment") from error
    graph = StateGraph(ForkState)

    def gather(state: ForkState) -> dict[str, str]:
        receipt_id, _ = journal.receipt(
            state["run_id"],
            "shared-evidence",
            "research question",
            evidence,
            reusable=True,
        )
        return {"evidence_receipt_id": receipt_id}

    def finish(state: ForkState) -> dict[str, str]:
        source = journal.output(state["evidence_receipt_id"])
        receipt_id, _ = journal.receipt(
            state["run_id"],
            f"analysis:{state['run_id']}",
            f"{source}:{state['hypothesis']}",
            analyze,
            reusable=False,
        )
        return {"result_receipt_id": receipt_id}

    graph.add_node("gather", gather)
    graph.add_node("finish", finish)
    graph.add_edge(START, "gather")
    graph.add_edge("gather", "finish")
    graph.add_edge("finish", END)
    return graph.compile(checkpointer=checkpointer)


class ImperativeForkRuntime:
    def __init__(
        self, journal: ForkJournal, evidence: EvidenceAdapter, analyze: EvidenceAdapter
    ):
        self.journal, self.evidence, self.analyze = journal, evidence, analyze

    @staticmethod
    def save_checkpoint(path: Path, state: ForkState) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, sort_keys=True, separators=(",", ":")))

    @staticmethod
    def load_checkpoint(path: Path) -> ForkState:
        return cast(ForkState, json.loads(path.read_text()))

    def start(
        self, run_id: str, scope_id: str, policy: ForkPolicy, hypothesis: str
    ) -> ForkState:
        self.journal.admit(run_id, scope_id, policy)
        receipt_id, _ = self.journal.receipt(
            run_id, "shared-evidence", "research question", self.evidence, reusable=True
        )
        return ForkState(
            run_id=run_id,
            scope_id=scope_id,
            fingerprint=policy.fingerprint,
            hypothesis=hypothesis,
            evidence_receipt_id=receipt_id,
            result_receipt_id="",
        )

    def finish(self, state: ForkState) -> ForkState:
        source = self.journal.output(state["evidence_receipt_id"])
        receipt_id, _ = self.journal.receipt(
            state["run_id"],
            f"analysis:{state['run_id']}",
            f"{source}:{state['hypothesis']}",
            self.analyze,
            reusable=False,
        )
        return ForkState(**{**state, "result_receipt_id": receipt_id})

    def fork(
        self, parent: ForkState, child_run_id: str, policy: ForkPolicy, hypothesis: str
    ) -> ForkState:
        self.journal.fork(parent["run_id"], child_run_id, parent["scope_id"], policy)
        return ForkState(
            **{
                **parent,
                "run_id": child_run_id,
                "hypothesis": hypothesis,
                "result_receipt_id": "",
            }
        )
