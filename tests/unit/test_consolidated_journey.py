"""Complete fixture journey preserves uncertainty and stops on denied evidence."""

import asyncio
import json
from dataclasses import replace

import pytest
from agent.experimental.canonical import admit_canonical_json
from agent.experimental.checked_knowledge import CHECKED_SCHEMA
from agent.experimental.consolidated_example import (
    BODIES,
    example_context,
    example_journey,
    example_renderer,
    example_verifier,
)
from agent.experimental.context_sources import ResolvedContextSource
from agent.experimental.knowledge import text_digest
from agent.experimental.knowledge_execution import ExecutionDecision
from agent.experimental.render_manifest import MANIFEST_SCHEMA, ManifestArtifact


def acquisitions(first=None):
    result = {}
    for snapshot, body in zip(example_context().snapshots, BODIES, strict=True):

        async def acquire(snapshot=snapshot, body=body):
            return ResolvedContextSource(
                snapshot.content_ref, body.encode(), "utf8-exact/1", "text/plain"
            )

        result[snapshot.snapshot_id] = acquire
    if first is not None:
        result["source-1"] = first
    return result


@pytest.mark.asyncio
async def test_full_journey_returns_three_distinct_fixture_reports_and_canonical_records():
    journey = example_journey()
    result = await journey.run()
    assert result.candidate.fixture_only is True
    assert result.candidate.admitted.knowledge.coverage == "partial"
    assert len(result.sources) == 2
    assert len({r.body for r in result.reports}) == 3
    for report in result.reports:
        assert b"EXPERIMENTAL FIXTURE" in report.body
        assert b"Coverage: partial" in report.body
        assert b"causation is unproven" in report.body
        assert b"remains unestablished" in report.body
    assert (
        admit_canonical_json(result.knowledge_bytes, schema_version=CHECKED_SCHEMA).data
        == result.knowledge_bytes
    )
    assert (
        admit_canonical_json(result.manifest_bytes, schema_version=MANIFEST_SCHEMA).data
        == result.manifest_bytes
    )
    with pytest.raises(ValueError, match="single use"):
        await journey.run()


@pytest.mark.asyncio
async def test_source_denial_prevents_verification_and_rendering():
    calls = []

    async def denied():
        raise PermissionError("fixture source revoked")

    async def verify(checked):
        calls.append("verify")
        return await example_verifier(checked)

    async def render(knowledge):
        calls.append("render")
        return await example_renderer(knowledge)

    with pytest.raises(PermissionError):
        await example_journey(
            acquisitions=acquisitions(denied), verify=verify, render=render
        ).run()
    assert calls == []


@pytest.mark.asyncio
async def test_changed_source_bytes_fail_before_any_judgment():
    source = await acquisitions()["source-1"]()

    async def changed():
        return replace(source, body=b"X" * len(source.body))

    calls = []

    async def verify(checked):
        calls.append(checked)
        return await example_verifier(checked)

    with pytest.raises(ValueError, match="pinned descriptor"):
        await example_journey(acquisitions=acquisitions(changed), verify=verify).run()
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind", ["semantic_support", "freshness", "structural", "conflict_coverage"]
)
async def test_negative_executed_knowledge_cannot_complete_journey(kind):
    async def verify(checked):
        if checked.check_type == kind:
            return ExecutionDecision(
                outcome="indeterminate", reason="Fixture uncertainty"
            )
        return await example_verifier(checked)

    with pytest.raises(ValueError, match=r"requires passing|ineligible"):
        await example_journey(verify=verify).run()


@pytest.mark.asyncio
async def test_rehashed_report_with_omitted_caveat_is_rejected_by_executed_auditor():
    async def render(knowledge):
        reports = list(await example_renderer(knowledge))
        first = reports[0]
        caveat = b"Coverage: partial. Enterprise-wide impact is unresolved."
        body = first.body.replace(caveat, b" " * len(caveat))
        # Preserve mapped offsets while changing text outside mappings.
        assert len(body) == len(first.body)
        artifact = first.artifact.model_dump(mode="json")
        artifact["content_digest"] = text_digest(body.decode())
        artifact = ManifestArtifact.model_validate_json(json.dumps(artifact))
        reports[0] = replace(first, artifact=artifact, body=body)
        return tuple(reports)

    with pytest.raises(ValueError, match="audits to pass"):
        await example_journey(render=render).run()


@pytest.mark.asyncio
async def test_renderer_cannot_return_incomplete_layers():
    async def render(knowledge):
        return (await example_renderer(knowledge))[:2]

    with pytest.raises(ValueError, match="three reports"):
        await example_journey(render=render).run()


@pytest.mark.asyncio
async def test_acquisition_cannot_uncancel_journey_owner():
    source = await acquisitions()["source-1"]()
    started = asyncio.Event()

    async def stubborn():
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            asyncio.current_task().uncancel()
        return source

    journey = example_journey(acquisitions=acquisitions(stubborn))
    task = asyncio.create_task(journey.run())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    with pytest.raises(ValueError, match="single use"):
        await journey.run()


@pytest.mark.asyncio
async def test_deadline_stops_waiting_acquisition():
    async def waiting():
        await asyncio.Event().wait()

    with pytest.raises(TimeoutError):
        await example_journey(
            acquisitions=acquisitions(waiting), timeout_seconds=1
        ).run()


@pytest.mark.parametrize("timeout", [0, 121, True])
def test_invalid_deadline_is_rejected_before_work(timeout):
    with pytest.raises(ValueError):
        example_journey(timeout_seconds=timeout)


@pytest.mark.asyncio
async def test_commit_hook_runs_with_live_owners_and_closes_them_afterward():
    observed = []

    async def commit(result, knowledge_owner, render_owner):
        assert knowledge_owner.check_bindings(result.candidate.admitted.knowledge)
        assert render_owner.check_bindings(result.candidate.admitted.manifest)
        observed.extend((knowledge_owner, render_owner))

    result = await example_journey(commit=commit).run()
    assert len(observed) == 2
    with pytest.raises(ValueError, match="closed"):
        observed[0].check_bindings(result.candidate.admitted.knowledge)
    with pytest.raises(ValueError, match="closed"):
        observed[1].check_bindings(result.candidate.admitted.manifest)


@pytest.mark.asyncio
async def test_commit_failure_propagates_without_retry():
    calls = []

    async def commit(_result, _knowledge, _render):
        calls.append(1)
        raise ConnectionError("unknown commit acknowledgement")

    with pytest.raises(ConnectionError):
        await example_journey(commit=commit).run()
    assert calls == [1]
