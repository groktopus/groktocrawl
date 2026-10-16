"""Matched flat-HTTP and recorded-continuation retrieval for W11."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, Protocol

import httpx

TERMINAL = {"succeeded", "partial", "failed", "cancelled", "expired"}
HTTP_ATTEMPTS = 5


class ToolCaller(Protocol):
    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any: ...


def _require(value: Any, operation: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RuntimeError(f"{operation} returned no object")
    if "error" in value:
        error = value["error"]
        code = error.get("code", "unknown") if isinstance(error, dict) else "unknown"
        raise RuntimeError(f"{operation} failed: {code}")
    return value


def _require_mutation_ack(value: Any, operation: str) -> None:
    """Accept SlopSearX's structured or plain-text mutation acknowledgement."""
    if isinstance(value, dict):
        _require(value, operation)
        return
    if isinstance(value, str) and value.strip():
        return
    raise RuntimeError(f"{operation} returned no acknowledgement")


def flat_http_searches(
    client: httpx.Client,
    queries: list[str],
    engines: list[str],
    *,
    max_results: int,
    wait: Callable[[float], None] = time.sleep,
) -> list[dict[str, Any]]:
    """Execute the frozen query sequence through the SearXNG HTTP surface."""
    if not queries or not engines:
        raise ValueError("queries and engines must not be empty")
    records = []
    for query in queries:
        for attempt in range(HTTP_ATTEMPTS):
            response = client.get(
                "/search",
                params={
                    "q": query,
                    "format": "json",
                    "language": "en",
                    "pageno": 1,
                    "engines": ",".join(engines),
                },
            )
            if response.status_code != 429 or attempt == HTTP_ATTEMPTS - 1:
                break
            retry_after = response.headers.get("Retry-After", "")
            delay = float(retry_after) if retry_after.isdigit() else 2**attempt
            wait(min(delay, 5.0))
        response.raise_for_status()
        payload = response.json()
        results = payload.get("results")
        if not isinstance(results, list):
            raise RuntimeError("HTTP search returned no result list")
        records.append(
            {
                "query": query,
                "engines": list(engines),
                "results": results[:max_results],
                "result_count": len(results),
                "unresponsive_engines": payload.get("unresponsive_engines", []),
            }
        )
    return records


def _poll(
    client: ToolCaller,
    job_id: str,
    *,
    timeout_seconds: float,
    wait: Callable[[float], None],
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    while True:
        job = _require(client.call_tool("slopsearx_get_job", {"job_id": job_id}), "get_job")
        if job.get("state") in TERMINAL:
            return job
        if time.monotonic() >= deadline:
            raise TimeoutError("research job did not reach a terminal state")
        wait(0.25)


def recorded_continuation_searches(
    client: ToolCaller,
    *,
    question: str,
    queries: list[str],
    engines: list[str],
    max_results: int,
    idempotency_key: str,
    timeout_seconds: float = 180.0,
    wait: Callable[[float], None] = time.sleep,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Execute the same caller-authored queries through SlopSearX research."""
    if not queries or not engines:
        raise ValueError("queries and engines must not be empty")
    start = _require(
        client.call_tool(
            "slopsearx_start_research",
            {
                "question": question,
                "initial_plan": [{"query": queries[0], "engines": engines}],
                "max_queries": len(queries),
                "max_attempts": len(queries),
                "max_engines_per_query": len(engines),
                "max_engine_attempts": len(queries) * len(engines),
                "max_results": len(queries) * max_results,
                "idempotency_key": idempotency_key,
            },
        ),
        "start_research",
    )
    job_id = str(start.get("job_id", ""))
    if not job_id:
        raise RuntimeError("start_research returned no job id")
    job = _poll(client, job_id, timeout_seconds=timeout_seconds, wait=wait)
    for index, query in enumerate(queries[1:], 1):
        _require(
            client.call_tool(
                "slopsearx_extend_research",
                {
                    "job_id": job_id,
                    "query": query,
                    "engines": engines,
                    "continuation_key": f"{idempotency_key}:{index}",
                },
            ),
            "extend_research",
        )
        job = _poll(client, job_id, timeout_seconds=timeout_seconds, wait=wait)
    update = client.call_tool(
        "slopsearx_update_research",
        {"job_id": job_id, "complete": True, "rationale": "Frozen caller plan executed."},
    )
    # SlopSearX 0.5 returns a plain-text acknowledgement for this mutation.
    # Structured responses remain supported and structured errors fail closed.
    _require_mutation_ack(update, "update_research")
    job = _require(client.call_tool("slopsearx_get_job", {"job_id": job_id}), "get_job")
    observed_queries = [item.get("query") for item in job.get("queries", [])]
    if observed_queries != queries:
        raise RuntimeError("research job did not preserve the frozen query sequence")

    records = []
    for item in job["queries"]:
        if item.get("engines") != engines:
            raise RuntimeError("research job did not preserve the frozen engine scope")
        cursor = item.get("cursor")
        page = (
            _require(
                client.call_tool(
                    "slopsearx_read_results",
                    {"cursor": cursor, "max_results": max_results},
                ),
                "read_results",
            )
            if cursor
            else {"results": []}
        )
        records.append(
            {
                "query": item["query"],
                "engines": item["engines"],
                "results": page.get("results", []),
                "result_count": item.get("result_count", 0),
                "coverage": item.get("coverage", {}),
                "cursor": cursor,
            }
        )
    return records, job
