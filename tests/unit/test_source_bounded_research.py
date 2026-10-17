"""Captured-source answers do not claim unproved present-day validity."""

import json

import pytest
from agent.experimental.model_review import ModelReply
from agent.experimental.query_construction import CapturedSource
from agent.experimental.real_journey import research_from_sources

from tests.unit.test_query_construction import (
    QUESTION,
    TEXT,
    batch_decisions,
    journey_clock,
    proposal,
)


@pytest.mark.asyncio
async def test_source_bound_report_publishes_only_explicit_captured_statements():
    calls = []

    async def complete(request):
        p = json.loads(request.payload)
        calls.append(p)
        if "schema" in p:
            assert p["answer_basis"] == "captured_sources"
            assert (
                p["schema"]["$defs"]["ClaimSelection"]["properties"]["temporal_scope"][
                    "const"
                ]
                == "historical"
            )
            content = proposal()
        else:
            assert all(
                c["temporal_scope"] == "historical" for c in p["context"]["claims"]
            )
            assert all(
                "captured sources report" in c["text"] for c in p["context"]["claims"]
            )
            content = batch_decisions(p)
        return ModelReply(json.dumps(content).encode(), "test-model", 1, 1)

    result = await research_from_sources(
        QUESTION,
        (CapturedSource("https://example.test/note", TEXT, "2026-09-06T00:00:00Z"),),
        complete=complete,
        scope_id="test-scope",
        clock=journey_clock,
        source_bound=True,
    )
    assert len(calls) == 2
    assert len(result.reports) == 3
    for report in result.reports:
        assert (
            "does not independently confirm present-day validity"
            in report.body.decode()
        )
        assert "captured sources report" in report.body.decode()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case", ["current_claim", "missing_capture", "past_target", "unsupported"]
)
async def test_source_bound_mode_cannot_bypass_verification(case):
    calls = []

    async def complete(request):
        p = json.loads(request.payload)
        calls.append(p)
        if "schema" in p:
            content = proposal()
            if case == "current_claim":
                content["claims"][0]["temporal_scope"] = "current"
        else:
            content = batch_decisions(
                p, reject="semantic_support" if case == "unsupported" else None
            )
        return ModelReply(json.dumps(content).encode(), "test-model", 1, 1)

    with pytest.raises(ValueError):
        await research_from_sources(
            QUESTION,
            (
                CapturedSource(
                    "https://example.test/note",
                    TEXT,
                    None if case == "missing_capture" else "2026-09-06T00:00:00Z",
                ),
            ),
            complete=complete,
            scope_id="test-scope",
            clock=journey_clock,
            as_of="2026-09-01T00:00:00Z" if case == "past_target" else None,
            source_bound=True,
        )
    assert len(calls) == (1 if case == "current_claim" else 2)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "budget,expected",
    [
        (None, "224b934819b5754ee9ecd2f7b22eabfa6dbb518601d29cb30411a2d24236a133"),
        (32000, "60a61a93edba1e5ab2425ac38fc13c9b6769e591e122fefe3b1cc40cd7b4ff75"),
    ],
)
async def test_previous_policy_model_requests_keep_their_exact_bytes(budget, expected):
    # Captured from main 306400e6 before adding source-bound mode.
    import hashlib
    from dataclasses import asdict

    from agent.experimental.query_construction import construct_research

    async def complete(request):
        value = asdict(request)
        value["payload"] = request.payload.decode()
        assert (
            hashlib.sha256(
                json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            == expected
        )
        return ModelReply(json.dumps(proposal()).encode(), "test-model", 1, 1)

    await construct_research(
        QUESTION,
        (CapturedSource("https://example.test/note", TEXT, "2026-09-06T00:00:00Z"),),
        complete=complete,
        scope_id="test-scope",
        clock=journey_clock,
        research_id="research-byte-check",
        revision_id="revision-byte-check",
        max_output_tokens=8192,
        evidence_budget_chars=budget,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "construction_seconds,review_seconds,source_bound,error,budget,discovery_seconds,override",
    [
        (130, 20, True, None, 180, 0, True),
        (20, 130, True, None, 180, 0, True),
        (110, 80, True, TimeoutError, 180, 0, True),
        (130, 20, False, TimeoutError, 180, 0, False),
        (20, 100, False, ValueError, 180, 0, False),
        (153, 35, True, None, 300, 25, True),
        (153, 35, True, TimeoutError, 180, 25, True),
        (180, 130, True, TimeoutError, 300, 0, True),
        (130, 20, True, TimeoutError, 180, 0, False),
        (20, 100, True, ValueError, 180, 0, False),
    ],
)
async def test_model_stages_respect_the_shared_research_deadline(
    monkeypatch,
    construction_seconds,
    review_seconds,
    source_bound,
    error,
    budget,
    discovery_seconds,
    override,
):
    import asyncio

    from agent.experimental.query_runner import run_query

    elapsed = 0
    calls = []

    class VirtualTimeout:
        """Advance simulated provider time without making CI wait minutes."""

        def __init__(self, seconds):
            self.seconds = seconds

        async def __aenter__(self):
            self.started = elapsed
            return self

        async def __aexit__(self, exc_type, _exc, _tb):
            if exc_type is None and elapsed - self.started > self.seconds:
                raise TimeoutError("simulated provider exceeded stage deadline")

    monkeypatch.setattr(asyncio, "timeout", VirtualTimeout)

    async def search(_query, _limit):
        nonlocal elapsed
        elapsed += discovery_seconds
        return ("https://example.test/note",)

    async def acquire(url):
        return CapturedSource(url, TEXT, "2026-09-06T00:00:00Z")

    async def complete(request):
        nonlocal elapsed
        payload = json.loads(request.payload)
        constructing = "schema" in payload
        calls.append("construction" if constructing else "review")
        elapsed += construction_seconds if constructing else review_seconds
        content = proposal() if constructing else batch_decisions(payload)
        return ModelReply(json.dumps(content).encode(), "test-model", 1, 1)

    async def run():
        return await run_query(
            QUESTION,
            search=search,
            acquire=acquire,
            complete=complete,
            scope_id="test-scope",
            clock=journey_clock,
            source_bound=source_bound,
            timeout_seconds=budget,
            model_timeout_seconds=budget if source_bound and override else None,
        )

    if error is not None:
        with pytest.raises(error):
            await run()
    else:
        result = await run()
        assert len(result.reports) == 3
        assert calls == ["construction", "review"]
        assert elapsed == construction_seconds + review_seconds + discovery_seconds


@pytest.mark.parametrize(
    "policy,expected",
    [
        (None, 180),
        ("durable-live/2", 180),
        ("durable-live/3", 180),
        ("durable-live/4", 180),
        ("durable-live/5", 300),
    ],
)
def test_execution_deadline_is_pinned_to_the_admitted_policy(policy, expected):
    from agent.experimental.live_policy import execution_timeout_seconds

    assert execution_timeout_seconds(policy) == expected
