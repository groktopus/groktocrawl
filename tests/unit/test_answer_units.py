"""Application-owned source-bound answer-unit checks for ADR-0080."""

import pytest
from agent.experimental.answer_units import (
    ANSWER_UNIT_SCHEMA,
    AnswerUnit,
    AnswerUnitBundle,
    SourcePassage,
    UnitReview,
    assemble_answer,
    requires_selective_review,
)
from agent.experimental.knowledge import text_digest
from pydantic import ValidationError


def passage(identity: str, text: str = "Exact retained evidence.") -> SourcePassage:
    return SourcePassage(
        passage_id=identity,
        snapshot_id=f"snapshot-{identity}",
        start=0,
        end=len(text),
        quote=text,
        quote_digest=text_digest(text),
    )


def unit(**changes: object) -> AnswerUnit:
    values: dict[str, object] = {
        "unit_id": "unit-1",
        "text": "The source reports the bounded result.",
        "kind": "source_statement",
        "question_ids": ("question-1",),
        "passage_ids": ("passage-1",),
        "qualifiers": ("According to the retained source",),
        "support": "supported",
        "support_reason": "The exact passage states the result.",
        "temporal_scope": "historical",
        "freshness": "historical",
        "disputed": False,
        "high_consequence": False,
    }
    values.update(changes)
    return AnswerUnit(**values)


def bundle(*units: AnswerUnit, passages: tuple[SourcePassage, ...] | None = None) -> AnswerUnitBundle:
    return AnswerUnitBundle(
        schema_version=ANSWER_UNIT_SCHEMA,
        required_question_ids=("question-1", "question-2"),
        passages=passages or (passage("passage-1"), passage("passage-2")),
        units=units,
    )


def test_application_assigns_citations_and_reports_partial_coverage() -> None:
    result = assemble_answer(bundle(unit()))

    assert result.coverage == "partial"
    assert result.missing_question_ids == ("question-2",)
    assert result.text.endswith("[1]")
    assert result.citations[0].passage_id == "passage-1"


@pytest.mark.parametrize(
    "risky",
    [
        unit(kind="inference"),
        unit(passage_ids=("passage-1", "passage-2")),
        unit(disputed=True),
        unit(high_consequence=True),
    ],
)
def test_risky_units_require_a_passing_selective_review(risky: AnswerUnit) -> None:
    candidate = bundle(risky)
    assert requires_selective_review(risky)
    with pytest.raises(ValueError, match="selective review"):
        assemble_answer(candidate)
    with pytest.raises(ValueError, match="selective review"):
        assemble_answer(
            candidate,
            (UnitReview(unit_id=risky.unit_id, verdict="indeterminate", reason="unknown"),),
        )

    assert assemble_answer(
        candidate,
        (UnitReview(unit_id=risky.unit_id, verdict="pass", reason="supported"),),
    ).coverage == "partial"


def test_unknown_model_references_fail_closed() -> None:
    with pytest.raises(ValidationError, match="unknown passage"):
        bundle(unit(passage_ids=("invented-passage",)))
    with pytest.raises(ValidationError, match="unknown question"):
        bundle(unit(question_ids=("invented-question",)))


def test_uncertainty_is_preserved_and_does_not_claim_coverage() -> None:
    uncertain = unit(
        text="The retained evidence is insufficient to answer this question.",
        kind="uncertainty",
        support="insufficient",
        support_reason="The passage states no applicable result.",
        freshness="unknown",
    )
    result = assemble_answer(bundle(uncertain))

    assert result.coverage == "insufficient"
    assert result.missing_question_ids == ("question-1", "question-2")


def test_unknown_or_mismatched_freshness_cannot_publish_as_fact() -> None:
    with pytest.raises(ValidationError, match="unknown freshness"):
        unit(freshness="unknown")
    with pytest.raises(ValidationError, match="temporal scope"):
        unit(temporal_scope="current", freshness="historical")


def test_renderer_cannot_accept_reviews_for_unknown_units() -> None:
    with pytest.raises(ValueError, match="absent or repeated"):
        assemble_answer(
            bundle(unit()),
            (UnitReview(unit_id="invented-unit", verdict="pass", reason="no"),),
        )
