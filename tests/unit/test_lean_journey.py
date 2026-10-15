"""End-to-end call ceilings and fail-closed behavior for the lean successor."""

import json

import pytest
from agent.experimental.answer_units import SourcePassage
from agent.experimental.knowledge import text_digest
from agent.experimental.lean_construction import RequiredQuestion
from agent.experimental.lean_journey import run_lean_journey
from agent.experimental.model_review import ModelReply, ReviewRequest


def passage() -> SourcePassage:
    quote = "The measured result was 42."
    return SourcePassage(
        passage_id="passage-1",
        snapshot_id="snapshot-1",
        start=0,
        end=len(quote),
        quote=quote,
        quote_digest=text_digest(quote),
    )


def construction(*, kind: str = "source_statement") -> bytes:
    return json.dumps(
        {
            "schema_version": "source-bound-selection/1",
            "units": [
                {
                    "text": "The measured result was 42.",
                    "kind": kind,
                    "question_indices": [1],
                    "passage_indices": [1],
                    "qualifiers": ["According to the retained measurement"],
                    "support": "supported",
                    "support_reason": "The exact passage states 42.",
                    "temporal_scope": "historical",
                    "freshness": "historical",
                    "disputed": False,
                    "high_consequence": False,
                }
            ],
        }
    ).encode()


def arguments() -> tuple[str, tuple[RequiredQuestion, ...], tuple[SourcePassage, ...]]:
    return (
        "What was measured?",
        (RequiredQuestion(question_id="question-1", text="What was measured?"),),
        (passage(),),
    )


@pytest.mark.asyncio
async def test_simple_journey_uses_one_call_and_reports_usage() -> None:
    calls: list[ReviewRequest] = []

    async def complete(request: ReviewRequest) -> ModelReply:
        calls.append(request)
        return ModelReply(construction(), "local-model", 100, 25)

    result = await run_lean_journey(*arguments(), complete=complete)

    assert len(calls) == result.provider_calls == 1
    assert result.reported_input_tokens == 100
    assert result.reported_output_tokens == 25
    assert result.answer.coverage == "complete"
    assert result.answer.text == "The measured result was 42. [1]"


@pytest.mark.asyncio
async def test_risky_journey_uses_exactly_two_calls() -> None:
    responses = iter(
        (
            ModelReply(construction(kind="inference"), "local-model", 100, 25),
            ModelReply(
                json.dumps(
                    {
                        "schema_version": "selective-unit-review/1",
                        "decisions": [
                            {
                                "unit_index": 1,
                                "verdict": "pass",
                                "reason": "The inference follows from the passage.",
                            }
                        ],
                    }
                ).encode(),
                "local-model",
                40,
                10,
            ),
        )
    )
    calls = 0

    async def complete(_: ReviewRequest) -> ModelReply:
        nonlocal calls
        calls += 1
        return next(responses)

    result = await run_lean_journey(*arguments(), complete=complete)

    assert calls == result.provider_calls == 2
    assert result.reported_input_tokens == 140
    assert result.reported_output_tokens == 35


@pytest.mark.asyncio
async def test_failed_selective_review_stops_before_answer() -> None:
    responses = iter(
        (
            ModelReply(construction(kind="inference"), "local-model", None, None),
            ModelReply(
                json.dumps(
                    {
                        "schema_version": "selective-unit-review/1",
                        "decisions": [
                            {
                                "unit_index": 1,
                                "verdict": "fail",
                                "reason": "The inference exceeds the passage.",
                            }
                        ],
                    }
                ).encode(),
                "local-model",
                None,
                None,
            ),
        )
    )
    calls = 0

    async def complete(_: ReviewRequest) -> ModelReply:
        nonlocal calls
        calls += 1
        return next(responses)

    with pytest.raises(ValueError, match="selective review"):
        await run_lean_journey(*arguments(), complete=complete)
    assert calls == 2


@pytest.mark.asyncio
async def test_unknown_usage_remains_unknown() -> None:
    async def complete(_: ReviewRequest) -> ModelReply:
        return ModelReply(construction(), "local-model", None, 25)

    result = await run_lean_journey(*arguments(), complete=complete)

    assert result.reported_input_tokens is None
    assert result.reported_output_tokens == 25
