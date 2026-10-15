from typing import Any

import httpx

from scripts.w11_retrieval_transports import (
    flat_http_searches,
    recorded_continuation_searches,
)


def test_flat_http_uses_frozen_explicit_scope() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["engines"] == "a,b"
        return httpx.Response(200, json={"results": [{"url": "https://example.com"}]})

    with httpx.Client(base_url="https://search.test", transport=httpx.MockTransport(handler)) as client:
        result = flat_http_searches(client, ["q"], ["a", "b"], max_results=1)
    assert result[0]["result_count"] == 1
    assert result[0]["engines"] == ["a", "b"]


def test_flat_http_retries_rate_limit_with_bounded_backoff() -> None:
    attempts = 0
    waits = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            return httpx.Response(429, headers={"Retry-After": "1"})
        return httpx.Response(200, json={"results": []})

    with httpx.Client(
        base_url="https://search.test", transport=httpx.MockTransport(handler)
    ) as client:
        flat_http_searches(client, ["q"], ["a"], max_results=1, wait=waits.append)
    assert attempts == 3
    assert waits == [1.0, 1.0]


class FakeMcp:
    def __init__(self) -> None:
        self.queries = ["q0"]
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((name, arguments))
        if name == "slopsearx_start_research":
            return {"job_id": "job-1", "state": "queued"}
        if name == "slopsearx_extend_research":
            self.queries.append(arguments["query"])
            return {"job_id": "job-1", "state": "queued"}
        if name == "slopsearx_update_research":
            return "Research updated"
        if name == "slopsearx_read_results":
            return {"results": [{"url": f'https://example.com/{arguments["cursor"]}'}]}
        if name == "slopsearx_get_job":
            return {
                "job_id": "job-1",
                "state": "succeeded",
                "queries": [
                    {
                        "query": query,
                        "engines": ["a", "b"],
                        "cursor": f"c{index}",
                        "result_count": 1,
                        "coverage": {"complete": True},
                    }
                    for index, query in enumerate(self.queries)
                ],
            }
        raise AssertionError(name)


def test_recorded_continuation_preserves_caller_plan_and_bounds() -> None:
    client = FakeMcp()
    records, job = recorded_continuation_searches(
        client,
        question="question",
        queries=["q0", "q1"],
        engines=["a", "b"],
        max_results=3,
        idempotency_key="trial",
        wait=lambda _: None,
    )
    assert [item["query"] for item in records] == ["q0", "q1"]
    assert job["queries"][1]["engines"] == ["a", "b"]
    start = next(arguments for name, arguments in client.calls if name == "slopsearx_start_research")
    assert start["max_queries"] == 2
    assert start["max_engine_attempts"] == 4
    assert start["max_results"] == 6
    assert any(name == "slopsearx_update_research" for name, _ in client.calls)
