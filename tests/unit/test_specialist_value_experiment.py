import pytest
from agent.experimental.specialist_value_experiment import (
    SpecialistAssignment,
    SpecialistEvidence,
    SpecialistHandoff,
    reconcile_handoffs,
)
from pydantic import ValidationError


def handoff(
    specialist: str,
    stance: str,
    *,
    source: str,
    canonical: str | None = None,
) -> SpecialistHandoff:
    assignment = SpecialistAssignment(
        specialist_id=specialist,
        question="Check the claim.",
        obligation_ids=("claim",),
        max_sources=2,
    )
    return SpecialistHandoff(
        schema_version="specialist-evidence-handoff/1",
        assignment=assignment,
        attempted_source_ids=(source,),
        acquired_source_ids=(source,),
        rejected_sources={},
        evidence=(
            SpecialistEvidence(
                evidence_id=f"e-{specialist}",
                source_id=source,
                canonical_source_id=canonical or source,
                publisher_id=source,
                obligation_id="claim",
                stance=stance,
                span="Exact evidence span.",
            ),
        ),
        source_independence_note="Independent publisher.",
        freshness_note="Current for the case boundary.",
        limitations=(),
        completion_state="completed",
        calls_used=1,
    )


def test_reconciliation_preserves_conflict() -> None:
    artifact = reconcile_handoffs(
        (
            handoff("support", "supports", source="a"),
            handoff("risk", "challenges", source="b"),
        ),
        allowed_obligation_ids=frozenset({"claim"}),
        call_budget=2,
    )
    assert artifact.covered_obligation_ids == ("claim",)
    assert artifact.conflicting_obligation_ids == ("claim",)
    assert len(artifact.admitted_evidence) == 2


def test_reconciliation_rejects_canonical_duplicate() -> None:
    artifact = reconcile_handoffs(
        (
            handoff("one", "supports", source="a", canonical="same"),
            handoff("two", "supports", source="b", canonical="same"),
        ),
        allowed_obligation_ids=frozenset({"claim"}),
        call_budget=2,
    )
    assert len(artifact.admitted_evidence) == 1
    assert artifact.rejected_evidence == {"e-two": "canonical_duplicate"}


def test_budget_exhaustion_keeps_branch_visible() -> None:
    artifact = reconcile_handoffs(
        (
            handoff("one", "supports", source="a"),
            handoff("two", "supports", source="b"),
        ),
        allowed_obligation_ids=frozenset({"claim"}),
        call_budget=1,
    )
    assert artifact.handoff_failures == ("two",)
    assert artifact.total_calls == 2


def test_handoff_cannot_exceed_assignment() -> None:
    payload = handoff("one", "supports", source="a").model_dump(mode="json")
    payload["evidence"][0]["obligation_id"] = "outside"
    with pytest.raises(ValidationError, match="exceeds its assignment"):
        SpecialistHandoff.model_validate(payload)
