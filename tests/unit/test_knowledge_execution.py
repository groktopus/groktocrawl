"""A configured reviewer name cannot substitute for an actual callback result."""

import asyncio
from datetime import UTC, datetime

import pytest
from agent.experimental.checked_knowledge import CheckedKnowledge
from agent.experimental.knowledge_checks import CheckAssessment, KnowledgeCheckInput
from agent.experimental.knowledge_execution import (
    ExecutionDecision,
    KnowledgeExecutionLedger,
)

from tests.unit.test_checked_knowledge import REVIEWER, declarations, encode, record
from tests.unit.test_knowledge_context import context_payload  # noqa: F401


@pytest.fixture
def supplied(request):
    return record(request.getfixturevalue("context_payload")["context"])


def clock():
    return datetime(2026, 9, 8, tzinfo=UTC)


async def fixture_executor(checked):
    return ExecutionDecision(
        outcome="supported" if checked.check_type == "assessment" else "pass",
        reason="Authored local fixture",
    )


def ledger(executor=fixture_executor, **kwargs):
    return KnowledgeExecutionLedger(((REVIEWER, executor),), clock=clock, **kwargs)


def check_input(value, index=0):
    return KnowledgeCheckInput.model_validate_json(
        encode(value["verification_inputs"][index])
    )


async def executed_knowledge(owner, value):
    results = [
        await owner.execute(check_input(value, i))
        for i in range(len(value["verification_inputs"]))
    ]
    value["verifications"] = [
        r.model_dump(mode="json") for r in results if not isinstance(r, CheckAssessment)
    ]
    value["assessments"] = [
        r.model_dump(mode="json") for r in results if isinstance(r, CheckAssessment)
    ]
    value["assessment_links"][0]["assessment_ids"] = [
        value["assessments"][0]["assessment_id"]
    ]
    declarations(value)
    return CheckedKnowledge.model_validate_json(encode(value))


@pytest.mark.asyncio
async def test_only_exact_callback_results_bind_to_live_owner(supplied):
    owner = ledger()
    authored = CheckedKnowledge.model_validate_json(encode(supplied))
    with pytest.raises(ValueError, match="not returned"):
        owner.check_bindings(authored)
    actual = await executed_knowledge(owner, supplied)
    assert owner.check_bindings(actual) is True
    assert all(
        r.checked_at == "2026-09-08T00:00:00.000000Z" for r in actual.verifications
    )
    with pytest.raises(ValueError, match="not returned"):
        ledger().check_bindings(actual)
    owner.close()
    with pytest.raises(ValueError, match="closed"):
        owner.check_bindings(actual)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field", ["reason", "checked_at", "verdict", "verification_id"]
)
async def test_tampering_with_consistent_records_does_not_forge_execution(
    supplied, field
):
    owner = ledger()
    actual = (await executed_knowledge(owner, supplied)).model_dump(mode="json")
    values = {
        "reason": "Rewritten",
        "checked_at": "2026-09-09T00:00:00Z",
        "verdict": "indeterminate",
        "verification_id": "forged-id",
    }
    actual["verifications"][0][field] = values[field]
    declarations(actual)
    changed = CheckedKnowledge.model_validate_json(encode(actual))
    with pytest.raises(ValueError, match="not returned"):
        owner.check_bindings(changed)


@pytest.mark.asyncio
async def test_input_identity_and_operation_budget_are_reserved_before_dispatch(
    supplied,
):
    calls = []

    async def executor(checked):
        calls.append(checked.input_id)
        raise RuntimeError("failed callback")

    owner = ledger(executor, max_operations=1)
    with pytest.raises(RuntimeError):
        await owner.execute(check_input(supplied))
    with pytest.raises(ValueError, match="already been issued"):
        await owner.execute(check_input(supplied))
    with pytest.raises(ValueError, match="capacity"):
        await owner.execute(check_input(supplied, 1))
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_inflight_limit_and_owner_close_reject_late_results(supplied):
    started, release = asyncio.Event(), asyncio.Event()

    async def executor(checked):
        started.set()
        await release.wait()
        return await fixture_executor(checked)

    owner = ledger(executor, max_inflight=1)
    task = asyncio.create_task(owner.execute(check_input(supplied)))
    await started.wait()
    with pytest.raises(ValueError, match="capacity"):
        await owner.execute(check_input(supplied, 1))
    owner.close()
    release.set()
    with pytest.raises(ValueError, match="closed"):
        await task


@pytest.mark.asyncio
async def test_callback_cannot_uncancel_the_owner_and_record_a_late_pass(supplied):
    started = asyncio.Event()

    async def executor(checked):
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            asyncio.current_task().uncancel()
        return await fixture_executor(checked)

    owner = ledger(executor)
    task = asyncio.create_task(owner.execute(check_input(supplied)))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    with pytest.raises(ValueError, match="not returned"):
        owner.check_bindings(CheckedKnowledge.model_validate_json(encode(supplied)))


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["supported", "fail", "indeterminate"])
async def test_callback_outcome_kind_is_checked_and_negative_results_preserved(
    supplied, outcome
):
    async def executor(_checked):
        return ExecutionDecision(outcome=outcome, reason="Fixture decision")

    owner = ledger(executor)
    if outcome == "supported":
        with pytest.raises(ValueError):
            await owner.execute(check_input(supplied))
    else:
        result = await owner.execute(check_input(supplied))
        assert result.verdict == outcome


@pytest.mark.asyncio
async def test_reserved_result_bytes_are_enforced_without_truncation(supplied):
    async def executor(_checked):
        return ExecutionDecision(outcome="pass", reason="🧪" * 1000)

    owner = ledger(executor, max_result_bytes=1024)
    with pytest.raises(ValueError, match="byte limit"):
        await owner.execute(check_input(supplied))
    with pytest.raises(ValueError, match="not returned"):
        owner.check_bindings(CheckedKnowledge.model_validate_json(encode(supplied)))


@pytest.mark.parametrize(
    "kwargs", [{"max_operations": False}, {"max_inflight": 9}, {"max_result_bytes": 0}]
)
def test_invalid_ledger_limits_fail(kwargs):
    with pytest.raises(ValueError):
        ledger(**kwargs)


@pytest.mark.asyncio
async def test_reviewer_substitution_does_not_dispatch(supplied):
    calls = []

    async def executor(checked):
        calls.append(checked)
        return await fixture_executor(checked)

    supplied["verification_inputs"][0]["reviewer"]["version"] = "2"
    with pytest.raises(ValueError, match="not configured"):
        await ledger(executor).execute(check_input(supplied))
    assert calls == []


@pytest.mark.asyncio
async def test_byte_reservations_block_dispatch_and_release_after_failure(supplied):
    started, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def executor(checked):
        calls.append(checked.input_id)
        if len(calls) == 1:
            started.set()
            await release.wait()
            raise RuntimeError("fixture failure")
        return await fixture_executor(checked)

    owner = ledger(executor, max_result_bytes=1_048_576)
    task = asyncio.create_task(owner.execute(check_input(supplied)))
    await started.wait()
    with pytest.raises(ValueError, match="capacity"):
        await owner.execute(check_input(supplied, 1))
    assert len(calls) == 1
    release.set()
    with pytest.raises(RuntimeError):
        await task
    await owner.execute(check_input(supplied, 1))
    # A retained result also consumes the shared result-byte budget.
    with pytest.raises(ValueError, match="capacity"):
        await owner.execute(check_input(supplied, 2))
    assert len(calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "now", [datetime(2026, 9, 8), datetime(2020, 1, 1, tzinfo=UTC)]
)
async def test_untrusted_clock_values_cannot_timestamp_results(supplied, now):
    owner = KnowledgeExecutionLedger(((REVIEWER, fixture_executor),), clock=lambda: now)
    with pytest.raises(ValueError, match="clock"):
        await owner.execute(check_input(supplied))


@pytest.mark.asyncio
async def test_generated_identity_collision_cannot_overwrite_receipt(
    supplied, monkeypatch
):
    monkeypatch.setattr(
        "agent.experimental.knowledge_execution.uuid4", lambda: "same-id"
    )
    owner = ledger()
    first = await owner.execute(check_input(supplied))
    with pytest.raises(ValueError, match="collision"):
        await owner.execute(check_input(supplied, 1))
    assert first.verification_id == "same-id"
