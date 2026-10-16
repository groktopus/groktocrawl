"""Selective answer-unit review uses no more than one model call."""

import json

import pytest
from agent.experimental.answer_units import (
    ANSWER_UNIT_SCHEMA,
    AnswerUnit,
    AnswerUnitBundle,
    SourcePassage,
    assemble_answer,
)
from agent.experimental.knowledge import text_digest
from agent.experimental.lean_review import review_selected_units
from agent.experimental.model_review import ModelReply, ReviewRequest


def passage(identity: str) -> SourcePassage:
    quote = f"Evidence for {identity}."
    return SourcePassage(
        passage_id=identity,
        snapshot_id=f"snapshot-{identity}",
        start=0,
        end=len(quote),
        quote=quote,
        quote_digest=text_digest(quote),
    )


def unit(identity: str, **changes: object) -> AnswerUnit:
    values: dict[str, object] = {
        "unit_id": identity,
        "text": f"Statement for {identity}.",
        "kind": "source_statement",
        "question_ids": ("question-1",),
        "passage_ids": ("passage-1",),
        "qualifiers": ("According to the source",),
        "support": "supported",
        "support_reason": "The passage states it.",
        "temporal_scope": "historical",
        "freshness": "historical",
        "disputed": False,
        "high_consequence": False,
    }
    values.update(changes)
    return AnswerUnit(**values)


def bundle(*units: AnswerUnit) -> AnswerUnitBundle:
    return AnswerUnitBundle(
        schema_version=ANSWER_UNIT_SCHEMA,
        required_question_ids=("question-1",),
        passages=(passage("passage-1"), passage("passage-2")),
        units=units,
    )


@pytest.mark.asyncio
async def test_simple_units_use_zero_review_calls() -> None:
    calls = 0

    async def complete(_: ReviewRequest) -> ModelReply:
        nonlocal calls
        calls += 1
        raise AssertionError("simple units must not invoke review")

    result = await review_selected_units(bundle(unit("unit-1")), complete=complete)

    assert calls == 0
    assert result.model_reply is None
    assert result.reviews == ()
    assert assemble_answer(bundle(unit("unit-1")), result.reviews).coverage == "complete"


@pytest.mark.asyncio
async def test_all_flagged_units_share_one_ordered_review_call() -> None:
    calls: list[ReviewRequest] = []

    async def complete(request: ReviewRequest) -> ModelReply:
        calls.append(request)
        return ModelReply(
            json.dumps(
                {
                    "schema_version": "selective-unit-review/1",
                    "decisions": [
                        {"unit_index": 1, "verdict": "pass", "reason": "supported"},
                        {"unit_index": 2, "verdict": "pass", "reason": "qualified"},
                    ],
                }
            ).encode(),
            "local-model",
            200,
            40,
        )

    candidate = bundle(
        unit("unit-inference", kind="inference"),
        unit("unit-combined", passage_ids=("passage-1", "passage-2")),
        unit("unit-simple"),
    )
    result = await review_selected_units(candidate, complete=complete)

    assert len(calls) == 1
    assert calls[0].max_output_tokens == 1024
    assert tuple(item.unit_id for item in result.reviews) == (
        "unit-inference",
        "unit-combined",
    )
    assert assemble_answer(candidate, result.reviews).coverage == "complete"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "decisions",
    [
        [{"unit_index": 1, "verdict": "pass", "reason": "only one"}],
        [
            {"unit_index": 2, "verdict": "pass", "reason": "wrong order"},
            {"unit_index": 1, "verdict": "pass", "reason": "wrong order"},
        ],
    ],
)
async def test_incomplete_or_reordered_review_fails_closed(
    decisions: list[dict[str, object]],
) -> None:
    async def complete(_: ReviewRequest) -> ModelReply:
        return ModelReply(
            json.dumps(
                {"schema_version": "selective-unit-review/1", "decisions": decisions}
            ).encode(),
            "local-model",
            None,
            None,
        )

    candidate = bundle(
        unit("unit-1", kind="inference"),
        unit("unit-2", high_consequence=True),
    )
    with pytest.raises(ValueError, match="complete and ordered"):
        await review_selected_units(candidate, complete=complete)


@pytest.mark.asyncio
async def test_failed_review_cannot_be_assembled() -> None:
    async def complete(_: ReviewRequest) -> ModelReply:
        return ModelReply(
            json.dumps(
                {
                    "schema_version": "selective-unit-review/1",
                    "decisions": [
                        {"unit_index": 1, "verdict": "fail", "reason": "unsupported"}
                    ],
                }
            ).encode(),
            "local-model",
            None,
            None,
        )

    candidate = bundle(unit("unit-1", disputed=True))
    result = await review_selected_units(candidate, complete=complete)
    with pytest.raises(ValueError, match="selective review"):
        assemble_answer(candidate, result.reviews)
