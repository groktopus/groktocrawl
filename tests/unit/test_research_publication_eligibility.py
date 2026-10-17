"""Live-shaped review outcomes must reach an honest publication decision."""

import json

import pytest
from agent.experimental.model_review import ModelReply
from agent.experimental.publication_gate import UnverifiedResearchError
from agent.experimental.query_construction import CapturedSource
from agent.experimental.real_journey import research_from_sources

from tests.unit.test_query_construction import (
    QUESTION,
    TEXT,
    batch_decisions,
    journey_clock,
    proposal,
)


async def reviewed_run(
    *, required_current, freshness="indeterminate", reject_support=False
):
    calls = []

    async def complete(request):
        payload = json.loads(request.payload)
        calls.append(payload)
        if "schema" in payload:
            content = proposal()
            if required_current:
                content["claims"][0]["temporal_scope"] = "current"
            else:
                content["claims"].append(
                    {
                        **content["claims"][0],
                        "text": "Unverified current conclusion.",
                        "temporal_scope": "current",
                    }
                )
        else:
            content = batch_decisions(payload)
            for check, decision in zip(
                payload["checks"], content["decisions"], strict=True
            ):
                if (
                    check["check_type"] == "freshness"
                    and check["freshness"]["sources"][0]["basis"] == "unknown"
                ):
                    decision["outcome"] = freshness
                if reject_support and check["check_type"] == "semantic_support":
                    decision["outcome"] = "fail"
        return ModelReply(json.dumps(content).encode(), "test-model", 1, 1)

    try:
        return await research_from_sources(
            QUESTION,
            (
                CapturedSource(
                    "https://example.test/pilot", TEXT, "2026-09-06T00:00:00Z"
                ),
            ),
            complete=complete,
            scope_id="test-scope",
            clock=journey_clock,
        )
    finally:
        assert len(calls) == 2, "Publication must not repeat model operations"


@pytest.mark.asyncio
async def test_current_reporting_claim_explains_unverified_freshness():
    with pytest.raises(
        UnverifiedResearchError, match="Source freshness could not be verified"
    ) as error:
        await reviewed_run(required_current=True)
    assert error.value.reason == "source_freshness_unverified"


@pytest.mark.asyncio
async def test_optional_unverified_claim_is_omitted_without_losing_verified_answer():
    result = await reviewed_run(required_current=False)
    assert len(result.reports) == 3
    assert len(result.candidate.admitted.knowledge.context.claims) == 2
    for report in result.reports:
        assert "Unverified current conclusion." not in report.body.decode()
        assert "could not be verified" in report.body.decode()
        assert {c for s in report.artifact.statements for c in s.claim_ids} == {
            "claim-1"
        }


@pytest.mark.asyncio
async def test_model_cannot_pass_freshness_without_an_allowed_basis():
    with pytest.raises(ValueError):
        await reviewed_run(required_current=True, freshness="pass")


@pytest.mark.asyncio
async def test_required_answer_support_failure_has_a_safe_explanation():
    with pytest.raises(UnverifiedResearchError) as error:
        await reviewed_run(required_current=False, reject_support=True)
    assert error.value.reason == "answer_support_unverified"
    assert "did not pass evidence verification" in error.value.message
