import json

import httpx

from scripts.grade_w11_general_retrieval import (
    acquisition_order,
    digest,
    grade_trial,
    register_candidates,
)


def _retrieval() -> list[dict]:
    return [
        {
            "results": [
                {"url": f"https://example.com/{index}", "title": str(index)}
                for index in range(6)
            ]
        },
        {
            "results": [
                {"url": "https://example.com/0", "title": "duplicate"},
                {"url": "https://other.example/a", "title": "a"},
                {"url": "https://other.example/b", "title": "b"},
                {"url": "https://other.example/c", "title": "c"},
            ]
        },
    ]


def test_registration_conserves_duplicate_origins() -> None:
    candidates = register_candidates(_retrieval())
    assert len(candidates) == 9
    duplicate = candidates["https://example.com/0"]
    assert duplicate["search_origins"] == [
        {"attempt": 0, "rank": 1},
        {"attempt": 1, "rank": 1},
    ]


def test_full_policy_uses_four_initial_two_followup_then_fills() -> None:
    candidates = register_candidates(_retrieval())
    order = acquisition_order(candidates, policy="full")
    assert len(order) == 8
    assert sum(attempt == 0 for _, attempt in order[:4]) == 4
    assert sum(attempt == 1 for _, attempt in order[4:6]) == 2


def test_fixed_policy_selects_at_most_eight() -> None:
    candidates = register_candidates(_retrieval())
    order = acquisition_order(candidates, policy="fixed")
    assert len(order) == 8
    assert all(attempt in {0, 1} for _, attempt in order)


def test_grade_trial_closes_supported_gap_without_public_excerpt() -> None:
    candidate_id = digest("https://example.com/evidence")[:16]

    def api_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v2/scrape"
        return httpx.Response(
            200,
            json={"success": True, "data": {"markdown": "verbatim evidence"}},
        )

    assessment = {
        "candidates": {
            candidate_id: {
                "relevant_gap_ids": ["claim"],
                "supports_or_challenges": True,
                "quality": dict.fromkeys(("currency", "relevance", "authority", "accuracy", "purpose"), 2),
                "derivative_of": None,
                "marginal_value": True,
                "improves_currency": False,
                "improves_authority": True,
                "resolves_contradiction": False,
                "reason": "Direct evidence.",
            }
        },
        "gaps": {
            "claim": {
                "status": "closed",
                "candidate_ids": [candidate_id],
                "reason": "Supported.",
            }
        },
    }

    def llm_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/chat/completions"
        return httpx.Response(
            200,
            json={
                "model": "local",
                "choices": [{"message": {"content": json.dumps(assessment)}}],
                "usage": {"total_tokens": 10},
            },
        )

    case = {
        "case_id": "case",
        "query": "question",
        "as_of": "2026-09-11",
        "claims": [{"claim_id": "claim", "importance": 3, "closure_rule": "Direct evidence."}],
    }
    entry = {
        "position": 1,
        "case_id": "case",
        "challenge_type": "primary",
        "repetition": 0,
        "arm": "flat_http",
        "control_policy": "fixed",
    }
    with (
        httpx.Client(base_url="https://api.test", transport=httpx.MockTransport(api_handler)) as api,
        httpx.Client(base_url="https://llm.test", transport=httpx.MockTransport(llm_handler)) as llm,
    ):
        public, private = grade_trial(
            case=case,
            entry=entry,
            retrieval=[{"results": [{"url": "https://example.com/evidence", "title": "Evidence"}]}],
            api_client=api,
            llm_client=llm,
            model="local",
            seed=1,
        )
    assert public["metrics"]["weighted_closure"] == 1
    assert public["gap_results"][0]["status"] == "closed"
    assert "verbatim evidence" not in json.dumps(public)
    assert private["acquisitions"][0]["reviewed_excerpt"] == "verbatim evidence"
