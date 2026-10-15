"""Strict W12 Research Mission contract for controlled product experiments.

The contract describes research obligations and limits. It does not grant tool
permissions, authenticate a requester, assert that evidence is true, or authorize
publication. Those boundaries remain owned by their existing components.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Identity = Annotated[str, Field(strict=True, pattern=r"^[a-z0-9][a-z0-9._-]{0,99}$")]
Text = Annotated[str, Field(strict=True, min_length=1, max_length=10_000)]
ShortText = Annotated[str, Field(strict=True, min_length=1, max_length=500)]


class MissionRecord(BaseModel):
    model_config = ConfigDict(
        frozen=True, extra="forbid", revalidate_instances="always"
    )


class ResearchQuestion(MissionRecord):
    question_id: Identity
    text: ShortText
    required: bool = True


class ResearchObligation(MissionRecord):
    obligation_id: Identity
    question_id: Identity
    description: ShortText
    weight: Annotated[int, Field(strict=True, ge=1, le=5)]
    evidence_role: Literal[
        "primary_authority",
        "independent_corroboration",
        "implementation_evidence",
        "counterevidence",
        "context",
    ]
    closure_rule: ShortText


class ScopeBoundary(MissionRecord):
    include: tuple[ShortText, ...] = Field(min_length=1, max_length=20)
    exclude: tuple[ShortText, ...] = Field(default=(), max_length=20)

    @model_validator(mode="after")
    def disjoint(self) -> Self:
        included = {item.casefold().strip() for item in self.include}
        excluded = {item.casefold().strip() for item in self.exclude}
        if included & excluded:
            raise ValueError("mission scope cannot both include and exclude an item")
        return self


class SourcePolicy(MissionRecord):
    allowed_authority: tuple[Literal["primary", "official", "independent"], ...] = (
        "primary",
        "official",
        "independent",
    )
    minimum_independent_publishers: Annotated[int, Field(strict=True, ge=0, le=5)]
    derivative_sources_count_as_independent: Literal[False] = False
    inaccessible_sources_can_close_obligations: Literal[False] = False


class FreshnessPolicy(MissionRecord):
    mode: Literal["current", "as_of", "historical", "not_applicable"]
    as_of: datetime | None = None
    rationale: ShortText

    @field_validator("as_of")
    @classmethod
    def require_utc(cls, value: datetime | None) -> datetime | None:
        if value is not None:
            offset = value.utcoffset()
            if offset is None or offset.total_seconds() != 0:
                raise ValueError("mission as_of must have an explicit UTC offset")
        return value

    @model_validator(mode="after")
    def mode_matches_time(self) -> Self:
        if (self.mode == "as_of") != (self.as_of is not None):
            raise ValueError("as_of timestamp is required only for as_of missions")
        return self


class ResearchBudget(MissionRecord):
    max_searches: Annotated[int, Field(strict=True, ge=1, le=4)]
    max_sources: Annotated[int, Field(strict=True, ge=1, le=10)]
    max_model_calls: Annotated[int, Field(strict=True, ge=1, le=6)]
    max_elapsed_seconds: Annotated[int, Field(strict=True, ge=1, le=300)]


class ClarificationRule(MissionRecord):
    clarification_id: Identity
    question: ShortText
    trigger: ShortText
    blocking: bool


class ResearchMission(MissionRecord):
    """Caller-reviewed research boundary, never a permission or truth oracle."""

    schema_version: Literal["research-mission/1"]
    mission_id: Identity
    decision: Text
    audience: ShortText
    scope: ScopeBoundary
    questions: tuple[ResearchQuestion, ...] = Field(min_length=1, max_length=20)
    obligations: tuple[ResearchObligation, ...] = Field(min_length=1, max_length=50)
    source_policy: SourcePolicy
    freshness: FreshnessPolicy
    contradiction_policy: Literal["preserve_and_report"]
    acceptable_uncertainty: ShortText
    clarifications: tuple[ClarificationRule, ...] = Field(default=(), max_length=10)
    budget: ResearchBudget
    stop_when: tuple[ShortText, ...] = Field(min_length=1, max_length=10)
    abstain_when: tuple[ShortText, ...] = Field(min_length=1, max_length=10)

    @model_validator(mode="after")
    def structural_integrity(self) -> Self:
        question_ids = tuple(item.question_id for item in self.questions)
        obligation_ids = tuple(item.obligation_id for item in self.obligations)
        clarification_ids = tuple(item.clarification_id for item in self.clarifications)
        all_ids = question_ids + obligation_ids + clarification_ids
        if len(all_ids) != len(set(all_ids)):
            raise ValueError("mission identities must be unique")
        available_questions = set(question_ids)
        if any(
            item.question_id not in available_questions for item in self.obligations
        ):
            raise ValueError("every obligation must reference a mission question")
        required_questions = {
            item.question_id for item in self.questions if item.required
        }
        covered_questions = {item.question_id for item in self.obligations}
        if required_questions - covered_questions:
            raise ValueError("every required question needs an evidence obligation")
        if self.source_policy.minimum_independent_publishers > self.budget.max_sources:
            raise ValueError("independence requirement exceeds the source budget")
        return self


def validate_research_mission(payload: object) -> ResearchMission:
    """Validate an untrusted mission without granting any execution authority."""

    return ResearchMission.model_validate(payload)


def render_control_brief(mission: ResearchMission) -> str:
    """Render equivalent mission semantics as prose for the matched control arm."""

    lines = [
        f"Decision: {mission.decision}",
        f"Audience: {mission.audience}",
        "Include: " + "; ".join(mission.scope.include),
        "Exclude: " + ("; ".join(mission.scope.exclude) or "none declared"),
        "Questions:",
    ]
    lines.extend(f"- {item.text}" for item in mission.questions)
    lines.append("Evidence obligations:")
    lines.extend(
        f"- {item.description} [role={item.evidence_role}; weight={item.weight}; "
        f"closure={item.closure_rule}]"
        for item in mission.obligations
    )
    lines.extend(
        [
            "Allowed source authority: "
            + ", ".join(mission.source_policy.allowed_authority),
            "Minimum independent publishers: "
            + str(mission.source_policy.minimum_independent_publishers),
            "Derivative sources do not count as independent.",
            "Inaccessible sources cannot close obligations.",
            f"Freshness: {mission.freshness.mode}; {mission.freshness.rationale}",
            "Contradictions: preserve and report them.",
            f"Acceptable uncertainty: {mission.acceptable_uncertainty}",
        ]
    )
    if mission.freshness.as_of is not None:
        lines.append(f"As of: {mission.freshness.as_of.isoformat()}")
    if mission.clarifications:
        lines.append("Clarifications:")
        lines.extend(
            f"- Ask '{item.question}' when {item.trigger}; "
            f"blocking={'yes' if item.blocking else 'no'}"
            for item in mission.clarifications
        )
    lines.extend(
        [
            "Budget: "
            f"{mission.budget.max_searches} searches, "
            f"{mission.budget.max_sources} sources, "
            f"{mission.budget.max_model_calls} model calls, "
            f"{mission.budget.max_elapsed_seconds} seconds.",
            "Stop when: " + "; ".join(mission.stop_when),
            "Abstain when: " + "; ".join(mission.abstain_when),
        ]
    )
    return "\n".join(lines)


class MissionExperimentCase(MissionRecord):
    case_id: Identity
    stratum: Literal[
        "straightforward",
        "ambiguous",
        "compound",
        "temporal",
        "contradictory_source",
        "unanswerable",
    ]
    raw_request: Text
    source_ids: tuple[Identity, ...] = Field(min_length=1, max_length=20)
    reference_mission: ResearchMission
    expected_intake: Literal["accept", "clarify", "abstain"]
    prohibited_assumptions: tuple[ShortText, ...] = Field(default=(), max_length=20)

    @model_validator(mode="after")
    def identity_and_sources(self) -> Self:
        if self.case_id != self.reference_mission.mission_id:
            raise ValueError("case and reference mission identities must match")
        if len(self.source_ids) != len(set(self.source_ids)):
            raise ValueError("case source identities must be unique")
        return self


class MissionExperimentCorpus(MissionRecord):
    schema_version: Literal["research-mission-experiment-corpus/1"]
    domain: Literal["agentic engineering software factory in the enterprise"]
    source_corpus_path: Literal["docs/experiments/enterprise-evaluation/corpus.json"]
    source_corpus_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    cases: tuple[MissionExperimentCase, ...] = Field(min_length=12, max_length=12)

    @model_validator(mode="after")
    def matched_strata(self) -> Self:
        case_ids = tuple(item.case_id for item in self.cases)
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("experiment case identities must be unique")
        counts: dict[str, int] = {}
        for item in self.cases:
            counts[item.stratum] = counts.get(item.stratum, 0) + 1
        if set(counts.values()) != {2} or len(counts) != 6:
            raise ValueError("experiment requires exactly two cases in each stratum")
        return self


def load_mission_experiment_corpus(
    path: Path, *, source_corpus_path: Path
) -> MissionExperimentCorpus:
    """Load the frozen case file and close every source reference."""

    payload = json.loads(path.read_bytes())
    corpus = MissionExperimentCorpus.model_validate(payload)
    source_bytes = source_corpus_path.read_bytes()
    source_digest = hashlib.sha256(source_bytes).hexdigest()
    if source_digest != corpus.source_corpus_sha256:
        raise ValueError("source corpus digest differs from the frozen mission corpus")
    source_payload = json.loads(source_bytes)
    source_ids = frozenset(item["source_id"] for item in source_payload["sources"])
    referenced = {source_id for item in corpus.cases for source_id in item.source_ids}
    missing = referenced - source_ids
    if missing:
        raise ValueError(
            f"case corpus references unavailable sources: {sorted(missing)}"
        )
    return corpus
