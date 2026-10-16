from scripts.summarize_w11_general_retrieval import summarize


def _record(arm: str, urls: list[str]) -> dict:
    return {
        "status": "completed",
        "case_id": "case",
        "repetition": 0,
        "arm": arm,
        "challenge_type": "freshness",
        "control_policy": "full",
        "query_plan_sha256": "plan",
        "engine_scope_sha256": "scope",
        "elapsed_ms": 10 if arm == "flat_http" else 15,
        "attempts": [
            {
                "query_sha256": "query",
                "result_url_sha256": urls,
                "returned_results": len(urls),
                "result_count": len(urls),
            }
        ],
        **(
            {
                "workflow": {
                    "state": "succeeded",
                    "stop_reason": "caller_completed",
                    "caller_completed": True,
                    "budgets": {
                        "limits": {"queries": 1, "attempts": 1, "engine_attempts": 2, "results": 10},
                        "used": {"queries": 1, "attempts": 1, "engine_attempts": 2, "results": 1},
                    },
                }
            }
            if arm == "recorded_continuation"
            else {}
        ),
    }


def test_complete_pair_reports_overlap_without_calling_it_quality() -> None:
    result = summarize(
        [_record("flat_http", ["a", "b"]), _record("recorded_continuation", ["b", "c"])],
        expected_pairs=1,
    )
    assert result["hard_gate_passed"] is True
    assert result["mean_result_set_jaccard"] == 1 / 3
    assert result["median_elapsed_ms_delta"] == 5
    assert "not a research-quality score" in result["interpretation"]


def test_missing_arm_fails_completion_gate() -> None:
    result = summarize([_record("flat_http", ["a"])], expected_pairs=1)
    assert result["complete"] is False
    assert result["hard_gate_passed"] is False
    assert result["incomplete_pairs"][0]["arms"] == ["flat_http"]


def test_failed_trial_is_retained() -> None:
    failure = {
        "status": "failed",
        "case_id": "case",
        "repetition": 0,
        "arm": "recorded_continuation",
        "error_type": "TimeoutError",
    }
    result = summarize([_record("flat_http", ["a"]), failure], expected_pairs=1)
    assert result["integrity"]["no_failed_trials"] is False
    assert result["failures"][0]["error_type"] == "TimeoutError"


def test_unbounded_workflow_fails_hard_gate() -> None:
    research = _record("recorded_continuation", ["a"])
    research["workflow"]["budgets"]["used"]["attempts"] = 2
    result = summarize([_record("flat_http", ["a"]), research], expected_pairs=1)
    assert result["integrity"]["all_workflows_accounted_and_bounded"] is False
    assert result["hard_gate_passed"] is False
