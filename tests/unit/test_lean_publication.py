"""Fail-closed publication admission for the lean successor."""

import json
from dataclasses import replace

import pytest
from agent.experimental.answer_units import AssembledAnswer
from agent.experimental.lean_construction import RequiredQuestion
from agent.experimental.lean_journey import LeanJourneyResult, run_lean_journey
from agent.experimental.lean_publication import prepare_lean_publication
from agent.experimental.model_review import ModelReply, ReviewRequest
from agent.experimental.passage_preparation import (
    RetainedSourceText,
    prepare_source_passages,
)


def source(text: str = "The measured result was 42.") -> RetainedSourceText:
    return RetainedSourceText("snapshot-1", text)


async def completed_result() -> tuple[
    tuple[RetainedSourceText, ...], LeanJourneyResult
]:
    async def complete(_: ReviewRequest) -> ModelReply:
        return ModelReply(
            json.dumps(
                {
                    "schema_version": "source-bound-selection/1",
                    "units": [
                        {
                            "text": "The measured result was 42.",
                            "kind": "source_statement",
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
            ).encode(),
            "local-model",
            100,
            25,
        )

    retained = (source(),)
    result = await run_lean_journey(
        "What was measured?",
        (RequiredQuestion(question_id="question-1", text="What was measured?"),),
        prepare_source_passages(retained),
        complete=complete,
    )
    return retained, result


@pytest.mark.asyncio
async def test_exact_journey_is_admitted_as_development_artifact() -> None:
    retained, result = await completed_result()

    publication = prepare_lean_publication(result, retained)

    assert publication.status == "eligible_development_artifact"
    assert publication.provider_calls == 1
    assert publication.policy_version == "lean-evidence-first/1"
    assert publication.citations[0].snapshot_id == "snapshot-1"


@pytest.mark.asyncio
async def test_changed_retained_source_denies_publication() -> None:
    _, result = await completed_result()

    with pytest.raises(ValueError, match="exact retained sources"):
        prepare_lean_publication(result, (source("The result was changed."),))


@pytest.mark.asyncio
async def test_changed_rendered_answer_denies_publication() -> None:
    retained, result = await completed_result()
    changed = result.answer.model_copy(
        update={"text": result.answer.text + " Unverified addition."}
    )

    with pytest.raises(ValueError, match="deterministic assembly"):
        prepare_lean_publication(
            replace(result, answer=AssembledAnswer.model_validate(changed)),
            retained,
        )


@pytest.mark.asyncio
async def test_changed_call_count_denies_publication() -> None:
    retained, result = await completed_result()

    with pytest.raises(ValueError, match="provider calls"):
        prepare_lean_publication(replace(result, provider_calls=2), retained)
