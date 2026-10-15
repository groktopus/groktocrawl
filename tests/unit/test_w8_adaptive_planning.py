"""Admission and budget tests for the W8 adaptive planning runner."""

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[2] / "scripts" / "run_w8_adaptive_planning.py"
sys.path.insert(0, str(SCRIPT.parent))
SPEC = importlib.util.spec_from_file_location("w8_adaptive", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def decision(**changes):
    value = {
        "sufficient": False,
        "reason": "weak",
        "unresolved_needs": ["primary source"],
        "follow_ups": [{"query": "specific primary source", "purpose": "missing_support"}],
    }
    value.update(changes)
    return value


def test_admits_bounded_distinct_follow_ups():
    admitted = MODULE.validate_decision(decision(), "broad question", MODULE.Bounds())
    assert admitted["follow_ups"][0]["purpose"] == "missing_support"


@pytest.mark.parametrize(
    "payload",
    [
        decision(follow_ups=[{"query": "broad question", "purpose": "missing_support"}]),
        decision(follow_ups=[{"query": "a", "purpose": "missing_support"}, {"query": "b", "purpose": "freshness"}, {"query": "c", "purpose": "opposition"}]),
        decision(sufficient=True, reason="adequate"),
        decision(unresolved_needs=[]),
        decision(follow_ups=[{"query": "x", "purpose": "invented"}]),
    ],
)
def test_rejects_repeated_over_budget_or_incoherent_plans(payload):
    with pytest.raises(ValueError):
        MODULE.validate_decision(payload, "broad question", MODULE.Bounds())


def test_admits_sufficient_stop_without_more_work():
    payload = {"sufficient": True, "reason": "adequate", "unresolved_needs": [], "follow_ups": []}
    assert MODULE.validate_decision(payload, "question", MODULE.Bounds()) == payload


def baseline(results):
    return {"response": {"results": results}, "latency_ms": 10}


def case():
    return {
        "case_id": "case-1", "category": "injected", "query": "question",
        "as_of": "2026-09-11",
        "known_useful_urls": [{"url": "https://primary.example/report"}],
    }


def test_run_bounds_searches_and_deduplicates_repeated_results(monkeypatch):
    plan = decision(
        reason="contradictory",
        follow_ups=[
            {"query": "primary report", "purpose": "missing_support"},
            {"query": "opposing report", "purpose": "opposition"},
        ],
    )
    monkeypatch.setattr(MODULE, "_planner_request", lambda **_: (plan, {"usage": {}}, 5))
    calls = []

    def search(_endpoint, query, _limit, _timeout):
        calls.append(query)
        return ({"results": [{"url": "https://primary.example/report"}]}, 4)

    monkeypatch.setattr(MODULE, "_search", search)
    row = MODULE.run_case(
        case(), baseline([{"url": "https://other.example"}]), endpoint="http://search",
        base_url="http://model", api_key="key", model="local", bounds=MODULE.Bounds(), timeout=30,
    )
    assert len(calls) == 2
    assert row["searches"] == 3
    assert row["unique_results"] == 2
    assert row["decision"]["reason"] == "contradictory"
    assert row["score"]["known_useful_found"] == 1


def test_run_retains_unavailable_follow_up_and_unresolved_need(monkeypatch):
    plan = decision(unresolved_needs=["source is unavailable"])
    monkeypatch.setattr(MODULE, "_planner_request", lambda **_: (plan, {"usage": {}}, 5))

    def unavailable(*_args):
        raise OSError("injected unavailable source")

    monkeypatch.setattr(MODULE, "_search", unavailable)
    row = MODULE.run_case(
        case(), baseline([]), endpoint="http://search", base_url="http://model",
        api_key="key", model="local", bounds=MODULE.Bounds(), timeout=30,
    )
    assert row["stop_reason"] == "plan_exhausted"
    assert row["decision"]["unresolved_needs"] == ["source is unavailable"]
    assert row["attempts"][1]["status"] == "failed"
    assert row["attempts"][1]["error_type"] == "OSError"


def test_run_stops_before_search_when_time_budget_is_exhausted(monkeypatch):
    plan = decision(unresolved_needs=["unresolvable within the available evidence"])
    monkeypatch.setattr(MODULE, "_planner_request", lambda **_: (plan, {"usage": {}}, 5))
    clock = iter((0.0, 2.0, 2.0))
    monkeypatch.setattr(MODULE.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(
        MODULE, "_search", lambda *_: pytest.fail("search exceeded the time budget")
    )
    row = MODULE.run_case(
        case(), baseline([]), endpoint="http://search", base_url="http://model",
        api_key="key", model="local", bounds=MODULE.Bounds(max_elapsed_ms=1000),
        timeout=30,
    )
    assert row["stop_reason"] == "time_limit"
    assert row["searches"] == 1
    assert row["decision"]["unresolved_needs"]
