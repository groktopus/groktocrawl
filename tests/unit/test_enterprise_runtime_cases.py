"""Scoped catalog probes against the real fixture journey, not a second executor."""

import asyncio
import json
from pathlib import Path

import pytest
from agent.experimental.knowledge import text_digest
from agent.experimental.pipeline import AcquiredText, FixtureJourney, FixturePlan

from tests.unit.test_fixture_pipeline import fixture_plan
from tests.unit.test_knowledge_structure import payload  # noqa: F401

CATALOG = Path(__file__).parents[2] / (
    "docs/experiments/enterprise-evaluation/runtime-contract-plan.json"
)


def record_trace(record_property, case_id, events, result):
    catalog = json.loads(CATALOG.read_text())
    assert case_id in {case["id"] for case in catalog["cases"]}
    assert events[-1]["event"] == "terminal"
    record_property(
        "runtime_contract_trace",
        json.dumps(
            {
                "case_id": case_id,
                "scope": "local_fixture_no_external_effects",
                "events": events,
                "accounting": result.accounting.model_dump(mode="json"),
                "publication_present": result.publication is not None,
            }
        ),
    )


@pytest.mark.parametrize("adverse", [False, True], ids=["control", "adverse"])
async def test_catalog_cancellation(request, record_property, adverse):
    plan, responses = fixture_plan(request.getfixturevalue("payload"))
    started, release, returned = asyncio.Event(), asyncio.Event(), asyncio.Event()
    events = []
    loop = asyncio.get_running_loop()

    def event(name):
        events.append({"event": name, "monotonic_timestamp": loop.time()})

    async def acquire():
        event("dispatch")
        started.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            # Even a callback returning a valid late result must not publish.
            event("callback_cancelled")
        event("callback_returned")
        returned.set()
        return responses["s1"]

    run = FixtureJourney(
        run_id="catalog-cancellation", plan=plan, acquisitions={"s1": acquire}
    )
    task = asyncio.create_task(run.run())
    try:
        await asyncio.wait_for(started.wait(), 1)
        if adverse:
            event("cancel")
            run.cancel()
        else:
            release.set()
        result = await asyncio.wait_for(task, 1)
        await asyncio.wait_for(returned.wait(), 1)
        event("terminal")
        assert result.execution_outcome == ("cancelled" if adverse else "completed")
        assert (result.publication is None) == adverse
        assert len(result.accounting.operations) == (1 if adverse else 4)
        assert result.accounting.spent.sources == (0 if adverse else 1)
        names = [e["event"] for e in events]
        assert names == (
            [
                "dispatch",
                "cancel",
                "callback_cancelled",
                "callback_returned",
                "terminal",
            ]
            if adverse
            else ["dispatch", "callback_returned", "terminal"]
        )
        assert await run.run() is result
        assert [e["event"] for e in events] == names
        record_trace(
            record_property,
            f"cancellation-{'adverse' if adverse else 'control'}",
            events,
            result,
        )
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("adverse", [False, True], ids=["control", "adverse"])
async def test_catalog_provenance(request, record_property, adverse):
    plan, responses = fixture_plan(request.getfixturevalue("payload"))
    original = responses["s1"]
    raw = plan.model_dump()
    expected = text_digest(original.text)
    raw["sources"][0]["expected_digest"] = expected
    plan = FixturePlan.model_validate(raw)
    # Mutation is outside every cited span, so quote checks alone cannot catch it.
    text = original.text + ("\nUncited changed content." if adverse else "")
    # The digest contract applies after the declared newline normalization.
    response = AcquiredText.model_validate(
        {**original.model_dump(), "text": text.replace("\n", "\r\n")}
    )
    events = []
    loop = asyncio.get_running_loop()

    async def acquire():
        events.append(
            {
                "event": "dispatch",
                "monotonic_timestamp": loop.time(),
                "snapshot_id": "s1",
            }
        )
        events.append(
            {
                "event": "callback_returned",
                "monotonic_timestamp": loop.time(),
                "expected_digest": expected,
                "returned_normalized_digest": text_digest(text),
            }
        )
        return response

    run = FixtureJourney(
        run_id="catalog-provenance", plan=plan, acquisitions={"s1": acquire}
    )
    result = await run.run()
    events.append({"event": "terminal", "monotonic_timestamp": loop.time()})
    assert result.execution_outcome == ("failed" if adverse else "completed")
    assert (result.publication is None) == adverse
    assert len(result.accounting.operations) == (1 if adverse else 4)
    if adverse:
        assert result.stop_reason == "operation_failed"
    else:
        snapshot = result.publication.audits[
            0
        ].checked_input.research.verifications.structure.snapshots[0]
        assert snapshot.digest == expected
        assert snapshot.text == original.text
    assert await run.run() is result
    assert len(events) == 3
    record_trace(
        record_property,
        f"provenance-{'adverse' if adverse else 'control'}",
        events,
        result,
    )
