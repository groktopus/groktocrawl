import json
from pathlib import Path

import pytest
from agent.experimental.mission_experiment import (
    build_downstream_prompt,
    build_grade_prompt,
    build_grade_work_order,
    build_intake_grade_prompt,
    build_intake_grade_work_order,
    build_intake_prompt,
    build_intake_work_order,
    build_work_order,
    grade_work_order_record,
    intake_grade_work_order_record,
    intake_work_order_record,
    sealed_candidate_id,
    sealed_grade_candidate,
    sealed_intake_candidate_id,
    validate_candidate_grade,
    validate_downstream_result,
    validate_intake_grade,
    validate_intake_result,
    work_order_record,
)
from agent.experimental.research_mission import load_mission_experiment_corpus


def corpus():
    return load_mission_experiment_corpus(
        Path("docs/experiments/research-mission/w12.1-cases.json"),
        source_corpus_path=Path("docs/experiments/enterprise-evaluation/corpus.json"),
    )


def source_pack(case):
    source_payload = json.loads(
        Path("docs/experiments/enterprise-evaluation/corpus.json").read_bytes()
    )
    by_id = {item["source_id"]: item for item in source_payload["sources"]}
    return tuple(
        {
            "source_id": source_id,
            "title": by_id[source_id]["title"],
            "text": by_id[source_id]["text"],
        }
        for source_id in case.source_ids
    )


def result(case, arm):
    payload = {
        "answer": "The evidence supports a bounded answer [delivery-policy].",
        "citations": [case.source_ids[0]],
        "claims": [
            {
                "text": "A bounded claim.",
                "source_ids": [case.source_ids[0]],
                "uncertainty": "No uncertainty beyond the supplied fixture.",
            }
        ],
        "obligation_results": None,
    }
    if arm == "treatment":
        payload["obligation_results"] = {
            item.obligation_id: {
                "status": "supported",
                "source_ids": [case.source_ids[0]],
                "rationale": "The source directly addresses the obligation.",
            }
            for item in case.reference_mission.obligations
        }
    return payload


def test_work_order_is_reproducible_complete_and_counterbalanced():
    cases = corpus().cases
    first = build_work_order(cases, seed=20260919)
    assert first == build_work_order(cases, seed=20260919)
    assert len(first) == 72
    assert len({item.trial_id for item in first}) == 72
    for case in cases:
        rows = [item for item in first if item.case_id == case.case_id]
        assert {item.arm for item in rows} == {"control", "treatment"}
        assert len(rows) == 6
        assert [item.arm for item in rows[:2]] == [item.arm for item in rows[4:6]]
        assert [item.arm for item in rows[:2]] == list(
            reversed([item.arm for item in rows[2:4]])
        )
    assert work_order_record(first, seed=20260919) == work_order_record(
        first, seed=20260919
    )


def test_intake_work_order_is_reproducible_and_complete():
    cases = corpus().cases
    first = build_intake_work_order(cases, seed=20260919)
    assert first == build_intake_work_order(cases, seed=20260919)
    assert len(first) == 36
    assert len({item.trial_id for item in first}) == 36
    for case in cases:
        assert sum(item.case_id == case.case_id for item in first) == 3
    assert intake_work_order_record(first, seed=20260919)["seed"] == 20260919


def test_grade_work_order_is_blinded_reproducible_and_complete():
    downstream = build_work_order(corpus().cases, seed=20260919)
    grades = build_grade_work_order(downstream, seed=20260919)
    assert grades == build_grade_work_order(downstream, seed=20260919)
    assert len(grades) == 72
    assert len({item.candidate_id for item in grades}) == 72
    encoded = json.dumps(grade_work_order_record(grades, seed=20260919))
    assert "control" not in encoded
    assert "treatment" not in encoded
    assert "trial_id" not in encoded


def test_intake_grade_work_order_is_opaque_and_complete():
    intake = build_intake_work_order(corpus().cases, seed=20260919)
    grades = build_intake_grade_work_order(intake, seed=20260919)
    assert len(grades) == 36
    assert len({item.candidate_id for item in grades}) == 36
    expected_ids = {sealed_intake_candidate_id(item.trial_id) for item in intake}
    assert {item.candidate_id for item in grades} == expected_ids
    assert intake_grade_work_order_record(grades, seed=20260919)["seed"] == 20260919


def test_prompts_hold_sources_constant_but_isolate_contract_shape():
    case = corpus().cases[0]
    sources = source_pack(case)
    control = build_downstream_prompt(case, sources=sources, arm="control")
    treatment = build_downstream_prompt(case, sources=sources, arm="treatment")
    assert control["sources"] == treatment["sources"]
    assert "research_request" in control
    assert "research_mission" not in control
    assert "research_mission" in treatment
    assert "research_request" not in treatment


def test_prompt_rejects_a_changed_source_pack():
    case = corpus().cases[0]
    with pytest.raises(ValueError, match="differs from the frozen case"):
        build_downstream_prompt(case, sources=(), arm="control")


@pytest.mark.parametrize("arm", ["control", "treatment"])
def test_result_validation_and_blinding(arm):
    case = corpus().cases[0]
    validated = validate_downstream_result(result(case, arm), case=case, arm=arm)
    sealed = sealed_grade_candidate(validated, candidate_id="sealed-1")
    assert set(sealed) == {"candidate_id", "answer", "citations", "claims"}
    assert "obligation_results" not in sealed
    assert arm not in json.dumps(sealed)


def test_sealed_candidate_identity_does_not_reveal_arm_or_case():
    control = sealed_candidate_id("mission-straightforward-delivery-r1-control")
    treatment = sealed_candidate_id("mission-straightforward-delivery-r1-treatment")
    assert control != treatment
    assert control.startswith("candidate-")
    assert "control" not in control
    assert "treatment" not in treatment
    assert "delivery" not in control


def test_control_cannot_receive_treatment_obligations():
    case = corpus().cases[0]
    with pytest.raises(ValueError, match="control result"):
        validate_downstream_result(result(case, "treatment"), case=case, arm="control")


def test_treatment_must_close_the_exact_obligation_identity_set():
    case = corpus().cases[0]
    payload = result(case, "treatment")
    payload["obligation_results"]["invented"] = payload["obligation_results"].pop("o1")
    with pytest.raises(ValueError, match="every frozen obligation"):
        validate_downstream_result(payload, case=case, arm="treatment")


def test_results_cannot_cite_out_of_packet_sources():
    case = corpus().cases[0]
    payload = result(case, "control")
    payload["citations"] = ["outside-source"]
    payload["claims"][0]["source_ids"] = ["outside-source"]
    with pytest.raises(ValueError, match="outside the frozen case"):
        validate_downstream_result(payload, case=case, arm="control")


def test_intake_prompt_contains_no_reference_answer_or_source_pack():
    case = corpus().cases[2]
    prompt = build_intake_prompt(case)
    encoded = json.dumps(prompt)
    assert prompt["raw_request"] == case.raw_request
    assert "reference_mission" not in encoded
    assert "source_ids" not in encoded
    assert case.reference_mission.decision not in encoded


def test_intake_result_enforces_action_shape():
    case = corpus().cases[0]
    clarified = validate_intake_result(
        {
            "action": "clarify",
            "mission": None,
            "clarifying_question": "Which target environment do you mean?",
            "rationale": "The target changes the authorization boundary.",
        }
    )
    assert clarified.action == "clarify"
    with pytest.raises(ValueError, match="requires one question"):
        validate_intake_result(
            {
                "action": "clarify",
                "mission": case.reference_mission.model_dump(mode="json"),
                "clarifying_question": None,
                "rationale": "Invalid mixed response.",
            }
        )


def test_grade_prompt_is_blind_and_grade_closes_obligations():
    case = corpus().cases[0]
    candidate = sealed_grade_candidate(
        validate_downstream_result(result(case, "control"), case=case, arm="control"),
        candidate_id="candidate-opaque",
    )
    prompt = build_grade_prompt(case, sources=source_pack(case), candidate=candidate)
    encoded = json.dumps(prompt)
    assert "control" not in encoded
    assert "treatment" not in encoded
    grade = validate_candidate_grade(
        {
            "obligation_grades": {
                item.obligation_id: {
                    "status": "closed",
                    "source_ids": [case.source_ids[0]],
                    "rationale": "The evidence supports the required point.",
                }
                for item in case.reference_mission.obligations
            },
            "scope_violations": [],
            "supported_material_claims": 1,
            "total_material_claims": 1,
            "decision_usefulness": 90,
            "decision_usefulness_rationale": "The answer supports the decision.",
            "appropriate_abstention": "not_applicable",
            "hard_boundary_failure": False,
            "hard_boundary_rationale": "No hard boundary failure was found.",
        },
        case=case,
    )
    assert grade.decision_usefulness == 90


def test_intake_grade_prompt_and_contract_are_bounded():
    case = corpus().cases[2]
    candidate = {
        "candidate_id": "intake-candidate-opaque",
        "result": {
            "action": "clarify",
            "mission": None,
            "clarifying_question": "Which environment do you mean?",
            "rationale": "The authorization changes by environment.",
        },
    }
    prompt = build_intake_grade_prompt(case, candidate=candidate)
    assert prompt["expected_intake"] == "clarify"
    grade = validate_intake_grade(
        {
            "action_correct": True,
            "action_rationale": "The ambiguity is decision changing.",
            "required_field_recall": 100,
            "invented_constraints": [],
            "lost_hard_boundaries": [],
            "unnecessary_fields": [],
            "clarification_utility": 95,
            "correction_actions": [],
            "hard_boundary_failure": False,
            "hard_boundary_rationale": "No boundary was crossed.",
        }
    )
    assert grade.action_correct
