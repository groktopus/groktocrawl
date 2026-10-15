"""Bounded recovery ownership and fencing contract.

This module models the authority rules that a durable implementation must
preserve. It is an in-memory contract fixture, not a lease service, scheduler,
checkpoint store or recovery proof.
"""

from dataclasses import dataclass, field
from typing import Literal

from pydantic import Field, model_validator

from .knowledge import Identity, Record

Seconds = float
ProviderOutcome = Literal["confirmed", "absent", "unknown"]


class RecoveryContract(Record):
    schema_version: Literal["recovery-contract-prototype/1"]
    execution_owner: Identity
    authoritative_store: Identity
    lease_seconds: Seconds = Field(gt=0, le=86_400)
    reclaim_delay_seconds: Seconds = Field(gt=0, le=86_400)
    heartbeat_seconds: Seconds = Field(gt=0, le=3_600)
    max_run_age_seconds: Seconds = Field(gt=0, le=7_776_000)
    retry_window_seconds: Seconds = Field(gt=0, le=7_776_000)
    provider_timeout_seconds: Seconds = Field(gt=0, le=86_400)
    retry_owner: Identity
    outbox_authority: Identity
    reconciliation_owner: Identity
    allows_exactly_once_network_effects: bool = False

    @model_validator(mode="after")
    def bounded_relationships(self) -> "RecoveryContract":
        if self.heartbeat_seconds >= self.lease_seconds:
            raise ValueError("heartbeat must be shorter than the lease")
        if self.reclaim_delay_seconds < self.heartbeat_seconds:
            raise ValueError("reclaim delay cannot precede heartbeat interval")
        if self.retry_window_seconds > self.max_run_age_seconds:
            raise ValueError("retry window cannot exceed maximum run age")
        if self.allows_exactly_once_network_effects:
            raise ValueError("network effects remain at-least-once or unknown")
        return self


@dataclass
class RecoveryOperation:
    logical_operation_id: str
    input_digest: str
    owner_generation: int = 1
    state: Literal[
        "admitted",
        "dispatch_intent",
        "running",
        "receipt",
        "outcome_unknown",
        "cancelled",
        "committed",
    ] = "admitted"
    attempt_id: str | None = None
    receipt_digest: str | None = None


@dataclass
class RecoveryLedger:
    """Deterministic authority fixture for stale-owner and race tests."""

    operations: dict[str, RecoveryOperation] = field(default_factory=dict)

    def admit(self, logical_operation_id: str, input_digest: str) -> RecoveryOperation:
        existing = self.operations.get(logical_operation_id)
        if existing is not None:
            if existing.input_digest != input_digest:
                raise ValueError("operation input conflicts with admitted identity")
            return existing
        operation = RecoveryOperation(logical_operation_id, input_digest)
        self.operations[logical_operation_id] = operation
        return operation

    def reclaim(self, logical_operation_id: str) -> int:
        operation = self._get(logical_operation_id)
        if operation.state in {"cancelled", "committed"}:
            raise ValueError("terminal operation cannot be reclaimed")
        operation.owner_generation += 1
        operation.state = "dispatch_intent"
        return operation.owner_generation

    def dispatch(self, logical_operation_id: str, generation: int, attempt_id: str) -> None:
        operation = self._owned(logical_operation_id, generation)
        if operation.state not in {"admitted", "dispatch_intent"}:
            raise ValueError("operation is not dispatchable")
        operation.attempt_id = attempt_id
        operation.state = "running"

    def receipt(self, logical_operation_id: str, generation: int, digest: str) -> str:
        operation = self._owned(logical_operation_id, generation)
        if operation.state in {"receipt", "outcome_unknown", "committed"}:
            if operation.receipt_digest != digest:
                raise ValueError("receipt conflicts with committed identity")
            return operation.state
        if operation.state != "running":
            raise ValueError("operation has no active attempt")
        operation.receipt_digest = digest
        operation.state = "receipt"
        return operation.state

    def mark_unknown(self, logical_operation_id: str, generation: int) -> None:
        operation = self._owned(logical_operation_id, generation)
        if operation.state != "running":
            raise ValueError("only a running attempt can become unknown")
        operation.state = "outcome_unknown"

    def reconcile_unknown(
        self,
        logical_operation_id: str,
        generation: int,
        outcome: ProviderOutcome,
        receipt_digest: str | None = None,
    ) -> str:
        """Resolve an ambiguous provider attempt without inventing success."""
        operation = self._owned(logical_operation_id, generation)
        if operation.state != "outcome_unknown":
            raise ValueError("only an unknown attempt can be reconciled")
        if outcome == "confirmed":
            if receipt_digest is None:
                raise ValueError("confirmed outcome requires a receipt digest")
            operation.receipt_digest = receipt_digest
            operation.state = "receipt"
        elif outcome == "absent":
            operation.attempt_id = None
            operation.state = "dispatch_intent"
        return operation.state

    def cancel(self, logical_operation_id: str, generation: int) -> str:
        operation = self._owned(logical_operation_id, generation)
        if operation.state == "committed":
            return operation.state
        if operation.state == "cancelled":
            return operation.state
        operation.state = "cancelled"
        return operation.state

    def publish(self, logical_operation_id: str, generation: int, digest: str) -> None:
        operation = self._owned(logical_operation_id, generation)
        if operation.state == "committed":
            if operation.receipt_digest != digest:
                raise ValueError("publication receipt conflicts")
            return
        if operation.state != "receipt" or operation.receipt_digest != digest:
            raise ValueError("publication requires the exact receipt")
        operation.state = "committed"

    def _get(self, logical_operation_id: str) -> RecoveryOperation:
        try:
            return self.operations[logical_operation_id]
        except KeyError as error:
            raise ValueError("operation is unknown") from error

    def _owned(self, logical_operation_id: str, generation: int) -> RecoveryOperation:
        operation = self._get(logical_operation_id)
        if generation != operation.owner_generation:
            raise ValueError("stale owner generation")
        return operation
