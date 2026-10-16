"""Budget and stale-owner schedules for the in-memory fixture controller ledger."""

import json

import pytest
from agent.experimental.execution import Budget, ExecutionLedger
from pydantic import ValidationError

DIGEST = "a" * 64


def ledger(**budget):
    return ExecutionLedger(
        run_id="run1",
        policy_version="fixture/1",
        limit=Budget(**budget),
        max_operations=3,
    )


def reserve(owner, identity="op1", **budget):
    return owner.reserve(
        operation_id=identity,
        input_digest=DIGEST,
        budget=Budget(**budget),
        expected_revision=owner.state.revision,
    )


def complete(owner, identity="op1", **actual):
    return owner.complete(
        operation_id=identity,
        input_digest=DIGEST,
        output_id=f"out-{identity}",
        actual=Budget(**actual),
        expected_revision=owner.state.revision,
    )


@pytest.mark.parametrize(
    "dimension", ["searches", "sources", "tokens", "cost_microusd"]
)
def test_pending_plus_spent_enforces_each_dimension(dimension):
    owner = ledger(**{dimension: 3})
    reserve(owner, **{dimension: 2})
    with pytest.raises(ValueError, match="budget exhausted"):
        reserve(owner, "op2", **{dimension: 2})
    complete(owner, **{dimension: 1})
    reserve(owner, "op2", **{dimension: 2})
    assert getattr(owner.state.spent, dimension) == 1
    assert getattr(owner.state.reserved, dimension) == 2


def test_out_of_order_completions_keep_reserved_order():
    owner = ledger(tokens=10)
    reserve(owner, "first", tokens=5)
    reserve(owner, "second", tokens=5)
    complete(owner, "second", tokens=3)
    complete(owner, "first", tokens=4)
    assert [op.operation_id for op in owner.state.operations] == ["first", "second"]
    assert owner.state.spent.tokens == 7
    assert owner.state.reserved.tokens == 0


def test_duplicate_reservation_has_no_second_dispatch_transition():
    owner = ledger(tokens=5)
    first = reserve(owner, tokens=5)
    assert reserve(owner, tokens=5) is first
    assert len(first.operations) == 1
    with pytest.raises(ValueError, match="identity conflicts"):
        reserve(owner, tokens=4)


def test_duplicate_completion_is_idempotent_even_after_cancel(record_property):
    owner = ledger(tokens=5)
    reserve(owner, tokens=5)
    first = complete(owner, tokens=3)
    assert first.spent.tokens == 3
    assert first.reserved.tokens == 0
    assert len(first.operations) == 1
    assert first.operations[0].state == "completed"
    assert complete(owner, tokens=3) is first
    terminal = owner.cancel(expected_revision=owner.state.revision)
    assert (
        owner.complete(
            operation_id="op1",
            input_digest=DIGEST,
            output_id="out-op1",
            actual=Budget(tokens=3),
            expected_revision=0,
        )
        is terminal
    )
    with pytest.raises(ValueError, match="conflicts"):
        complete(owner, tokens=2)
    assert owner.state is terminal
    assert terminal.spent == first.spent
    assert terminal.operations == first.operations
    record_property(
        "receipt_replay_trace",
        json.dumps(
            {
                "scope": "in_memory_accounting_only",
                "control": first.model_dump(mode="json"),
                "exact_replay_while_running": "same_state_object",
                "after_cancel": terminal.model_dump(mode="json"),
                "exact_replay_after_cancel": "same_state_object",
                "conflicting_replay": "rejected_without_state_change",
            }
        ),
    )


@pytest.mark.parametrize("operation", ["reserve", "complete", "cancel"])
def test_stale_mutation_has_no_effect(operation):
    owner = ledger(tokens=5)
    reserve(owner, tokens=5)
    before = owner.state
    with pytest.raises(ValueError, match="stale"):
        if operation == "reserve":
            owner.reserve(
                operation_id="op2",
                input_digest=DIGEST,
                budget=Budget(),
                expected_revision=0,
            )
        elif operation == "complete":
            owner.complete(
                operation_id="op1",
                input_digest=DIGEST,
                output_id="out",
                actual=Budget(),
                expected_revision=0,
            )
        else:
            owner.cancel(expected_revision=0)
    assert owner.state is before


def test_cancel_keeps_uncertain_usage_and_blocks_late_work():
    owner = ledger(tokens=5)
    reserve(owner, tokens=5)
    terminal = owner.cancel(expected_revision=1)
    assert terminal.reserved.tokens == 5
    with pytest.raises(ValueError, match="cancelled"):
        reserve(owner, "op2")
    with pytest.raises(ValueError, match="cancelled"):
        complete(owner, tokens=3)
    assert owner.cancel(expected_revision=terminal.revision) is terminal


def test_overrun_keeps_pending_reservation():
    owner = ledger(tokens=5)
    reserve(owner, tokens=5)
    before = owner.state
    with pytest.raises(ValueError, match="exceeds"):
        complete(owner, tokens=6)
    assert owner.state is before
    assert owner.state.operations[0].state == "pending"


def test_count_limit_bounds_even_zero_cost_operations():
    owner = ledger()
    for index in range(3):
        reserve(owner, str(index))
        complete(owner, str(index))
    with pytest.raises(ValueError, match="count"):
        reserve(owner, "fourth")


def test_receipt_input_identity_and_unreserved_work():
    owner = ledger()
    with pytest.raises(ValueError, match="not reserved"):
        complete(owner)
    reserve(owner)
    with pytest.raises(ValueError, match="input"):
        owner.complete(
            operation_id="op1",
            input_digest="b" * 64,
            output_id="out",
            actual=Budget(),
            expected_revision=1,
        )


@pytest.mark.parametrize("invalid", [-1, True, "1", 1.5, 2**53])
def test_invalid_budget_rejected(invalid):
    with pytest.raises(ValidationError):
        Budget(tokens=invalid)


def test_deep_immutable_state_and_forged_budget_revalidation():
    owner = ledger(tokens=5)
    reserve(owner, tokens=5)
    with pytest.raises(ValidationError):
        owner.state.operations[0].reserved.tokens = 100
    forged = Budget().model_copy(update={"tokens": -1})
    with pytest.raises(ValidationError):
        owner.reserve(
            operation_id="bad", input_digest=DIGEST, budget=forged, expected_revision=1
        )


def test_completion_seals_ledger_and_requires_settled_work():
    owner = ledger(tokens=2)
    reserve(owner, tokens=2)
    with pytest.raises(ValueError, match="pending"):
        owner.finish(outcome="completed", expected_revision=1)
    complete(owner, tokens=1)
    terminal = owner.finish(outcome="completed", expected_revision=2)
    assert terminal.state == "completed"
    assert owner.cancel(expected_revision=terminal.revision) is terminal
    with pytest.raises(ValueError, match="completed"):
        reserve(owner, "late")


def test_failure_retains_unknown_usage():
    owner = ledger(tokens=2)
    reserve(owner, tokens=2)
    result = owner.finish(outcome="failed", expected_revision=1)
    assert result.reserved.tokens == 2
