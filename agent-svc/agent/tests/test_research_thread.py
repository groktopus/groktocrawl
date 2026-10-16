from copy import deepcopy
from pathlib import Path

import pytest
from agent.experimental.research_thread import (
    ThreadAnswer,
    build_followup_prompt,
    build_thread_work_order,
    load_thread_experiment_corpus,
    validate_research_thread,
    validate_thread_answer,
)

CASES = (
    Path(__file__).parents[3] / "docs/experiments/research-thread/w12.2-cases.json"
)


def thread_payload() -> dict:
    return {
        "schema_version": "research-thread-experiment/1",
        "scope_id": "scope-a",
        "thread_id": "thread-a",
        "subjects": [
            {
                "subject_id": "product-a",
                "label": "Product A",
                "identifiers": ["vendor.example/product-a"],
            },
            {
                "subject_id": "product-a-cloud",
                "label": "Product A Cloud",
                "identifiers": ["vendor.example/product-a-cloud"],
            },
        ],
        "roots": [
            {
                "root_id": "root-0",
                "sequence": 0,
                "created_at": "2026-09-01T00:00:00Z",
                "subject_ids": ["product-a"],
                "snapshot_ids": ["snapshot-0"],
                "claim_ids": ["claim-0"],
            },
            {
                "root_id": "root-1",
                "sequence": 1,
                "created_at": "2026-09-02T00:00:00Z",
                "subject_ids": ["product-a"],
                "snapshot_ids": ["snapshot-1"],
                "claim_ids": ["claim-1"],
            },
        ],
        "source_lineage": [
            {
                "source_link_id": "source-link-0",
                "prior_snapshot_id": "snapshot-0",
                "current_snapshot_id": "snapshot-1",
                "relationship": "updated",
                "rationale": "same canonical product record with a later version",
            }
        ],
        "claim_lineage": [
            {
                "claim_link_id": "claim-link-0",
                "prior_claim_id": "claim-0",
                "current_claim_id": "claim-1",
                "relationship": "superseded",
                "rationale": "the later release changed the current limit",
            }
        ],
        "recheck_obligations": [
            {
                "obligation_id": "recheck-0",
                "question": "What is the current supported limit?",
                "status": "satisfied",
                "opened_in_root_id": "root-0",
                "resolved_in_root_id": "root-1",
                "evidence_snapshot_ids": ["snapshot-1"],
            }
        ],
    }


def test_valid_thread_preserves_distinct_near_match() -> None:
    thread = validate_research_thread(
        thread_payload(), scope_id="scope-a", thread_id="thread-a"
    )
    assert {subject.subject_id for subject in thread.subjects} == {
        "product-a",
        "product-a-cloud",
    }


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda value: value["roots"][1].update(sequence=2), "append-only"),
        (
            lambda value: value["roots"][1].update(snapshot_ids=["snapshot-0"]),
            "reassign",
        ),
        (
            lambda value: value["source_lineage"][0].update(
                prior_snapshot_id="snapshot-1", current_snapshot_id="snapshot-0"
            ),
            "earlier root",
        ),
        (
            lambda value: value["recheck_obligations"][0].update(
                status="open", resolved_in_root_id="root-1"
            ),
            "satisfied",
        ),
        (
            lambda value: value["roots"][1].update(
                subject_ids=["product-a-unknown"]
            ),
            "unknown subject",
        ),
    ],
)
def test_rejects_broken_thread_lineage(mutation, message: str) -> None:
    payload = deepcopy(thread_payload())
    mutation(payload)
    with pytest.raises(ValueError, match=message):
        validate_research_thread(payload, scope_id="scope-a", thread_id="thread-a")


def test_rejects_caller_identity_mismatch() -> None:
    with pytest.raises(ValueError, match="expected scope"):
        validate_research_thread(
            thread_payload(), scope_id="scope-b", thread_id="thread-a"
        )


def test_frozen_corpus_has_every_longitudinal_stratum() -> None:
    corpus = load_thread_experiment_corpus(CASES)
    assert len(corpus.cases) == 9
    assert {case.stratum for case in corpus.cases} == {
        "changed",
        "terminology",
        "derivative",
        "contradiction",
        "historical",
        "resolved",
        "unavailable",
        "near_match",
        "no_change",
    }


def test_work_order_is_paired_and_repeatable() -> None:
    corpus = load_thread_experiment_corpus(CASES)
    first = build_thread_work_order(corpus, seed=20260919)
    second = build_thread_work_order(corpus, seed=20260919)
    assert first == second
    assert len(first) == 54
    assert {
        (item.case_id, item.repetition, item.arm) for item in first
    } == {
        (case.case_id, repetition, arm)
        for case in corpus.cases
        for repetition in range(1, 4)
        for arm in ("control", "treatment")
    }


def test_answer_citations_are_closed_to_case() -> None:
    case = load_thread_experiment_corpus(CASES).cases[0]
    with pytest.raises(ValueError, match="outside the frozen case"):
        validate_thread_answer(
            {
                "change_events": [],
                "current_truths": [],
                "historical_truths": [],
                "unresolved": ["not established"],
                "citations": ["unknown-snapshot"],
                "answer": "The answer is unresolved.",
            },
            case,
        )


def test_followup_arms_receive_identical_source_snapshots() -> None:
    case = load_thread_experiment_corpus(CASES).cases[0]
    prior = ThreadAnswer(
        change_events=(),
        current_truths=("Earlier state.",),
        historical_truths=(),
        unresolved=(),
        citations=(case.initial_sources[0].snapshot_id,),
        answer="Earlier state.",
    )
    control = build_followup_prompt(case, arm="control", prior_answer=prior)
    treatment = build_followup_prompt(case, arm="treatment", prior_answer=prior)
    assert control["source_snapshots"] == treatment["source_snapshots"]
    assert "research_thread" not in control
    assert "research_thread" in treatment
