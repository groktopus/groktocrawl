"""Explicit, deterministic follow-up previews; never executes proposed actions."""

import re
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EvidenceChoice(BaseModel):
    """Caller-selected identity and subject, verified against retained evidence."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["session", "root"]
    container_id: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9_-]+$")
    ref_id: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9_-]+$")
    subject: str = Field(min_length=1, max_length=160, pattern=r"^[^\n\r\x00]+$")


class FollowupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    wording: str = Field(min_length=1, max_length=4000)
    selected: list[EvidenceChoice] = Field(default_factory=list, max_length=8)
    standalone_override: str | None = Field(default=None, min_length=1, max_length=4000)
    correction: str | None = Field(default=None, min_length=1, max_length=1000)
    max_age_seconds: int = Field(default=86400, ge=0, le=31536000)

    @model_validator(mode="after")
    def unique_choices(self) -> "FollowupRequest":
        identities = [(x.kind, x.container_id, x.ref_id) for x in self.selected]
        if len(identities) != len(set(identities)):
            raise ValueError("Selected identities must be unique")
        return self


class SelectedEvidence(BaseModel):
    identity: EvidenceChoice
    observed_at: str | None
    temporal_status: Literal["recent_snapshot", "historical", "unknown"]


class NextAction(BaseModel):
    type: Literal["compare", "deepen", "narrow", "request_evidence"]
    query: str
    selected: list[EvidenceChoice]
    requires_confirmation: Literal[True] = True


class FollowupResponse(BaseModel):
    success: Literal[True] = True
    original_wording: str
    proposed_query: str | None
    status: Literal["ready", "needs_clarification"]
    selected: list[SelectedEvidence]
    ambiguities: list[str]
    correction: str | None
    actions: list[NextAction]
    context_policy: Literal["explicit_selection_only"] = "explicit_selection_only"
    executed: Literal[False] = False


def temporal_status(
    observed_at: str | None, max_age: int, now: datetime
) -> Literal["recent_snapshot", "historical", "unknown"]:
    if not observed_at:
        return "unknown"
    try:
        observed = datetime.fromisoformat(observed_at)
        if observed.tzinfo is None:
            return "unknown"
        age = (now - observed).total_seconds()
        return "recent_snapshot" if 0 <= age <= max_age else "historical"
    except ValueError:
        return "unknown"


def preview(
    body: FollowupRequest, selected: list[SelectedEvidence]
) -> FollowupResponse:
    """Resolve only a single explicitly named referent; abstain on ambiguity.

    Source bodies never enter this function. Caller subject labels are data,
    never instructions; the preview invokes no model or research subsystem.
    """
    query = body.standalone_override or body.wording
    ambiguities = []
    pronouns = re.findall(
        r"\b(it|its|they|them|their|theirs|that|this|these|those|other\s+one)\b",
        query,
        re.IGNORECASE,
    )
    if not body.standalone_override and pronouns:
        if len(selected) == 1 and all(
            word.lower() in {"it", "its"} for word in pronouns
        ):
            subject = selected[0].identity.subject
            # One substitution pass keeps explicitly supplied subject text inert.
            query = re.sub(
                r"\b(it|its)\b",
                lambda match: (
                    subject + ("'s" if match.group().lower() == "its" else "")
                ),
                query,
                flags=re.IGNORECASE,
            )
        else:
            ambiguities.append(
                "Name each referent or supply standalone_override; selection order is not identity."
            )
    if body.correction and not body.standalone_override:
        ambiguities.append(
            "Apply the stated correction in standalone_override before execution."
        )
    actions = []
    if not ambiguities:
        identities = [x.identity for x in selected]
        if len(identities) >= 2:
            actions.append(NextAction(type="compare", query=query, selected=identities))
        if len(identities) == 1:
            actions.append(NextAction(type="deepen", query=query, selected=identities))
        actions.append(NextAction(type="narrow", query=query, selected=identities))
        actions.append(
            NextAction(type="request_evidence", query=query, selected=identities)
        )
    return FollowupResponse(
        original_wording=body.wording,
        proposed_query=None if ambiguities else query,
        status="needs_clarification" if ambiguities else "ready",
        selected=selected,
        ambiguities=ambiguities,
        correction=body.correction,
        actions=actions,
    )


def selected_evidence(
    choice: EvidenceChoice, observed_at: str | None, max_age: int
) -> SelectedEvidence:
    return SelectedEvidence(
        identity=choice,
        observed_at=observed_at,
        temporal_status=temporal_status(observed_at, max_age, datetime.now(UTC)),
    )
