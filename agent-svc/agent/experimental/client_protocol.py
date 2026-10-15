"""Application-owned protocol contracts for verified research clients."""

from collections.abc import Iterable
from typing import Any, Literal

from pydantic import Field, model_validator

from .knowledge import Identity, Record

ProtocolState = Literal[
    "accepted", "running", "cancel_requested", "completed", "failed", "cancelled"
]
TerminalEvent = Literal["done", "error", "cancelled"]


class ProtocolEvent(Record):
    protocol_version: Literal["research/1"]
    run_id: Identity
    sequence: int = Field(strict=True, ge=1)
    event: Literal["accepted", "progress", "done", "error", "cancelled"]
    state: ProtocolState
    stage: str | None = None
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None

    @property
    def event_id(self) -> str:
        return f"{self.run_id}:{self.sequence}"

    @property
    def terminal(self) -> bool:
        return self.event in {"done", "error", "cancelled"}

    @model_validator(mode="after")
    def event_shape(self) -> "ProtocolEvent":
        if self.event in {"accepted", "progress"}:
            if self.state not in {"accepted", "running", "cancel_requested"}:
                raise ValueError("nonterminal event has terminal state")
            if self.result is not None or self.error is not None:
                raise ValueError("nonterminal event cannot expose terminal payload")
        elif self.event == "done":
            if self.state != "completed" or self.result is None or self.error is not None:
                raise ValueError("done requires completed state and result")
        elif self.event == "error":
            if self.state != "failed" or self.result is not None or self.error is None:
                raise ValueError("error requires failed state and error")
        elif self.state != "cancelled" or self.result is not None or self.error is not None:
            raise ValueError("cancelled requires cancelled state and no payload")
        return self


def validate_trace(events: Iterable[ProtocolEvent]) -> tuple[ProtocolEvent, ...]:
    """Validate one canonical ordered trace with exactly one terminal event."""
    trace = tuple(events)
    if not trace:
        raise ValueError("trace must contain an accepted event")
    if trace[0].event != "accepted":
        raise ValueError("trace must begin with accepted")
    run_id = trace[0].run_id
    for expected, event in enumerate(trace, start=1):
        if event.run_id != run_id or event.sequence != expected:
            raise ValueError("trace identity or sequence is not contiguous")
        if expected > 1 and event.event == "accepted":
            raise ValueError("accepted may occur only once")
        if expected < len(trace) and event.terminal:
            raise ValueError("terminal event must be last")
    terminals = tuple(event for event in trace if event.terminal)
    if len(terminals) != 1:
        raise ValueError("trace must contain exactly one terminal event")
    return trace


def replay_after(
    events: Iterable[ProtocolEvent], last_event_id: str | None
) -> tuple[ProtocolEvent, ...]:
    """Return a deduplicated replay suffix, rejecting foreign cursors."""
    unique: dict[str, ProtocolEvent] = {}
    for event in events:
        previous = unique.get(event.event_id)
        if previous is not None and previous != event:
            raise ValueError("duplicate event conflicts with retained trace")
        unique[event.event_id] = event
    trace = validate_trace(tuple(unique.values()))
    if last_event_id is None:
        return trace
    if not last_event_id.startswith(f"{trace[0].run_id}:"):
        raise ValueError("cursor belongs to another run")
    try:
        cursor = int(last_event_id.rsplit(":", 1)[1])
    except ValueError as error:
        raise ValueError("cursor is invalid") from error
    if cursor < 0 or cursor > trace[-1].sequence:
        raise ValueError("cursor is outside the retained trace")
    return tuple(event for event in trace if event.sequence > cursor)


def client_projections(events: Iterable[ProtocolEvent]) -> dict[str, dict[str, Any]]:
    """Produce the status/CLI/MCP projection from one validated trace."""
    trace = validate_trace(events)
    terminal = next(event for event in trace if event.terminal)
    projection = {
        "protocol_version": terminal.protocol_version,
        "run_id": terminal.run_id,
        "state": terminal.state,
        "event": terminal.event,
        "result": terminal.result,
        "error": terminal.error,
    }
    return {name: dict(projection) for name in ("status", "cli", "mcp")}
