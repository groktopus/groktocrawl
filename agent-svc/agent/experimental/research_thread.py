"""Bounded longitudinal research-thread records; never an authority for truth."""

from __future__ import annotations

import json
import random
from datetime import datetime
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from .knowledge import Identity, Record, Text

THREAD_SCHEMA = "research-thread-experiment/1"


class ThreadExperimentSource(Record):
    snapshot_id: Identity
    subject_id: Identity
    lineage_id: Identity
    title: Text
    text: Text
    source_role: Literal["primary", "independent", "derivative", "unavailable"]


class ExpectedThreadOutcome(Record):
    change_events: tuple[Text, ...] = Field(max_length=20)
    current_truths: tuple[Text, ...] = Field(max_length=20)
    historical_truths: tuple[Text, ...] = Field(max_length=20)
    unresolved: tuple[Text, ...] = Field(max_length=20)
    prohibited_merges: tuple[Text, ...] = Field(max_length=20)


class ThreadExperimentCase(Record):
    case_id: Identity
    stratum: Literal[
        "changed",
        "terminology",
        "derivative",
        "contradiction",
        "historical",
        "resolved",
        "unavailable",
        "near_match",
        "no_change",
    ]
    question: Text
    initial_question: Text
    subjects: tuple[ThreadSubject, ...] = Field(min_length=1, max_length=5)
    initial_sources: tuple[ThreadExperimentSource, ...] = Field(min_length=1, max_length=10)
    followup_sources: tuple[ThreadExperimentSource, ...] = Field(min_length=1, max_length=10)
    expected: ExpectedThreadOutcome

    @model_validator(mode="after")
    def case_integrity(self) -> Self:
        subjects = {item.subject_id for item in self.subjects}
        sources = (*self.initial_sources, *self.followup_sources)
        snapshot_ids = [item.snapshot_id for item in sources]
        if len(snapshot_ids) != len(set(snapshot_ids)):
            raise ValueError("case snapshot identities must be distinct")
        if {item.subject_id for item in sources} - subjects:
            raise ValueError("case source references an unknown subject")
        if self.stratum == "near_match" and len(subjects) < 2:
            raise ValueError("near-match case requires distinct subjects")
        return self


class ThreadExperimentCorpus(Record):
    schema_version: Literal["research-thread-corpus/1"]
    cases: tuple[ThreadExperimentCase, ...] = Field(min_length=9, max_length=20)

    @model_validator(mode="after")
    def complete_strata(self) -> Self:
        expected = {
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
        if {case.stratum for case in self.cases} != expected:
            raise ValueError("corpus must contain every longitudinal stratum")
        if len({case.case_id for case in self.cases}) != len(self.cases):
            raise ValueError("case identities must be distinct")
        return self


def load_thread_experiment_corpus(path: Path) -> ThreadExperimentCorpus:
    return ThreadExperimentCorpus.model_validate(json.loads(path.read_bytes()))


class ThreadSubject(Record):
    subject_id: Identity
    label: Text
    identifiers: tuple[Text, ...] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def unique_identifiers(self) -> Self:
        if len(set(self.identifiers)) != len(self.identifiers):
            raise ValueError("subject identifiers must be distinct")
        return self


class ThreadRoot(Record):
    root_id: Identity
    sequence: int = Field(strict=True, ge=0)
    created_at: datetime
    subject_ids: tuple[Identity, ...] = Field(min_length=1, max_length=20)
    snapshot_ids: tuple[Identity, ...] = Field(max_length=100)
    claim_ids: tuple[Identity, ...] = Field(max_length=1000)

    @field_validator("created_at")
    @classmethod
    def timezone_qualified(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("root time must include an offset")
        return value


class SourceLineage(Record):
    source_link_id: Identity
    prior_snapshot_id: Identity
    current_snapshot_id: Identity
    relationship: Literal["unchanged", "updated", "derivative", "link_rotated"]
    rationale: Text

    @model_validator(mode="after")
    def distinct_versions(self) -> Self:
        if self.prior_snapshot_id == self.current_snapshot_id:
            raise ValueError("source lineage requires distinct snapshot identities")
        return self


class ClaimLineage(Record):
    claim_link_id: Identity
    prior_claim_id: Identity
    current_claim_id: Identity
    relationship: Literal[
        "unchanged", "corrected", "superseded", "historical", "resolved"
    ]
    rationale: Text

    @model_validator(mode="after")
    def distinct_versions(self) -> Self:
        if self.prior_claim_id == self.current_claim_id:
            raise ValueError("claim lineage requires distinct claim identities")
        return self


class RecheckObligation(Record):
    obligation_id: Identity
    question: Text
    status: Literal["open", "satisfied", "blocked"]
    opened_in_root_id: Identity
    resolved_in_root_id: Identity | None
    evidence_snapshot_ids: tuple[Identity, ...] = Field(max_length=100)

    @model_validator(mode="after")
    def resolution_matches_status(self) -> Self:
        resolved = self.resolved_in_root_id is not None
        if resolved != (self.status == "satisfied"):
            raise ValueError("only satisfied obligations identify a resolving root")
        if self.status == "satisfied" and not self.evidence_snapshot_ids:
            raise ValueError("satisfied obligation requires evidence")
        return self


class ResearchThread(Record):
    schema_version: Literal["research-thread-experiment/1"]
    scope_id: Identity
    thread_id: Identity
    subjects: tuple[ThreadSubject, ...] = Field(min_length=1, max_length=20)
    roots: tuple[ThreadRoot, ...] = Field(min_length=1, max_length=20)
    source_lineage: tuple[SourceLineage, ...] = Field(max_length=200)
    claim_lineage: tuple[ClaimLineage, ...] = Field(max_length=2000)
    recheck_obligations: tuple[RecheckObligation, ...] = Field(max_length=200)

    @model_validator(mode="after")
    def closed_and_append_only(self) -> Self:
        subject_ids = _unique(self.subjects, "subject_id", "subject")
        root_ids = _unique(self.roots, "root_id", "root")
        sequences = [root.sequence for root in self.roots]
        if sequences != list(range(len(self.roots))):
            raise ValueError("thread roots must form an ordered append-only sequence")
        times = [root.created_at for root in self.roots]
        if times != sorted(times):
            raise ValueError("thread roots must not move backward in time")
        snapshot_ids: set[str] = set()
        claim_ids: set[str] = set()
        for root in self.roots:
            if not set(root.subject_ids) <= subject_ids:
                raise ValueError("root references an unknown subject")
            if len(set(root.snapshot_ids)) != len(root.snapshot_ids):
                raise ValueError("root snapshot identities must be distinct")
            if len(set(root.claim_ids)) != len(root.claim_ids):
                raise ValueError("root claim identities must be distinct")
            if snapshot_ids & set(root.snapshot_ids) or claim_ids & set(root.claim_ids):
                raise ValueError("new roots must not reassign snapshot or claim identities")
            snapshot_ids.update(root.snapshot_ids)
            claim_ids.update(root.claim_ids)
        _unique(self.source_lineage, "source_link_id", "source lineage")
        _unique(self.claim_lineage, "claim_link_id", "claim lineage")
        _unique(self.recheck_obligations, "obligation_id", "recheck obligation")
        for source_link in self.source_lineage:
            if {
                source_link.prior_snapshot_id,
                source_link.current_snapshot_id,
            } - snapshot_ids:
                raise ValueError("source lineage references an unknown snapshot")
            _require_forward_reference(
                self.roots,
                source_link.prior_snapshot_id,
                source_link.current_snapshot_id,
                "snapshot_ids",
            )
        for claim_link in self.claim_lineage:
            if {claim_link.prior_claim_id, claim_link.current_claim_id} - claim_ids:
                raise ValueError("claim lineage references an unknown claim")
            _require_forward_reference(
                self.roots,
                claim_link.prior_claim_id,
                claim_link.current_claim_id,
                "claim_ids",
            )
        for obligation in self.recheck_obligations:
            if obligation.opened_in_root_id not in root_ids:
                raise ValueError("recheck obligation references an unknown opening root")
            if obligation.resolved_in_root_id not in root_ids | {None}:
                raise ValueError("recheck obligation references an unknown resolving root")
            if set(obligation.evidence_snapshot_ids) - snapshot_ids:
                raise ValueError("recheck obligation references unknown evidence")
            if obligation.resolved_in_root_id is not None:
                opened = next(
                    root.sequence
                    for root in self.roots
                    if root.root_id == obligation.opened_in_root_id
                )
                resolved = next(
                    root.sequence
                    for root in self.roots
                    if root.root_id == obligation.resolved_in_root_id
                )
                if resolved < opened:
                    raise ValueError("recheck obligation cannot resolve before it opens")
        return self


def _unique(records: tuple[Record, ...], field: str, label: str) -> set[str]:
    values = [getattr(record, field) for record in records]
    if len(values) != len(set(values)):
        raise ValueError(f"{label} identities must be distinct")
    return set(values)


def _require_forward_reference(
    roots: tuple[ThreadRoot, ...], prior: str, current: str, field: str
) -> None:
    locations = {
        identity: root.sequence
        for root in roots
        for identity in getattr(root, field)
    }
    if locations[current] <= locations[prior]:
        raise ValueError("lineage must point from an earlier root to a later root")


def validate_research_thread(
    payload: object, *, scope_id: str, thread_id: str
) -> ResearchThread:
    """Validate an untrusted thread against caller-established ownership."""
    thread = ResearchThread.model_validate(payload)
    if (thread.scope_id, thread.thread_id) != (scope_id, thread_id):
        raise ValueError("thread differs from expected scope or identity")
    return thread


class ThreadAnswer(Record):
    change_events: tuple[Text, ...] = Field(max_length=20)
    current_truths: tuple[Text, ...] = Field(max_length=20)
    historical_truths: tuple[Text, ...] = Field(max_length=20)
    unresolved: tuple[Text, ...] = Field(max_length=20)
    citations: tuple[Identity, ...] = Field(max_length=30)
    answer: Text


class ThreadGrade(Record):
    change_accuracy: int = Field(strict=True, ge=0, le=100)
    current_accuracy: int = Field(strict=True, ge=0, le=100)
    historical_preservation: int = Field(strict=True, ge=0, le=100)
    unresolved_accuracy: int = Field(strict=True, ge=0, le=100)
    usefulness: int = Field(strict=True, ge=0, le=100)
    false_merge: bool
    stale_current_leak: bool
    lost_history: bool
    unsupported_claims: int = Field(strict=True, ge=0, le=20)
    rationale: Text


class ThreadWorkItem(Record):
    trial_id: Identity
    case_id: Identity
    repetition: int = Field(strict=True, ge=1, le=3)
    arm: Literal["control", "treatment"]
    position: int = Field(strict=True, ge=0)


def build_thread_work_order(
    corpus: ThreadExperimentCorpus, *, seed: int
) -> tuple[ThreadWorkItem, ...]:
    rng = random.Random(seed)
    result: list[ThreadWorkItem] = []
    position = 0
    for repetition in range(1, 4):
        cases = list(corpus.cases)
        rng.shuffle(cases)
        for case in cases:
            arms: list[Literal["control", "treatment"]] = ["control", "treatment"]
            if (repetition + sum(case.case_id.encode())) % 2:
                arms.reverse()
            for arm in arms:
                result.append(
                    ThreadWorkItem(
                        trial_id=f"{case.case_id}-r{repetition}-{arm}",
                        case_id=case.case_id,
                        repetition=repetition,
                        arm=arm,
                        position=position,
                    )
                )
                position += 1
    return tuple(result)


def answer_schema(case: ThreadExperimentCase) -> dict:
    snapshot_ids = [
        source.snapshot_id
        for source in (*case.initial_sources, *case.followup_sources)
    ]
    text_array = {
        "type": "array",
        "maxItems": 20,
        "items": {"type": "string", "minLength": 1, "maxLength": 2000},
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "change_events": text_array,
            "current_truths": text_array,
            "historical_truths": text_array,
            "unresolved": text_array,
            "citations": {
                "type": "array",
                "maxItems": 30,
                "items": {"type": "string", "enum": snapshot_ids},
            },
            "answer": {"type": "string", "minLength": 1, "maxLength": 10000},
        },
        "required": [
            "change_events",
            "current_truths",
            "historical_truths",
            "unresolved",
            "citations",
            "answer",
        ],
    }


def grade_schema() -> dict:
    score = {"type": "integer", "minimum": 0, "maximum": 100}
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "change_accuracy": score,
            "current_accuracy": score,
            "historical_preservation": score,
            "unresolved_accuracy": score,
            "usefulness": score,
            "false_merge": {"type": "boolean"},
            "stale_current_leak": {"type": "boolean"},
            "lost_history": {"type": "boolean"},
            "unsupported_claims": {"type": "integer", "minimum": 0, "maximum": 20},
            "rationale": {"type": "string", "minLength": 1, "maxLength": 3000},
        },
        "required": [
            "change_accuracy",
            "current_accuracy",
            "historical_preservation",
            "unresolved_accuracy",
            "usefulness",
            "false_merge",
            "stale_current_leak",
            "lost_history",
            "unsupported_claims",
            "rationale",
        ],
    }


def validate_thread_answer(
    payload: object, case: ThreadExperimentCase
) -> ThreadAnswer:
    answer = ThreadAnswer.model_validate(payload)
    allowed = {
        source.snapshot_id
        for source in (*case.initial_sources, *case.followup_sources)
    }
    if set(answer.citations) - allowed:
        raise ValueError("answer cites a snapshot outside the frozen case")
    return answer


def build_followup_prompt(
    case: ThreadExperimentCase,
    *,
    arm: Literal["control", "treatment"],
    prior_answer: ThreadAnswer,
) -> dict:
    prompt = {
        "task": (
            "Answer using only supplied source snapshots. Separate what changed, "
            "what is current, what remains historically valid, and what is unresolved. "
            "Cite snapshot_id values. Do not infer identity from name similarity."
        ),
        "question": case.question,
        "source_snapshots": [
            source.model_dump(mode="json")
            for source in (*case.initial_sources, *case.followup_sources)
        ],
        "output_contract": answer_schema(case),
    }
    if arm == "treatment":
        prompt["research_thread"] = {
            "schema_version": THREAD_SCHEMA,
            "subjects": [item.model_dump(mode="json") for item in case.subjects],
            "prior_root": {
                "question": case.initial_question,
                "sources": [
                    source.model_dump(mode="json") for source in case.initial_sources
                ],
                "answer": prior_answer.model_dump(mode="json"),
            },
            "rules": [
                "Prior prose is context, not evidence.",
                "Only source snapshots may support factual claims.",
                "Preserve earlier claims as historical when later evidence changes.",
                "Keep distinct subject identifiers separate.",
            ],
        }
    return prompt
