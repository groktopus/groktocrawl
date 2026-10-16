import pytest

from scripts.build_w11_general_work_order import (
    W10_POLICIES,
    build,
    frozen_result_limit,
)

CASES = {
    "cases": [
        {"case_id": "a", "challenge_type": "freshness"},
        {"case_id": "b", "challenge_type": "identity"},
    ]
}


def _selection(*, complete: bool = True, authorized: bool = True) -> dict:
    return {
        "schema_version": "enterprise-evaluation/w10-policy-selection/1",
        "complete": complete,
        "w11_measurement_authorized": authorized,
        "selected_challenge_types": ["freshness"],
        "known_challenge_types": ["freshness", "identity"],
    }


def test_build_maps_selected_types_and_counterbalances_arms() -> None:
    result = build(_selection(), CASES, seed=7, repetitions=3, result_limit=8)
    assert result["policy_by_challenge_type"] == {
        "freshness": "full",
        "identity": "fixed",
    }
    assert len(result["entries"]) == 12
    assert result["result_limit"] == 8
    for repetition in range(3):
        entries = [row for row in result["entries"] if row["repetition"] == repetition]
        for case_id in ("a", "b"):
            assert {row["arm"] for row in entries if row["case_id"] == case_id} == {
                "flat_http",
                "recorded_continuation",
            }


def test_build_rejects_partial_w10_selection() -> None:
    try:
        build(
            _selection(complete=False),
            CASES,
            seed=7,
            repetitions=3,
            result_limit=8,
        )
    except ValueError as error:
        assert "must be complete" in str(error)
    else:
        raise AssertionError("partial W10 selection was accepted")


def test_build_rejects_inconclusive_w10_selection() -> None:
    with pytest.raises(ValueError, match="did not authorize"):
        build(
            _selection(authorized=False),
            CASES,
            seed=7,
            repetitions=3,
            result_limit=8,
        )


def test_build_rejects_mismatched_challenge_type_inventory() -> None:
    selection = _selection()
    selection["known_challenge_types"] = ["freshness"]
    with pytest.raises(ValueError, match="does not match"):
        build(selection, CASES, seed=7, repetitions=3, result_limit=8)


def test_build_rejects_unknown_selected_type() -> None:
    selection = _selection()
    selection["selected_challenge_types"] = ["unknown"]
    try:
        build(selection, CASES, seed=7, repetitions=3, result_limit=8)
    except ValueError as error:
        assert "unknown challenge type" in str(error)
    else:
        raise AssertionError("unknown challenge type was accepted")


def test_build_rejects_an_unfrozen_result_limit() -> None:
    try:
        build(_selection(), CASES, seed=7, repetitions=3, result_limit=0)
    except ValueError as error:
        assert "result limit" in str(error)
    else:
        raise AssertionError("invalid result limit was accepted")


def test_result_limit_comes_from_complete_matching_w10_manifest() -> None:
    manifest = {
        "schema_version": "enterprise-evaluation/w10-policy-run/1",
        "cases_sha256": "a" * 64,
        "records": 30,
        "completed": 30,
        "failed": 0,
        "failed_attempts": 0,
        "repetitions": 3,
        "policies": W10_POLICIES,
        "result_limit": 8,
    }
    assert (
        frozen_result_limit(
            manifest, cases_sha256="a" * 64, case_count=2, repetitions=3
        )
        == 8
    )
    for field, value in (
        ("cases_sha256", "b" * 64),
        ("completed", 29),
        ("failed", 1),
        ("failed_attempts", 1),
        ("policies", list(reversed(W10_POLICIES))),
    ):
        changed = {**manifest, field: value}
        with pytest.raises(ValueError, match="complete matching"):
            frozen_result_limit(
                changed, cases_sha256="a" * 64, case_count=2, repetitions=3
            )
