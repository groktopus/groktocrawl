"""Only executed and eligible reports reach the ephemeral publication boundary."""

import asyncio
import contextlib
from datetime import UTC, datetime

import pytest
from agent.experimental.canonical import admit_canonical_json
from agent.experimental.checked_knowledge import CHECKED_SCHEMA
from agent.experimental.knowledge_execution import ExecutionDecision
from agent.experimental.publication_gate import prepare_publication
from agent.experimental.render_execution import RenderExecutionLedger
from agent.experimental.render_manifest import RenderAuditInput

from tests.unit.test_checked_knowledge import REVIEWER, encode
from tests.unit.test_knowledge_context import Resolver as SourceResolver
from tests.unit.test_knowledge_context import context_payload  # noqa: F401
from tests.unit.test_knowledge_execution import executed_knowledge, ledger
from tests.unit.test_render_manifest import (  # noqa: F401
    BODY_BYTES,
    Resolver,
    audits,
    payload,
)


async def auditor(inspection):
    assert inspection.outputs == (BODY_BYTES,) * 3
    assert (
        inspection.knowledge.context.revision_id
        == inspection.checked_input.manifest_core.revision_id
    )
    return ExecutionDecision(
        outcome="pass", reason="Executed authored fixture; no quality claim"
    )


def render_ledger(callback=auditor):
    return RenderExecutionLedger(
        ((REVIEWER, callback),), clock=lambda: datetime(2026, 9, 10, tzinfo=UTC)
    )


def target(value):
    return {
        k: value[k]
        for k in ("scope_id", "research_id", "revision_id", "artifact_set_id")
    }


async def execute_render(value, resolver, owner):
    checked = RenderAuditInput.model_validate_json(encode(value["audit_inputs"][0]))
    result = await owner.execute(checked, **target(value), resolver=resolver)
    value["audits"] = [result.model_dump(mode="json")]


@pytest.fixture
async def ready(request):
    value, knowledge = request.getfixturevalue("payload")
    knowledge_owner = ledger()
    knowledge = (await executed_knowledge(knowledge_owner, knowledge)).model_dump(
        mode="json"
    )
    value["revision_digest"] = admit_canonical_json(
        encode(knowledge), schema_version=CHECKED_SCHEMA
    ).digest
    audits(value)
    resolver = Resolver(knowledge)
    render_owner = render_ledger()
    await execute_render(value, resolver, render_owner)
    return value, resolver, knowledge_owner, render_owner


async def prepare(ready, **overrides):
    value, resolver, knowledge_owner, render_owner = ready
    args = {
        "prior": (),
        "resolver": resolver,
        "source_resolver": SourceResolver(),
        "reviewers": (REVIEWER,),
        "knowledge_execution": knowledge_owner,
        "render_execution": render_owner,
    }
    args.update(overrides)
    return await prepare_publication(encode(value), **target(value), **args)


@pytest.mark.asyncio
async def test_executed_three_layer_candidate_is_explicitly_fixture_only(ready):
    result = await prepare(ready)
    assert result.fixture_only is True
    assert len(result.admitted.manifest.artifacts) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["reason", "verdict", "checked_at", "audit_id"])
async def test_forged_or_rewritten_audit_cannot_authorize_publication(ready, field):
    value = ready[0]
    value["audits"][0][field] = {
        "reason": "Forged",
        "verdict": "indeterminate",
        "checked_at": "2026-09-11T00:00:00Z",
        "audit_id": "forged",
    }[field]
    with pytest.raises(ValueError):
        await prepare(ready)


@pytest.mark.asyncio
async def test_fresh_owner_cannot_accept_another_owners_results(ready):
    with pytest.raises(ValueError, match="not returned"):
        await prepare(ready, render_execution=render_ledger())
    with pytest.raises(ValueError, match="not returned"):
        await prepare(ready, knowledge_execution=ledger())


@pytest.mark.asyncio
async def test_source_denial_prevents_candidate(ready):
    source = SourceResolver()
    source.error = PermissionError("source revoked")
    with pytest.raises(PermissionError):
        await prepare(ready, source_resolver=source)


@pytest.mark.asyncio
async def test_owner_closed_during_source_validation_prevents_candidate(ready):
    class ClosingSource(SourceResolver):
        async def resolve(self, reference):
            ready[3].close()
            return await super().resolve(reference)

    with pytest.raises(ValueError, match="closed"):
        await prepare(ready, source_resolver=ClosingSource())


@pytest.mark.asyncio
async def test_suppressed_source_cancellation_cannot_issue_candidate(ready):
    entered = asyncio.Event()

    class SlowSource(SourceResolver):
        async def resolve(self, reference):
            entered.set()
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.Event().wait()
            return await super().resolve(reference)

    task = asyncio.create_task(prepare(ready, source_resolver=SlowSource()))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["fail", "indeterminate", "supported"])
async def test_executed_nonpassing_render_audit_does_not_publish(ready, outcome):
    async def reject(_inspection):
        return ExecutionDecision(outcome=outcome, reason="Negative fixture")

    owner = render_ledger(reject)
    if outcome == "supported":
        with pytest.raises(ValueError):
            await execute_render(ready[0], ready[1], owner)
    else:
        await execute_render(ready[0], ready[1], owner)
        with pytest.raises(ValueError, match="audits to pass"):
            await prepare(ready, render_execution=owner)


@pytest.mark.asyncio
async def test_render_cancellation_cannot_be_erased_by_child(ready):
    started = asyncio.Event()

    async def stubborn(inspection):
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            asyncio.current_task().uncancel()
        return await auditor(inspection)

    owner = render_ledger(stubborn)
    task = asyncio.create_task(execute_render(ready[0], ready[1], owner))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    with pytest.raises(ValueError, match="not returned"):
        await prepare(ready, render_execution=owner)


@pytest.mark.asyncio
async def test_render_capacity_reserved_before_resolver_io(ready):
    entered, release = asyncio.Event(), asyncio.Event()

    async def waiting(inspection):
        entered.set()
        await release.wait()
        return await auditor(inspection)

    owner = render_ledger(waiting)
    task = asyncio.create_task(execute_render(ready[0], ready[1], owner))
    await entered.wait()
    with pytest.raises(ValueError, match="capacity"):
        await execute_render(ready[0], ready[1], owner)
    owner.close()
    release.set()
    with pytest.raises(ValueError, match="closed"):
        await task


@pytest.mark.asyncio
async def test_oversized_audit_result_has_no_receipt(ready):
    async def oversized(_inspection):
        return ExecutionDecision(outcome="pass", reason="🧪" * 5000)

    owner = render_ledger(oversized)
    with pytest.raises(ValueError, match="16 KiB"):
        await execute_render(ready[0], ready[1], owner)
    with pytest.raises(ValueError, match="not returned"):
        await prepare(ready, render_execution=owner)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "check_type", ["structural", "conflict_coverage", "semantic_support", "freshness"]
)
async def test_removing_required_check_cannot_publish_remaining_valid_receipts(
    ready, check_type
):
    import json

    from tests.unit.test_checked_knowledge import declarations

    value, resolver, knowledge_owner, _ = ready
    knowledge = json.loads(resolver.knowledge)
    removed = {
        i["input_id"]
        for i in knowledge["verification_inputs"]
        if i["check_type"] == check_type
    }
    knowledge["verification_inputs"] = [
        i for i in knowledge["verification_inputs"] if i["input_id"] not in removed
    ]
    knowledge["verifications"] = [
        r for r in knowledge["verifications"] if r["input_id"] not in removed
    ]
    declarations(knowledge)
    value["revision_digest"] = admit_canonical_json(
        encode(knowledge), schema_version=CHECKED_SCHEMA
    ).digest
    audits(value)
    resolver = Resolver(knowledge)
    owner = render_ledger()
    await execute_render(value, resolver, owner)
    with pytest.raises(ValueError, match=r"requires passing|ineligible"):
        await prepare((value, resolver, knowledge_owner, owner))


@pytest.mark.asyncio
async def test_actual_negative_knowledge_result_blocks_passing_render_audit(request):
    from tests.unit.test_knowledge_execution import fixture_executor

    value, knowledge = request.getfixturevalue("payload")

    async def negative(checked):
        if checked.check_type == "semantic_support":
            return ExecutionDecision(
                outcome="indeterminate", reason="Evidence does not settle this"
            )
        return await fixture_executor(checked)

    knowledge_owner = ledger(negative)
    knowledge = (await executed_knowledge(knowledge_owner, knowledge)).model_dump(
        mode="json"
    )
    value["revision_digest"] = admit_canonical_json(
        encode(knowledge), schema_version=CHECKED_SCHEMA
    ).digest
    audits(value)
    resolver, owner = Resolver(knowledge), render_ledger()
    await execute_render(value, resolver, owner)
    with pytest.raises(ValueError, match="ineligible"):
        await prepare((value, resolver, knowledge_owner, owner))


@pytest.mark.asyncio
async def test_missing_checked_evidence_cannot_be_hidden_by_passing_audit(ready):
    value, resolver, _, _ = ready
    for artifact in value["artifacts"]:
        artifact["statements"][0]["evidence_ids"] = []
    audits(value)
    owner = render_ledger()
    await execute_render(value, resolver, owner)
    with pytest.raises(ValueError, match="support closure"):
        await prepare(ready, render_execution=owner)


@pytest.mark.asyncio
async def test_omitted_executed_audit_cannot_be_cherry_picked(ready):
    value, resolver, _, owner = ready
    extra = dict(value["audit_inputs"][0], input_id="omitted-audit-input")
    checked = RenderAuditInput.model_validate_json(encode(extra))
    await owner.execute(checked, **target(value), resolver=resolver)
    with pytest.raises(ValueError, match="omits issued audits"):
        await prepare(ready)


@pytest.mark.asyncio
async def test_omitted_executed_knowledge_check_cannot_be_cherry_picked(ready):
    import json

    from agent.experimental.knowledge_checks import KnowledgeCheckInput

    _value, resolver, owner, _ = ready
    knowledge = json.loads(resolver.knowledge)
    extra = dict(
        knowledge["verification_inputs"][0], input_id="omitted-knowledge-input"
    )
    await owner.execute(KnowledgeCheckInput.model_validate_json(encode(extra)))
    with pytest.raises(ValueError, match="omits issued checks"):
        await prepare(ready)
