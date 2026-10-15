from scripts.summarize_w11_general_quality import summarize


def _record(arm: str, repetition: int, *, closed: int = 3, useful: bool = True) -> dict:
    return {
        "status": "completed",
        "case_id": "case",
        "repetition": repetition,
        "arm": arm,
        "metrics": {"closed_weight": closed, "total_weight": 3, "weighted_closure": closed / 3},
        "candidates": [
            {
                "admitted": True,
                "operational_assessment": {"supports_or_challenges": useful},
            }
        ],
    }


def test_equal_arms_pass_noninferiority_with_case_bootstrap() -> None:
    records = [
        _record(arm, repetition)
        for repetition in range(3)
        for arm in ("flat_http", "recorded_continuation")
    ]
    result = summarize(records, expected_pairs=3, samples=100)
    assert result["quality_gate_passed"] is True
    assert result["uncertainty"]["unit"] == "case"
    assert result["paired_effects"]["weighted_closure_delta"] == 0


def test_material_research_degradation_fails_quality_gate() -> None:
    records = [
        record
        for repetition in range(3)
        for record in (
            _record("flat_http", repetition),
            _record("recorded_continuation", repetition, closed=0, useful=False),
        )
    ]
    result = summarize(records, expected_pairs=3, samples=100)
    assert result["gates"]["closure_noninferior_2pp"] is False
    assert result["gates"]["precision_noninferior_5pp"] is False
    assert result["quality_gate_passed"] is False


def test_repetitions_are_not_counted_as_bootstrap_units() -> None:
    records = [
        _record(arm, repetition)
        for repetition in range(3)
        for arm in ("flat_http", "recorded_continuation")
    ]
    result = summarize(records, expected_pairs=3, samples=25)
    assert result["uncertainty"]["samples"] == 25
    assert result["uncertainty"]["unit"] == "case"
