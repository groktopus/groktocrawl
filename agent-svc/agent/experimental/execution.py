"""Single-event-loop, in-memory operation accounting; no dispatch or recovery.

Only the controller owns this object. Workers return receipts and never mutate
state directly. This component is not thread-safe, a lease, or an auth boundary.
"""

from typing import Annotated, Literal

from pydantic import Field

from .knowledge import Digest, Identity, Record

Units = Annotated[int, Field(strict=True, ge=0, le=2**53 - 1)]


class Budget(Record):
    searches: Units = 0
    sources: Units = 0
    tokens: Units = 0
    cost_microusd: Units = 0

    def plus(self, other: "Budget") -> "Budget":
        return Budget(
            **{
                key: value + other.model_dump()[key]
                for key, value in self.model_dump().items()
            }
        )

    def minus(self, other: "Budget") -> "Budget":
        return Budget(
            **{
                key: value - other.model_dump()[key]
                for key, value in self.model_dump().items()
            }
        )

    def fits(self, limit: "Budget") -> bool:
        return all(
            value <= limit.model_dump()[key] for key, value in self.model_dump().items()
        )


class Operation(Record):
    operation_id: Identity
    input_digest: Digest
    reserved: Budget
    state: Literal["pending", "completed"] = "pending"
    output_id: Identity | None = None
    actual: Budget | None = None


class ExecutionState(Record):
    run_id: Identity
    policy_version: Identity
    revision: Units = 0
    state: Literal["running", "cancelled", "completed", "failed"] = "running"
    limit: Budget
    max_operations: Annotated[int, Field(strict=True, ge=1, le=1000)]
    spent: Budget = Budget()
    reserved: Budget = Budget()
    operations: tuple[Operation, ...] = ()


class ExecutionLedger:
    """No await points: mutations belong to one event-loop controller owner."""

    def __init__(
        self, *, run_id: str, policy_version: str, limit: Budget, max_operations: int
    ) -> None:
        self._state = ExecutionState(
            run_id=run_id,
            policy_version=policy_version,
            limit=limit,
            max_operations=max_operations,
        )

    @property
    def state(self) -> ExecutionState:
        return self._state

    def _guard(self, expected_revision: int) -> None:
        if (
            type(expected_revision) is not int
            or expected_revision != self._state.revision
        ):
            raise ValueError("stale or invalid state revision")
        if self._state.state != "running":
            raise ValueError(f"run is {self._state.state}")

    def _commit(self, **changes: object) -> ExecutionState:
        # Revalidate instead of using model_copy's unvalidated update path.
        self._state = ExecutionState.model_validate(
            {
                **self._state.model_dump(),
                **changes,
                "revision": self._state.revision + 1,
            }
        )
        return self._state

    def reserve(
        self,
        *,
        operation_id: str,
        input_digest: str,
        budget: Budget,
        expected_revision: int,
    ) -> ExecutionState:
        candidate = Operation(
            operation_id=operation_id, input_digest=input_digest, reserved=budget
        )
        self._guard(expected_revision)
        previous = next(
            (
                item
                for item in self._state.operations
                if item.operation_id == operation_id
            ),
            None,
        )
        if previous is not None:
            if (
                previous.input_digest != input_digest
                or previous.reserved != candidate.reserved
            ):
                raise ValueError("operation identity conflicts with its reserved input")
            # A reservation receipt is not permission to dispatch again. The caller
            # must dispatch only IDs newly added by this transition.
            return self._state
        if len(self._state.operations) >= self._state.max_operations:
            raise ValueError("operation count exhausted")
        reserved = self._state.reserved.plus(candidate.reserved)
        if not self._state.spent.plus(reserved).fits(self._state.limit):
            raise ValueError("budget exhausted")
        return self._commit(
            reserved=reserved, operations=(*self._state.operations, candidate)
        )

    def complete(
        self,
        *,
        operation_id: str,
        input_digest: str,
        output_id: str,
        actual: Budget,
        expected_revision: int,
    ) -> ExecutionState:
        previous = next(
            (
                item
                for item in self._state.operations
                if item.operation_id == operation_id
            ),
            None,
        )
        if previous is None:
            raise ValueError("operation was not reserved")
        receipt = Operation(
            operation_id=operation_id,
            input_digest=input_digest,
            reserved=previous.reserved,
            state="completed",
            output_id=output_id,
            actual=actual,
        )
        if input_digest != previous.input_digest:
            raise ValueError("completion input differs from reserved input")
        if previous.state == "completed":
            if previous != receipt:
                raise ValueError("completion conflicts with recorded receipt")
            # Exact replay is a read: stale transport revisions do not cause a
            # second effect, including when cancellation happened after completion.
            return self._state
        self._guard(expected_revision)
        if not actual.fits(previous.reserved):
            raise ValueError(
                "usage exceeds reservation; retain reservation for reconciliation"
            )
        return self._commit(
            spent=self._state.spent.plus(actual),
            reserved=self._state.reserved.minus(previous.reserved),
            operations=tuple(
                receipt if item.operation_id == operation_id else item
                for item in self._state.operations
            ),
        )

    def cancel(self, *, expected_revision: int) -> ExecutionState:
        if (
            type(expected_revision) is not int
            or expected_revision != self._state.revision
        ):
            raise ValueError("stale or invalid state revision")
        if self._state.state != "running":
            return self._state
        # Pending work may already have incurred usage. Cancellation cannot refund
        # it on a guess; a future adapter/recovery protocol owns reconciliation.
        return self._commit(state="cancelled")

    def finish(
        self, *, outcome: Literal["completed", "failed"], expected_revision: int
    ) -> ExecutionState:
        """Seal accounting; a successful run cannot leave unsettled operations."""
        self._guard(expected_revision)
        if outcome not in {"completed", "failed"}:
            raise ValueError("invalid execution outcome")
        if outcome == "completed" and any(
            op.state == "pending" for op in self._state.operations
        ):
            raise ValueError("cannot complete with pending operations")
        return self._commit(state=outcome)
