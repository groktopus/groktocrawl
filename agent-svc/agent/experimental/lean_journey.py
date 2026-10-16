"""Bounded end-to-end composition of ADR-0080's lean research policy."""

from dataclasses import dataclass

from .answer_units import AssembledAnswer, SourcePassage, assemble_answer
from .lean_construction import (
    ConstructedAnswerUnits,
    RequiredQuestion,
    construct_answer_units,
)
from .lean_review import SelectiveReviewResult, review_selected_units
from .model_review import Complete


@dataclass(frozen=True)
class LeanJourneyResult:
    construction: ConstructedAnswerUnits
    review: SelectiveReviewResult
    answer: AssembledAnswer
    provider_calls: int
    reported_input_tokens: int | None
    reported_output_tokens: int | None


def _reported_total(values: tuple[int | None, ...]) -> int | None:
    if any(value is None for value in values):
        return None
    return sum(value for value in values if value is not None)


async def run_lean_journey(
    objective: str,
    questions: tuple[RequiredQuestion, ...],
    passages: tuple[SourcePassage, ...],
    *,
    complete: Complete,
    model: str = "local",
) -> LeanJourneyResult:
    """Run one construction call, optional one review call, then deterministic assembly."""

    construction = await construct_answer_units(
        objective,
        questions,
        passages,
        complete=complete,
        model=model,
    )
    review = await review_selected_units(
        construction.bundle,
        complete=complete,
        model=model,
    )
    answer = assemble_answer(construction.bundle, review.reviews)
    replies = (construction.model_reply,) + (
        (review.model_reply,) if review.model_reply is not None else ()
    )
    if not 1 <= len(replies) <= 2:
        raise AssertionError("lean journey exceeded its provider-call contract")
    return LeanJourneyResult(
        construction=construction,
        review=review,
        answer=answer,
        provider_calls=len(replies),
        reported_input_tokens=_reported_total(
            tuple(reply.input_tokens for reply in replies)
        ),
        reported_output_tokens=_reported_total(
            tuple(reply.output_tokens for reply in replies)
        ),
    )
