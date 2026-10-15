"""Fail-closed publication admission for ADR-0080 development artifacts."""

from typing import Literal

from .answer_units import AssembledAnswer, Citation, assemble_answer
from .knowledge import Digest, text_digest
from .knowledge_context import StrictRecord
from .lean_journey import LeanJourneyResult
from .passage_preparation import RetainedSourceText, prepare_source_passages


class LeanPublication(StrictRecord):
    status: Literal["eligible_development_artifact"]
    answer: AssembledAnswer
    answer_digest: Digest
    citations: tuple[Citation, ...]
    provider_calls: Literal[1, 2]
    policy_version: Literal["lean-evidence-first/1"]


def prepare_lean_publication(
    result: LeanJourneyResult,
    sources: tuple[RetainedSourceText, ...],
) -> LeanPublication:
    """Reproduce exact passages, reviews, rendering, and usage before admission."""

    expected_passages = prepare_source_passages(sources)
    if result.construction.bundle.passages != expected_passages:
        raise ValueError("answer passages differ from exact retained sources")
    expected_answer = assemble_answer(
        result.construction.bundle,
        result.review.reviews,
    )
    if result.answer != expected_answer:
        raise ValueError("answer differs from deterministic assembly")
    expected_calls: Literal[1, 2] = 2 if result.review.model_reply is not None else 1
    if result.provider_calls != expected_calls:
        raise ValueError("reported provider calls differ from journey receipts")
    if result.construction.model_reply.resolved_model == "":
        raise ValueError("construction has no resolved model identity")
    if result.review.model_reply is not None and (
        result.review.model_reply.resolved_model == ""
    ):
        raise ValueError("selective review has no resolved model identity")
    return LeanPublication(
        status="eligible_development_artifact",
        answer=result.answer,
        answer_digest=text_digest(result.answer.text),
        citations=result.answer.citations,
        provider_calls=expected_calls,
        policy_version="lean-evidence-first/1",
    )
