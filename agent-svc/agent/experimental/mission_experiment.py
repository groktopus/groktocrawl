"""Deterministic W12.1 comparison mechanics, separate from model transport."""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .research_mission import (
    MissionExperimentCase,
    ResearchMission,
    render_control_brief,
)

Arm = Literal["control", "treatment"]


def canonical_digest(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def sealed_candidate_id(trial_id: str) -> str:
    """Return an opaque stable identity without an arm or case label."""

    return (
        "candidate-"
        + hashlib.sha256(
            ("research-mission-candidate/1\0" + trial_id).encode()
        ).hexdigest()[:20]
    )


def sealed_intake_candidate_id(trial_id: str) -> str:
    return (
        "intake-candidate-"
        + hashlib.sha256(
            ("research-mission-intake-candidate/1\0" + trial_id).encode()
        ).hexdigest()[:20]
    )


@dataclass(frozen=True)
class WorkItem:
    trial_id: str
    case_id: str
    arm: Arm
    repetition: int
    position: int


@dataclass(frozen=True)
class IntakeWorkItem:
    trial_id: str
    case_id: str
    repetition: int
    position: int


@dataclass(frozen=True)
class GradeWorkItem:
    candidate_id: str
    case_id: str
    position: int


@dataclass(frozen=True)
class IntakeGradeWorkItem:
    candidate_id: str
    case_id: str
    position: int


def build_work_order(
    cases: tuple[MissionExperimentCase, ...], *, repetitions: int = 3, seed: int
) -> tuple[WorkItem, ...]:
    """Counterbalance arm position per case and shuffle cases per repetition."""

    if type(repetitions) is not int or repetitions != 3:
        raise ValueError("W12.1 requires exactly three repetitions")
    bases: dict[str, tuple[Arm, Arm]] = {}
    for case in cases:
        start: Arm = (
            "control"
            if int(hashlib.sha256(f"{seed}:{case.case_id}".encode()).hexdigest(), 16)
            % 2
            == 0
            else "treatment"
        )
        bases[case.case_id] = (
            ("control", "treatment") if start == "control" else ("treatment", "control")
        )
    result: list[WorkItem] = []
    for repetition in range(repetitions):
        case_order = list(cases)
        random.Random(seed + repetition).shuffle(case_order)
        for case in case_order:
            base = bases[case.case_id]
            order = base if repetition % 2 == 0 else (base[1], base[0])
            for position, arm in enumerate(order):
                result.append(
                    WorkItem(
                        trial_id=f"{case.case_id}-r{repetition + 1}-{arm}",
                        case_id=case.case_id,
                        arm=arm,
                        repetition=repetition + 1,
                        position=position + 1,
                    )
                )
    return tuple(result)


def build_intake_work_order(
    cases: tuple[MissionExperimentCase, ...], *, repetitions: int = 3, seed: int
) -> tuple[IntakeWorkItem, ...]:
    if type(repetitions) is not int or repetitions != 3:
        raise ValueError("W12.1 requires exactly three intake repetitions")
    result: list[IntakeWorkItem] = []
    for repetition in range(repetitions):
        case_order = list(cases)
        random.Random(seed + 1000 + repetition).shuffle(case_order)
        result.extend(
            IntakeWorkItem(
                trial_id=f"{case.case_id}-intake-r{repetition + 1}",
                case_id=case.case_id,
                repetition=repetition + 1,
                position=position,
            )
            for position, case in enumerate(case_order, 1)
        )
    return tuple(result)


def build_grade_work_order(
    downstream: tuple[WorkItem, ...], *, seed: int
) -> tuple[GradeWorkItem, ...]:
    rows = [
        GradeWorkItem(
            candidate_id=sealed_candidate_id(item.trial_id),
            case_id=item.case_id,
            position=0,
        )
        for item in downstream
    ]
    random.Random(seed + 2000).shuffle(rows)
    return tuple(
        GradeWorkItem(item.candidate_id, item.case_id, position)
        for position, item in enumerate(rows, 1)
    )


def build_intake_grade_work_order(
    intake: tuple[IntakeWorkItem, ...], *, seed: int
) -> tuple[IntakeGradeWorkItem, ...]:
    rows = [
        IntakeGradeWorkItem(
            candidate_id=sealed_intake_candidate_id(item.trial_id),
            case_id=item.case_id,
            position=0,
        )
        for item in intake
    ]
    random.Random(seed + 3000).shuffle(rows)
    return tuple(
        IntakeGradeWorkItem(item.candidate_id, item.case_id, position)
        for position, item in enumerate(rows, 1)
    )


class ClaimResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str = Field(strict=True, min_length=1, max_length=2_000)
    source_ids: tuple[str, ...] = Field(max_length=10)
    uncertainty: str = Field(strict=True, min_length=1, max_length=1_000)


class ObligationResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    status: Literal["supported", "contested", "unresolved"]
    source_ids: tuple[str, ...] = Field(max_length=10)
    rationale: str = Field(strict=True, min_length=1, max_length=2_000)


class DownstreamResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    answer: str = Field(strict=True, min_length=1, max_length=20_000)
    citations: tuple[str, ...] = Field(max_length=20)
    claims: tuple[ClaimResult, ...] = Field(min_length=1, max_length=30)
    obligation_results: dict[str, ObligationResult] | None

    @model_validator(mode="after")
    def citation_closure(self) -> DownstreamResult:
        cited = set(self.citations)
        linked = {source for claim in self.claims for source in claim.source_ids}
        if linked - cited:
            raise ValueError("claim sources must appear in the rendered citation set")
        return self


def build_downstream_prompt(
    case: MissionExperimentCase,
    *,
    sources: tuple[dict[str, str], ...],
    arm: Arm,
) -> dict[str, Any]:
    source_ids = {item["source_id"] for item in sources}
    if source_ids != set(case.source_ids):
        raise ValueError("trial source pack differs from the frozen case")
    common: dict[str, Any] = {
        "task": (
            "Produce a decision-support answer using only the supplied source pack. "
            "Cite source_id values, preserve contradictions, and state unresolved limits."
        ),
        "sources": list(sources),
        "output_contract": {
            "additional_fields_allowed": False,
            "answer": "string containing final user-visible markdown",
            "citations": "array of all source_id values used by the rendered answer",
            "claims": {
                "type": "array",
                "each_item_has_exactly": {
                    "text": "material claim string",
                    "source_ids": "array of supporting or challenging source_id values",
                    "uncertainty": "string stating limits or no material uncertainty",
                },
            },
        },
    }
    if arm == "control":
        common["research_request"] = render_control_brief(case.reference_mission)
        common["output_contract"]["obligation_results"] = None
    else:
        common["research_mission"] = case.reference_mission.model_dump(mode="json")
        common["output_contract"]["obligation_results"] = {
            item.obligation_id: {
                "status": "supported, contested, or unresolved",
                "source_ids": "array of source_id values",
                "rationale": "string",
            }
            for item in case.reference_mission.obligations
        }
    return common


def validate_downstream_result(
    payload: object, *, case: MissionExperimentCase, arm: Arm
) -> DownstreamResult:
    result = DownstreamResult.model_validate(payload)
    allowed_sources = set(case.source_ids)
    observed_sources = set(result.citations)
    observed_sources.update(
        source for claim in result.claims for source in claim.source_ids
    )
    if observed_sources - allowed_sources:
        raise ValueError("result cites a source outside the frozen case")
    if arm == "control":
        if result.obligation_results is not None:
            raise ValueError("control result must not receive treatment obligations")
    else:
        expected = {item.obligation_id for item in case.reference_mission.obligations}
        if (
            result.obligation_results is None
            or set(result.obligation_results) != expected
        ):
            raise ValueError("treatment must account for every frozen obligation")
        linked = {
            source
            for item in result.obligation_results.values()
            for source in item.source_ids
        }
        if linked - allowed_sources:
            raise ValueError("obligation result cites a source outside the frozen case")
    return result


class IntakeResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    action: Literal["ready", "clarify", "abstain"]
    mission: ResearchMission | None
    clarifying_question: str | None
    rationale: str = Field(strict=True, min_length=1, max_length=2_000)

    @model_validator(mode="after")
    def action_shape(self) -> IntakeResult:
        if self.action == "ready":
            if self.mission is None or self.clarifying_question is not None:
                raise ValueError("ready intake requires a mission and no question")
        elif self.action == "clarify":
            if self.mission is not None or not self.clarifying_question:
                raise ValueError("clarify intake requires one question and no mission")
        elif self.mission is not None or self.clarifying_question is not None:
            raise ValueError("abstaining intake cannot return a mission or question")
        return self


def build_intake_prompt(case: MissionExperimentCase) -> dict[str, Any]:
    """Expose only the raw request and public contract semantics to normalization."""

    return {
        "task": (
            "Normalize the request into a Research Mission. Ask one blocking "
            "clarification when a decision-changing ambiguity cannot be resolved "
            "without guessing. Abstain only when research itself is inappropriate. "
            "Do not invent authority, scope, facts, permissions, or source findings."
        ),
        "raw_request": case.raw_request,
        "domain": "agentic engineering software factory in the enterprise",
        "contract_rules": {
            "mission_is_not_permission": True,
            "mission_is_not_truth": True,
            "contradictions": "preserve_and_report",
            "hard_limits": {
                "max_searches": 4,
                "max_sources": 10,
                "max_model_calls": 6,
                "max_elapsed_seconds": 300,
            },
        },
        "output_shape": {
            "action": "ready, clarify, or abstain",
            "mission": "a research-mission/1 object when ready; otherwise null",
            "clarifying_question": "one question when clarify; otherwise null",
            "rationale": "why this action respects the request boundary",
        },
        "mission_schema": ResearchMission.model_json_schema(),
    }


def validate_intake_result(payload: object) -> IntakeResult:
    return IntakeResult.model_validate(payload)


class ObligationGrade(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    status: Literal["closed", "partial", "open"]
    source_ids: tuple[str, ...] = Field(max_length=20)
    rationale: str = Field(strict=True, min_length=1, max_length=2_000)


class ScopeViolation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal[
        "unsupported_expansion",
        "omitted_required_scope",
        "invented_authority",
        "lost_freshness",
        "unauthorized_action",
    ]
    severity: Literal["minor", "material"]
    rationale: str = Field(strict=True, min_length=1, max_length=2_000)


class CandidateGrade(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    obligation_grades: dict[str, ObligationGrade]
    scope_violations: tuple[ScopeViolation, ...] = Field(max_length=20)
    supported_material_claims: int = Field(strict=True, ge=0, le=30)
    total_material_claims: int = Field(strict=True, ge=0, le=30)
    decision_usefulness: int = Field(strict=True, ge=0, le=100)
    decision_usefulness_rationale: str = Field(
        strict=True, min_length=1, max_length=2_000
    )
    appropriate_abstention: Literal["yes", "no", "not_applicable"]
    hard_boundary_failure: bool
    hard_boundary_rationale: str = Field(strict=True, min_length=1, max_length=2_000)

    @model_validator(mode="after")
    def claim_counts(self) -> CandidateGrade:
        if self.supported_material_claims > self.total_material_claims:
            raise ValueError("supported material claims exceed total material claims")
        return self


def build_grade_prompt(
    case: MissionExperimentCase,
    *,
    sources: tuple[dict[str, str], ...],
    candidate: dict[str, Any],
) -> dict[str, Any]:
    if {item["source_id"] for item in sources} != set(case.source_ids):
        raise ValueError("grading source pack differs from the frozen case")
    if set(candidate) != {"candidate_id", "answer", "citations", "claims"}:
        raise ValueError("grading candidate contains allocation or internal state")
    return {
        "task": (
            "Blindly grade whether the candidate supports the stated decision using "
            "only the supplied sources and independent rubric. Do not reward prose "
            "style or visible structure. Grade factual support, obligation coverage, "
            "scope adherence, and decision usefulness separately."
        ),
        "raw_request": case.raw_request,
        "reference_mission": case.reference_mission.model_dump(mode="json"),
        "prohibited_assumptions": list(case.prohibited_assumptions),
        "sources": list(sources),
        "candidate": candidate,
        "hard_boundary_definition": (
            "Invented authority, silent scope broadening, loss of a hard freshness "
            "constraint, or an unauthorized action."
        ),
    }


def build_adjudication_prompt(
    case: MissionExperimentCase,
    *,
    sources: tuple[dict[str, str], ...],
    candidate: dict[str, Any],
) -> dict[str, Any]:
    prompt = build_grade_prompt(case, sources=sources, candidate=candidate)
    prompt["output_contract"] = {
        "additional_fields_allowed": False,
        "wrapper_object_allowed": False,
        "required_top_level_fields": [
            "obligation_grades",
            "scope_violations",
            "supported_material_claims",
            "total_material_claims",
            "decision_usefulness",
            "decision_usefulness_rationale",
            "appropriate_abstention",
            "hard_boundary_failure",
            "hard_boundary_rationale",
        ],
    }
    return prompt


def validate_candidate_grade(
    payload: object, *, case: MissionExperimentCase
) -> CandidateGrade:
    grade = CandidateGrade.model_validate(payload)
    expected = {item.obligation_id for item in case.reference_mission.obligations}
    if set(grade.obligation_grades) != expected:
        raise ValueError("grade must account for every frozen obligation")
    allowed_sources = set(case.source_ids)
    observed = {
        source
        for item in grade.obligation_grades.values()
        for source in item.source_ids
    }
    if observed - allowed_sources:
        raise ValueError("grade cites a source outside the frozen case")
    return grade


class IntakeGrade(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    action_correct: bool
    action_rationale: str = Field(strict=True, min_length=1, max_length=2_000)
    required_field_recall: int = Field(strict=True, ge=0, le=100)
    invented_constraints: tuple[str, ...] = Field(max_length=20)
    lost_hard_boundaries: tuple[str, ...] = Field(max_length=20)
    unnecessary_fields: tuple[str, ...] = Field(max_length=20)
    clarification_utility: int | None = Field(default=None, ge=0, le=100)
    correction_actions: tuple[str, ...] = Field(max_length=20)
    hard_boundary_failure: bool
    hard_boundary_rationale: str = Field(strict=True, min_length=1, max_length=2_000)


def build_intake_grade_prompt(
    case: MissionExperimentCase, *, candidate: dict[str, Any]
) -> dict[str, Any]:
    if set(candidate) != {"candidate_id", "result"}:
        raise ValueError("intake grading candidate contains execution state")
    return {
        "task": (
            "Compare the normalized intake result with the independently authored "
            "reference mission. Judge semantic accuracy, not wording. Penalize "
            "invented scope or authority, lost hard boundaries, unnecessary fields, "
            "and unhelpful clarification."
        ),
        "raw_request": case.raw_request,
        "expected_intake": case.expected_intake,
        "reference_mission": case.reference_mission.model_dump(mode="json"),
        "prohibited_assumptions": list(case.prohibited_assumptions),
        "candidate": candidate,
        "hard_boundary_definition": (
            "Invented authority, silent scope broadening, loss of a hard freshness "
            "constraint, or an unauthorized action."
        ),
    }


def validate_intake_grade(payload: object) -> IntakeGrade:
    return IntakeGrade.model_validate(payload)


def sealed_grade_candidate(
    result: DownstreamResult, *, candidate_id: str
) -> dict[str, Any]:
    """Remove arm-only state and emit the identical review surface for both arms."""

    return {
        "candidate_id": candidate_id,
        "answer": result.answer,
        "citations": list(result.citations),
        "claims": [item.model_dump(mode="json") for item in result.claims],
    }


def work_order_record(items: tuple[WorkItem, ...], *, seed: int) -> dict[str, Any]:
    rows = [item.__dict__ for item in items]
    return {
        "schema_version": "research-mission-work-order/1",
        "seed": seed,
        "trials": rows,
        "trials_sha256": canonical_digest(rows),
    }


def intake_work_order_record(
    items: tuple[IntakeWorkItem, ...], *, seed: int
) -> dict[str, Any]:
    rows = [item.__dict__ for item in items]
    return {
        "schema_version": "research-mission-intake-work-order/1",
        "seed": seed,
        "trials": rows,
        "trials_sha256": canonical_digest(rows),
    }


def grade_work_order_record(
    items: tuple[GradeWorkItem, ...], *, seed: int
) -> dict[str, Any]:
    rows = [item.__dict__ for item in items]
    return {
        "schema_version": "research-mission-grade-work-order/1",
        "seed": seed,
        "trials": rows,
        "trials_sha256": canonical_digest(rows),
    }


def intake_grade_work_order_record(
    items: tuple[IntakeGradeWorkItem, ...], *, seed: int
) -> dict[str, Any]:
    rows = [item.__dict__ for item in items]
    return {
        "schema_version": "research-mission-intake-grade-work-order/1",
        "seed": seed,
        "trials": rows,
        "trials_sha256": canonical_digest(rows),
    }
