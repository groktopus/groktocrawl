"""One-call source-bound construction behavior for ADR-0080."""

import json

import pytest
from agent.experimental.answer_units import SourcePassage
from agent.experimental.knowledge import text_digest
from agent.experimental.lean_construction import (
    RequiredQuestion,
    construct_answer_units,
)
from agent.experimental.model_review import ModelReply, ReviewRequest


def passage(identity: str, quote: str) -> SourcePassage:
    return SourcePassage(
        passage_id=identity,
        snapshot_id=f"snapshot-{identity}",
        start=0,
        end=len(quote),
        quote=quote,
        quote_digest=text_digest(quote),
    )


def reply(**changes: object) -> bytes:
    unit: dict[str, object] = {
        "text": "The retained test result was 42.",
        "kind": "source_statement",
        "question_indices": [1],
        "passage_indices": [2],
        "qualifiers": ["According to the retained test report"],
        "support": "supported",
        "support_reason": "The selected passage reports 42.",
        "temporal_scope": "historical",
        "freshness": "historical",
        "disputed": False,
        "high_consequence": False,
    }
    unit.update(changes)
    return json.dumps(
        {"schema_version": "source-bound-selection/1", "units": [unit]}
    ).encode()


@pytest.mark.asyncio
async def test_one_call_maps_indices_to_application_owned_identities() -> None:
    calls: list[ReviewRequest] = []

    async def complete(request: ReviewRequest) -> ModelReply:
        calls.append(request)
        return ModelReply(reply(), "local-model", 100, 30)

    result = await construct_answer_units(
        "What did the test report?",
        (RequiredQuestion(question_id="question-result", text="What was the result?"),),
        (
            passage("passage-context", "The test ran on Tuesday."),
            passage("passage-result", "The retained test result was 42."),
        ),
        complete=complete,
    )

    assert len(calls) == 1
    assert calls[0].max_output_tokens == 1536
    assert result.bundle.units[0].unit_id == "unit-1"
    assert result.bundle.units[0].question_ids == ("question-result",)
    assert result.bundle.units[0].passage_ids == ("passage-result",)
    assert result.model_reply.resolved_model == "local-model"


@pytest.mark.asyncio
async def test_absent_or_repeated_selections_fail_closed() -> None:
    responses = iter(
        (
            reply(passage_indices=[2]),
            reply(question_indices=[1, 1]),
        )
    )

    async def complete(_: ReviewRequest) -> ModelReply:
        return ModelReply(next(responses), "local-model", None, None)

    arguments = (
        "Question",
        (RequiredQuestion(question_id="question-1", text="Question"),),
        (passage("passage-1", "Only passage."),),
    )
    with pytest.raises(ValueError, match="absent or repeated passage"):
        await construct_answer_units(*arguments, complete=complete)
    with pytest.raises(ValueError, match="absent or repeated question"):
        await construct_answer_units(*arguments, complete=complete)


@pytest.mark.asyncio
async def test_invalid_answer_unit_semantics_are_not_repaired() -> None:
    async def complete(_: ReviewRequest) -> ModelReply:
        return ModelReply(
            reply(
                kind="source_statement",
                support="insufficient",
                passage_indices=[1],
            ),
            "local-model",
            None,
            None,
        )

    with pytest.raises(ValueError, match="non-uncertainty"):
        await construct_answer_units(
            "Question",
            (RequiredQuestion(question_id="question-1", text="Question"),),
            (passage("passage-1", "Only passage."),),
            complete=complete,
        )


@pytest.mark.asyncio
async def test_preflight_budgets_reject_without_calling_model() -> None:
    calls = 0

    async def complete(_: ReviewRequest) -> ModelReply:
        nonlocal calls
        calls += 1
        return ModelReply(reply(), "local-model", None, None)

    with pytest.raises(ValueError, match="byte budget"):
        await construct_answer_units(
            "Question",
            (RequiredQuestion(question_id="question-1", text="Question"),),
            (
                passage("passage-1", "x" * 70_000),
                passage("passage-2", "y" * 70_000),
            ),
            complete=complete,
        )
    assert calls == 0
