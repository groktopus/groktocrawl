"""Future-capability fixture for durable, human-guided research resume.

The application journal owns durable operation and guidance receipts. LangGraph
checkpoints contain only control position and immutable receipt identifiers.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol, TypedDict

from .durable_research import DurableResearchLedger


class GuidanceError(RuntimeError):
    """A guidance transition failed closed."""


@dataclass(frozen=True)
class OwnershipToken:
    run_id: str
    scope_id: str
    owner_id: str
    generation: int


class GuidanceOwner(Protocol):
    def claim(self, run_id: str, scope_id: str, owner_id: str) -> OwnershipToken: ...

    def checkpoint(
        self, token: OwnershipToken, name: str, checkpoint_digest: str
    ) -> None: ...

    def complete(self, token: OwnershipToken, result_digest: str) -> None: ...


class DurableResearchGuidanceOwner:
    """Use the accepted W5 ledger for claims, fencing, and terminal state."""

    def __init__(self, ledger: DurableResearchLedger):
        self.ledger = ledger

    def claim(self, run_id: str, scope_id: str, owner_id: str) -> OwnershipToken:
        before = self.ledger.get(run_id)
        if before is None or before.scope_id != scope_id:
            raise GuidanceError("run is missing or belongs to another scope")
        claimed = self.ledger.claim(run_id, owner_id)
        return OwnershipToken(
            run_id, scope_id, owner_id, claimed.owner_generation
        )

    def checkpoint(
        self, token: OwnershipToken, name: str, checkpoint_digest: str
    ) -> None:
        self.ledger.checkpoint(
            token.run_id,
            token.owner_id,
            token.generation,
            name,
            checkpoint_digest,
        )

    def complete(self, token: OwnershipToken, result_digest: str) -> None:
        self.ledger.commit_result(
            token.run_id, token.owner_id, token.generation, result_digest
        )


@dataclass(frozen=True)
class GuidanceReceipt:
    receipt_id: str
    kind: Literal["research", "guidance", "synthesis"]
    scope_id: str
    input_digest: str
    output: str


class GuidanceJournal:
    """Small file-backed application authority used only by this experiment."""

    schema_version = "guidance-journal/1"

    def __init__(self, path: Path):
        self.path = path
        if not path.exists():
            self._write({"schema_version": self.schema_version, "runs": {}})

    def _read(self) -> dict[str, Any]:
        value = json.loads(self.path.read_text())
        if value.get("schema_version") != self.schema_version:
            raise GuidanceError("unsupported guidance journal version")
        return value

    def _write(self, value: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        pending = self.path.with_suffix(self.path.suffix + ".pending")
        pending.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")))
        pending.replace(self.path)

    def initialize(self, run_id: str, scope_id: str) -> None:
        value = self._read()
        prior = value["runs"].get(run_id)
        if prior is not None:
            if prior["scope_id"] != scope_id:
                raise GuidanceError("run belongs to another scope")
            return
        value["runs"][run_id] = {"scope_id": scope_id, "receipts": {}}
        self._write(value)

    def record(
        self,
        run_id: str,
        scope_id: str,
        kind: Literal["research", "guidance", "synthesis"],
        operation_id: str,
        input_value: str,
        output: str,
    ) -> GuidanceReceipt:
        value = self._read()
        run = value["runs"].get(run_id)
        if run is None or run["scope_id"] != scope_id:
            raise GuidanceError("run is missing or belongs to another scope")
        input_digest = hashlib.sha256(input_value.encode()).hexdigest()
        receipt_id = f"{kind}:{operation_id}"
        receipt = GuidanceReceipt(
            receipt_id, kind, scope_id, input_digest, output
        )
        encoded = {
            "receipt_id": receipt.receipt_id,
            "kind": receipt.kind,
            "scope_id": receipt.scope_id,
            "input_digest": receipt.input_digest,
            "output": receipt.output,
        }
        prior = run["receipts"].get(receipt_id)
        if prior is not None and prior != encoded:
            raise GuidanceError("receipt identity conflicts with retained input")
        run["receipts"][receipt_id] = encoded
        self._write(value)
        return receipt

    def get(self, run_id: str, scope_id: str, receipt_id: str) -> GuidanceReceipt:
        value = self._read()
        run = value["runs"].get(run_id)
        if run is None or run["scope_id"] != scope_id:
            raise GuidanceError("run is missing or belongs to another scope")
        raw = run["receipts"].get(receipt_id)
        if raw is None:
            raise GuidanceError("receipt is missing")
        return GuidanceReceipt(**raw)


class GuidanceState(TypedDict, total=False):
    run_id: str
    scope_id: str
    state_version: str
    research_receipt_id: str
    guidance_receipt_id: str
    synthesis_receipt_id: str


class ResearchAdapter(Protocol):
    def __call__(self, question: str) -> str: ...


class SynthesisAdapter(Protocol):
    def __call__(self, evidence: str, guidance: str) -> str: ...


def checkpoint_digest(state: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(state, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def build_guidance_graph(
    journal: GuidanceJournal,
    research: ResearchAdapter,
    synthesize: SynthesisAdapter,
    checkpointer: Any,
) -> Any:
    """Compile a real LangGraph interrupt workflow over application receipts."""
    try:
        from langgraph.graph import END, START, StateGraph
        from langgraph.types import interrupt
    except ImportError as error:
        raise GuidanceError("LangGraph is required for this experiment") from error

    graph = StateGraph(GuidanceState)

    def gather(state: GuidanceState) -> dict[str, str]:
        receipt_id = state.get("research_receipt_id")
        if receipt_id:
            journal.get(state["run_id"], state["scope_id"], receipt_id)
            return {}
        output = research("research question")
        receipt = journal.record(
            state["run_id"],
            state["scope_id"],
            "research",
            "initial",
            "research question",
            output,
        )
        return {"research_receipt_id": receipt.receipt_id}

    def ask(state: GuidanceState) -> dict[str, str]:
        guidance_receipt_id = interrupt(
            {
                "question": "Which concern should the research prioritize?",
                "accepts": "an application guidance receipt ID",
            }
        )
        receipt = journal.get(
            state["run_id"], state["scope_id"], str(guidance_receipt_id)
        )
        if receipt.kind != "guidance":
            raise GuidanceError("resume input is not a guidance receipt")
        return {"guidance_receipt_id": receipt.receipt_id}

    def finish(state: GuidanceState) -> dict[str, str]:
        evidence = journal.get(
            state["run_id"], state["scope_id"], state["research_receipt_id"]
        )
        guidance = journal.get(
            state["run_id"], state["scope_id"], state["guidance_receipt_id"]
        )
        output = synthesize(evidence.output, guidance.output)
        receipt = journal.record(
            state["run_id"],
            state["scope_id"],
            "synthesis",
            "final",
            f"{evidence.receipt_id}:{guidance.receipt_id}",
            output,
        )
        return {"synthesis_receipt_id": receipt.receipt_id}

    graph.add_node("gather", gather)
    graph.add_node("ask", ask)
    graph.add_node("finish", finish)
    graph.add_edge(START, "gather")
    graph.add_edge("gather", "ask")
    graph.add_edge("ask", "finish")
    graph.add_edge("finish", END)
    return graph.compile(checkpointer=checkpointer)


@dataclass(frozen=True)
class GuidancePause:
    token: OwnershipToken
    question: str
    checkpoint_digest: str


class LangGraphGuidanceRuntime:
    """Coordinate graph control state through the W5 ownership boundary."""

    state_version = "human-guidance/1"

    def __init__(self, graph: Any, journal: GuidanceJournal, owner: GuidanceOwner):
        self.graph = graph
        self.journal = journal
        self.owner = owner

    @staticmethod
    def _config(run_id: str) -> dict[str, dict[str, str]]:
        return {"configurable": {"thread_id": run_id}}

    def start(self, run_id: str, scope_id: str, owner_id: str) -> GuidancePause:
        self.journal.initialize(run_id, scope_id)
        token = self.owner.claim(run_id, scope_id, owner_id)
        result = self.graph.invoke(
            {
                "run_id": run_id,
                "scope_id": scope_id,
                "state_version": self.state_version,
            },
            self._config(run_id),
        )
        interrupts = result.get("__interrupt__", ())
        if len(interrupts) != 1:
            raise GuidanceError("graph did not stop at the guidance boundary")
        values = dict(self.graph.get_state(self._config(run_id)).values)
        digest = checkpoint_digest(values)
        self.owner.checkpoint(token, "awaiting_guidance", digest)
        return GuidancePause(token, str(interrupts[0].value["question"]), digest)

    def resume(
        self,
        run_id: str,
        scope_id: str,
        owner_id: str,
        guidance_receipt_id: str,
    ) -> GuidanceReceipt:
        try:
            from langgraph.types import Command
        except ImportError as error:
            raise GuidanceError("LangGraph is required for this experiment") from error
        guidance = self.journal.get(run_id, scope_id, guidance_receipt_id)
        if guidance.kind != "guidance":
            raise GuidanceError("resume input is not a guidance receipt")
        saved = dict(self.graph.get_state(self._config(run_id)).values)
        if saved.get("state_version") != self.state_version:
            raise GuidanceError("unsupported guidance state version")
        if saved.get("run_id") != run_id or saved.get("scope_id") != scope_id:
            raise GuidanceError("checkpoint identity or scope differs from resume")
        token = self.owner.claim(run_id, scope_id, owner_id)
        result = self.graph.invoke(
            Command(resume=guidance_receipt_id), self._config(run_id)
        )
        receipt_id = result.get("synthesis_receipt_id")
        if not receipt_id:
            raise GuidanceError("graph did not complete after guidance")
        receipt = self.journal.get(run_id, scope_id, str(receipt_id))
        self.owner.complete(token, receipt.input_digest)
        return receipt

    def recover_after_guidance(
        self, run_id: str, scope_id: str, owner_id: str
    ) -> GuidanceReceipt:
        """Continue a checkpoint that durably passed the interrupt boundary."""
        saved = dict(self.graph.get_state(self._config(run_id)).values)
        if saved.get("state_version") != self.state_version:
            raise GuidanceError("unsupported guidance state version")
        if saved.get("run_id") != run_id or saved.get("scope_id") != scope_id:
            raise GuidanceError("checkpoint identity or scope differs from resume")
        guidance_receipt_id = saved.get("guidance_receipt_id")
        if not guidance_receipt_id:
            raise GuidanceError("checkpoint has not accepted guidance")
        guidance = self.journal.get(run_id, scope_id, str(guidance_receipt_id))
        if guidance.kind != "guidance":
            raise GuidanceError("checkpoint does not reference guidance")
        token = self.owner.claim(run_id, scope_id, owner_id)
        result = self.graph.invoke(None, self._config(run_id))
        receipt_id = result.get("synthesis_receipt_id")
        if not receipt_id:
            raise GuidanceError("graph did not complete during recovery")
        receipt = self.journal.get(run_id, scope_id, str(receipt_id))
        self.owner.complete(token, receipt.input_digest)
        return receipt


class ImperativeCheckpointStore:
    """File-backed control position for the non-framework reference arm."""

    schema_version = "imperative-guidance-checkpoint/1"

    def __init__(self, path: Path):
        self.path = path

    def save(self, state: GuidanceState) -> str:
        value = {"schema_version": self.schema_version, "state": dict(state)}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        pending = self.path.with_suffix(self.path.suffix + ".pending")
        pending.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")))
        pending.replace(self.path)
        return checkpoint_digest(dict(state))

    def load(self) -> GuidanceState:
        value = json.loads(self.path.read_text())
        if value.get("schema_version") != self.schema_version:
            raise GuidanceError("unsupported imperative checkpoint version")
        return GuidanceState(**value["state"])


class ImperativeGuidanceRuntime:
    """Explicit pause/resume reference using the same receipts and W5 owner."""

    state_version = LangGraphGuidanceRuntime.state_version
    question = "Which concern should the research prioritize?"

    def __init__(
        self,
        checkpoint: ImperativeCheckpointStore,
        journal: GuidanceJournal,
        owner: GuidanceOwner,
        research: ResearchAdapter,
        synthesize: SynthesisAdapter,
    ):
        self.checkpoint = checkpoint
        self.journal = journal
        self.owner = owner
        self.research = research
        self.synthesize = synthesize

    def start(self, run_id: str, scope_id: str, owner_id: str) -> GuidancePause:
        self.journal.initialize(run_id, scope_id)
        token = self.owner.claim(run_id, scope_id, owner_id)
        receipt = self.journal.record(
            run_id,
            scope_id,
            "research",
            "initial",
            "research question",
            self.research("research question"),
        )
        state = GuidanceState(
            run_id=run_id,
            scope_id=scope_id,
            state_version=self.state_version,
            research_receipt_id=receipt.receipt_id,
        )
        digest = self.checkpoint.save(state)
        self.owner.checkpoint(token, "awaiting_guidance", digest)
        return GuidancePause(token, self.question, digest)

    def resume(
        self,
        run_id: str,
        scope_id: str,
        owner_id: str,
        guidance_receipt_id: str,
    ) -> GuidanceReceipt:
        state = self.checkpoint.load()
        if state.get("state_version") != self.state_version:
            raise GuidanceError("unsupported guidance state version")
        if state.get("run_id") != run_id or state.get("scope_id") != scope_id:
            raise GuidanceError("checkpoint identity or scope differs from resume")
        evidence = self.journal.get(
            run_id, scope_id, state["research_receipt_id"]
        )
        guidance = self.journal.get(run_id, scope_id, guidance_receipt_id)
        if guidance.kind != "guidance":
            raise GuidanceError("resume input is not a guidance receipt")
        token = self.owner.claim(run_id, scope_id, owner_id)
        result = self.journal.record(
            run_id,
            scope_id,
            "synthesis",
            "final",
            f"{evidence.receipt_id}:{guidance.receipt_id}",
            self.synthesize(evidence.output, guidance.output),
        )
        self.owner.complete(token, result.input_digest)
        return result
