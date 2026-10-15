"""Construction cannot substitute model-authored provenance or altered evidence."""

import json
from datetime import UTC, datetime

import pytest
from agent.experimental.model_review import ModelReply
from agent.experimental.query_construction import CapturedSource, construct_research

QUESTION = "What does the pilot establish?"
TEXT = "Pilot lead time fell. Causation is unproven."


def journey_clock():
    return datetime(2026, 9, 7, tzinfo=UTC)


def proposal():
    return {
        "schema_version": "research-construction/4",
        "evidence": [
            {
                "source_index": 1,
                "start_line": 1,
                "end_line": 1,
            }
        ],
        "claims": [
            {
                "text": "The captured note reports lower lead time without establishing causation.",
                "qualifiers": ["One captured pilot note"],
                "temporal_scope": "historical",
                "supported_by": [1],
                "contradicted_by": [],
            }
        ],
        "answer_claim_index": 1,
        "answer_status": "answered",
        "conflicts": [],
    }


def batch_decisions(payload, *, reject=None):
    return {
        "schema_version": "model-review-batch/1",
        "decisions": [
            {
                "check_index": check["check_index"],
                "outcome": "fail"
                if check["check_type"] == reject
                else "supported"
                if check["check_type"] == "assessment"
                else "pass",
                "reason": "Scripted batch response for integration testing only.",
            }
            for check in payload["checks"]
        ],
    }


async def run(value, text=TEXT):
    async def complete(request):
        assert request.requested_model == "local"
        return ModelReply(json.dumps(value).encode(), "local", None, None)

    return await construct_research(
        QUESTION,
        (CapturedSource("https://example.test/pilot", text, "2026-09-06T00:00:00Z"),),
        complete=complete,
        scope_id="owner",
        clock=lambda: datetime(2026, 9, 7, tzinfo=UTC),
    )


@pytest.mark.asyncio
async def test_constructs_unverified_context_with_server_owned_sources():
    result = await run(proposal())
    assert result.context.objective == QUESTION
    assert result.context.snapshots[0].published_at is None
    assert result.context.evidence[0].start == 0
    assert result.context.evidence[0].end == len(TEXT)
    assert result.context.scope_id == "owner"
    assert not hasattr(result.context, "verifications")
    assert result.model_reply.resolved_model == "local"
    source = await result.resolve(result.context.snapshots[0].content_ref)
    assert source.body == TEXT.encode()


@pytest.mark.asyncio
async def test_construction_requests_its_strict_output_schema():
    seen = []

    async def complete(request):
        seen.append(request.response_schema)
        return ModelReply(json.dumps(proposal()).encode(), "local", None, None)

    await construct_research(
        QUESTION,
        (CapturedSource("https://example.test/pilot", TEXT, "2026-09-06T00:00:00Z"),),
        complete=complete,
        scope_id="owner",
        clock=lambda: datetime(2026, 9, 7, tzinfo=UTC),
    )
    assert seen[0]["properties"]["schema_version"]["const"] == (
        "research-construction/4"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["quote", "question", "scope", "source", "human"])
async def test_rejects_model_changes_to_evidence_or_authority(mutation):
    value = proposal()
    if mutation == "quote":
        value["evidence"][0]["quote"] = "Invented"
    elif mutation == "question":
        value["question"] = "Different question"
    elif mutation == "source":
        value["evidence"][0]["source_index"] = 2
    else:
        value["scope_id" if mutation == "scope" else "human_approved"] = "forged"
    with pytest.raises(ValueError):
        await run(value)


@pytest.mark.asyncio
async def test_explicit_lines_distinguish_repeated_text():
    result = await run(proposal(), TEXT + "\n" + TEXT)
    assert result.context.evidence[0].quote == TEXT + "\n"
    assert result.context.evidence[0].end == len(TEXT) + 1


@pytest.mark.asyncio
async def test_model_journey_executes_checks_and_audit_without_fixture_provenance():
    from agent.experimental.real_journey import research_from_sources

    calls = []

    async def complete(request):
        value = json.loads(request.payload)
        calls.append(value)
        if "schema" in value:
            content = proposal()
        elif "checks" in value:
            content = batch_decisions(value)
        else:
            assessment = value.get("check", {}).get("check_type") == "assessment"
            content = {
                "schema_version": "model-review-decision/1",
                "input_digest": value["input_digest"],
                "outcome": "supported" if assessment else "pass",
                "reason": "Scripted transport response for integration testing only.",
            }
        return ModelReply(json.dumps(content).encode(), "test-model", 1, 1)

    result = await research_from_sources(
        QUESTION,
        (CapturedSource("https://example.test/pilot", TEXT, "2026-09-06T00:00:00Z"),),
        complete=complete,
        scope_id="test-scope",
        clock=journey_clock,
    )
    assert not result.candidate.fixture_only
    assert len(result.reports) == 3
    assert len({report.body for report in result.reports}) == 3
    assert len(calls) == 2  # construction and one semantic-check batch
    assert "checks" in calls[-1]
    assert {item["check_type"] for item in calls[-1]["checks"]} == {
        "assessment",
        "semantic_support",
        "freshness",
    }
    reviewers = {
        i.check_type: i.reviewer.kind
        for i in result.candidate.admitted.knowledge.verification_inputs
    }
    assert reviewers["structural"] == "tool"
    assert reviewers["conflict_coverage"] == "tool"
    assert reviewers["assessment"] == "model"
    assert reviewers["semantic_support"] == "model"
    assert reviewers["freshness"] == "model"
    assert all(
        i.reviewer.kind == "tool"
        for i in result.candidate.admitted.manifest.audit_inputs
    )


@pytest.mark.asyncio
async def test_query_dispatches_acquisition_then_real_contract_journey():
    from agent.experimental.query_runner import run_query

    events = []

    async def search(query, limit):
        events.append("search")
        assert query == QUESTION and limit == 3
        return ("https://example.test/pilot",)

    async def acquire(url):
        events.append("acquire")
        return CapturedSource(url, TEXT, "2026-09-06T00:00:00Z")

    async def complete(request):
        events.append("model")
        value = json.loads(request.payload)
        content = (
            proposal()
            if "schema" in value
            else batch_decisions(value)
            if "checks" in value
            else {
                "schema_version": "model-review-decision/1",
                "input_digest": value["input_digest"],
                "outcome": "supported"
                if value.get("check", {}).get("check_type") == "assessment"
                else "pass",
                "reason": "Scripted integration response.",
            }
        )
        return ModelReply(json.dumps(content).encode(), "test-model", 1, 1)

    result = await run_query(
        QUESTION,
        search=search,
        acquire=acquire,
        complete=complete,
        scope_id="test-scope",
        clock=journey_clock,
    )
    assert not result.candidate.fixture_only
    assert events[:2] == ["search", "acquire"]
    assert events.count("model") == 2


@pytest.mark.asyncio
async def test_query_cancellation_during_search_prevents_acquisition():
    import asyncio

    from agent.experimental.query_runner import run_query

    started = asyncio.Event()
    calls = []

    async def search(query, limit):
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            asyncio.current_task().uncancel()
        return ("https://example.test/pilot",)

    async def acquire(url):
        calls.append(url)
        return CapturedSource(url, TEXT, "2026-09-06T00:00:00Z")

    async def complete(request):
        raise AssertionError("model must not run")

    task = asyncio.create_task(
        run_query(
            QUESTION,
            search=search,
            acquire=acquire,
            complete=complete,
            scope_id="test-scope",
        )
    )
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("reject", ["semantic_support"])
async def test_real_journey_cannot_publish_after_negative_model_review(reject):
    from agent.experimental.real_journey import research_from_sources

    async def complete(request):
        value = json.loads(request.payload)
        if "schema" in value:
            content = proposal()
        elif "checks" in value:
            content = batch_decisions(value, reject=reject)
        else:
            kind = value.get("check", {}).get("check_type", "audit")
            content = {
                "schema_version": "model-review-decision/1",
                "input_digest": value["input_digest"],
                "outcome": "fail"
                if kind == reject
                else "supported"
                if kind == "assessment"
                else "pass",
                "reason": "Scripted negative-review control.",
            }
        return ModelReply(json.dumps(content).encode(), "test-model", 1, 1)

    with pytest.raises(ValueError):
        await research_from_sources(
            QUESTION,
            (
                CapturedSource(
                    "https://example.test/pilot", TEXT, "2026-09-06T00:00:00Z"
                ),
            ),
            complete=complete,
            scope_id="test-scope",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["missing", "reordered", "wrong_outcome"])
async def test_real_journey_rejects_invalid_batched_review(mutation):
    from agent.experimental.real_journey import research_from_sources

    async def complete(request):
        value = json.loads(request.payload)
        if "schema" in value:
            content = proposal()
        else:
            content = batch_decisions(value)
            if mutation == "missing":
                content["decisions"].pop()
            elif mutation == "reordered":
                content["decisions"][0]["check_index"] = 2
            else:
                content["decisions"][0]["outcome"] = "pass"
        return ModelReply(json.dumps(content).encode(), "test-model", 1, 1)

    with pytest.raises(ValueError, match="no judgment accepted"):
        await research_from_sources(
            QUESTION,
            (
                CapturedSource(
                    "https://example.test/pilot", TEXT, "2026-09-06T00:00:00Z"
                ),
            ),
            complete=complete,
            scope_id="test-scope",
        )


def test_fixture_entry_point_still_rejects_model_reviewers():
    from agent.experimental.consolidated_example import example_journey
    from agent.experimental.consolidated_journey import ConsolidatedFixtureJourney
    from agent.experimental.model_review import ModelReviewAdapter

    async def complete(request):
        raise AssertionError("must not dispatch")

    fixture = example_journey()
    adapter = ModelReviewAdapter(provider="test", model="local", complete=complete)
    with pytest.raises(ValueError, match="requires fixture reviewers"):
        ConsolidatedFixtureJourney(
            context=fixture._context,
            checks=fixture._checks,
            acquisitions=fixture._acquire,
            verifier=adapter.reviewer,
            verify=fixture._verify,
            renderer=fixture._renderer,
            render=fixture._render,
            auditor=adapter.reviewer,
            audit=fixture._audit,
            artifact_set_id=fixture._artifact_set_id,
            clock=fixture._clock,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("start,end", [(0, 1), (2, 1), (1, 2), (True, 1)])
async def test_invalid_line_ranges_are_rejected(start, end):
    value = proposal()
    value["evidence"][0].update(start_line=start, end_line=end)
    with pytest.raises(ValueError):
        await run(value)
