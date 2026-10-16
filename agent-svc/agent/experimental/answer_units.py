"""Lean source-bound answer units proposed by ADR-0080."""

from typing import Literal, Self

from pydantic import Field, model_validator

from .knowledge import Digest, Identity, Text, text_digest
from .knowledge_context import Count, StrictRecord

ANSWER_UNIT_SCHEMA = "source-bound-answer-units/1"


class SourcePassage(StrictRecord):
    """An application-owned passage identity over exact retained source bytes."""

    passage_id: Identity
    snapshot_id: Identity
    start: Count
    end: Count
    quote: Text
    quote_digest: Digest

    @model_validator(mode="after")
    def valid_span(self) -> Self:
        if self.start >= self.end or self.end - self.start != len(self.quote):
            raise ValueError("passage span must match its nonempty exact quote")
        if text_digest(self.quote) != self.quote_digest:
            raise ValueError("passage digest must match its exact quote")
        return self


class AnswerUnit(StrictRecord):
    """Model-selected content whose identities and admissibility remain external."""

    unit_id: Identity
    text: Text
    kind: Literal["source_statement", "inference", "uncertainty"]
    question_ids: tuple[Identity, ...] = Field(min_length=1, max_length=20)
    passage_ids: tuple[Identity, ...] = Field(min_length=1, max_length=8)
    qualifiers: tuple[Text, ...] = Field(min_length=1, max_length=10)
    support: Literal["supported", "contested", "insufficient"]
    support_reason: Text
    temporal_scope: Literal["current", "historical"]
    freshness: Literal["current", "historical", "unknown"]
    disputed: bool
    high_consequence: bool

    @model_validator(mode="after")
    def semantic_shape(self) -> Self:
        if len(set(self.question_ids)) != len(self.question_ids) or len(
            set(self.passage_ids)
        ) != len(self.passage_ids):
            raise ValueError("answer-unit references must be distinct")
        if self.kind != "uncertainty" and self.support != "supported":
            raise ValueError("non-uncertainty units must report supported evidence")
        if self.kind == "uncertainty" and self.support == "supported":
            raise ValueError("uncertainty units must report a contested or insufficient basis")
        if self.kind != "uncertainty" and self.freshness == "unknown":
            raise ValueError("unknown freshness requires an uncertainty unit")
        if self.kind != "uncertainty" and self.temporal_scope != self.freshness:
            raise ValueError("answer-unit temporal scope differs from freshness basis")
        return self


class AnswerUnitBundle(StrictRecord):
    schema_version: Literal["source-bound-answer-units/1"]
    required_question_ids: tuple[Identity, ...] = Field(min_length=1, max_length=20)
    passages: tuple[SourcePassage, ...] = Field(min_length=1, max_length=32)
    units: tuple[AnswerUnit, ...] = Field(min_length=1, max_length=12)

    @model_validator(mode="after")
    def references_close(self) -> Self:
        question_ids = set(self.required_question_ids)
        passage_ids = {passage.passage_id for passage in self.passages}
        unit_ids = [unit.unit_id for unit in self.units]
        if len(question_ids) != len(self.required_question_ids):
            raise ValueError("required question identities must be distinct")
        if len(passage_ids) != len(self.passages) or len(set(unit_ids)) != len(unit_ids):
            raise ValueError("passage and unit identities must be distinct")
        if passage_ids & set(unit_ids):
            raise ValueError("passage and unit identity domains must not overlap")
        for unit in self.units:
            if not set(unit.question_ids) <= question_ids:
                raise ValueError("answer unit references an unknown question")
            if not set(unit.passage_ids) <= passage_ids:
                raise ValueError("answer unit references an unknown passage")
        return self


class UnitReview(StrictRecord):
    unit_id: Identity
    verdict: Literal["pass", "fail", "indeterminate"]
    reason: Text


class Citation(StrictRecord):
    number: int = Field(ge=1, le=32)
    passage_id: Identity
    snapshot_id: Identity


class RenderedUnit(StrictRecord):
    unit_id: Identity
    text: Text
    question_ids: tuple[Identity, ...]
    citation_numbers: tuple[int, ...]


class AssembledAnswer(StrictRecord):
    coverage: Literal["complete", "partial", "insufficient"]
    text: Text
    units: tuple[RenderedUnit, ...]
    citations: tuple[Citation, ...]
    missing_question_ids: tuple[Identity, ...]


def requires_selective_review(unit: AnswerUnit) -> bool:
    """Deterministic ADR-0080 review trigger; it grants no publication authority."""

    return (
        unit.kind == "inference"
        or len(unit.passage_ids) > 1
        or unit.disputed
        or unit.high_consequence
    )


def assemble_answer(
    bundle: AnswerUnitBundle, reviews: tuple[UnitReview, ...] = ()
) -> AssembledAnswer:
    """Render only eligible units and assign citations from application passages."""

    bundle = AnswerUnitBundle.model_validate_json(bundle.model_dump_json())
    review_by_unit: dict[str, UnitReview] = {}
    known_units = {unit.unit_id for unit in bundle.units}
    for supplied_review in reviews:
        if (
            supplied_review.unit_id not in known_units
            or supplied_review.unit_id in review_by_unit
        ):
            raise ValueError("review references an absent or repeated answer unit")
        review_by_unit[supplied_review.unit_id] = supplied_review

    for unit in bundle.units:
        if requires_selective_review(unit):
            required_review = review_by_unit.get(unit.unit_id)
            if required_review is None or required_review.verdict != "pass":
                raise ValueError("required selective review did not pass")

    cited_ids = {
        passage_id for unit in bundle.units for passage_id in unit.passage_ids
    }
    ordered_passages = [
        passage for passage in bundle.passages if passage.passage_id in cited_ids
    ]
    citation_number = {
        passage.passage_id: number
        for number, passage in enumerate(ordered_passages, 1)
    }
    rendered = tuple(
        RenderedUnit(
            unit_id=unit.unit_id,
            text=unit.text,
            question_ids=unit.question_ids,
            citation_numbers=tuple(citation_number[item] for item in unit.passage_ids),
        )
        for unit in bundle.units
    )
    lines = [
        f"{unit.text} "
        + "".join(f"[{citation_number[item]}]" for item in unit.passage_ids)
        for unit in bundle.units
    ]
    answered = {
        question_id
        for unit in bundle.units
        if unit.kind != "uncertainty"
        for question_id in unit.question_ids
    }
    missing = tuple(
        item for item in bundle.required_question_ids if item not in answered
    )
    coverage: Literal["complete", "partial", "insufficient"]
    if not answered:
        coverage = "insufficient"
    elif missing:
        coverage = "partial"
    else:
        coverage = "complete"
    return AssembledAnswer(
        coverage=coverage,
        text="\n\n".join(lines),
        units=rendered,
        citations=tuple(
            Citation(
                number=citation_number[passage.passage_id],
                passage_id=passage.passage_id,
                snapshot_id=passage.snapshot_id,
            )
            for passage in ordered_passages
        ),
        missing_question_ids=missing,
    )
