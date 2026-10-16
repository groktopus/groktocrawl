"""The configured model can plan bounded queries, not invent a new tool policy."""

import json

import pytest
from agent.experimental.durable_plan import plan_queries
from agent.experimental.model_review import ModelReply
from pydantic import ValidationError


@pytest.mark.asyncio
@pytest.mark.parametrize("queries", [["one", "two", "three"], ["one"]])
async def test_recorded_planning_passes_objective_and_strict_bounds(queries):
    seen = []

    async def complete(request):
        seen.append(request)
        return ModelReply(
            json.dumps(
                {
                    "reasoning": "Bounded plan",
                    "research_strategy": "deep",
                    "focused_queries": queries,
                }
            ).encode(),
            "configured",
            1,
            1,
        )

    assert await plan_queries(
        "The actual objective",
        complete=complete,
        model="configured",
        as_of="2026-09-01T00:00:00Z",
    ) == tuple(queries)
    assert len(seen) == 1
    body = json.loads(seen[0].payload)
    assert body["objective"] == "The actual objective"
    assert body["as_of"] == "2026-09-01T00:00:00Z"
    assert body["max_queries"] == 3
    assert seen[0].requested_model == "configured"
    assert seen[0].response_schema["additionalProperties"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "queries", [[], ["one"] * 2, ["one", "two", "three", "four"], ["  "]]
)
async def test_invalid_plan_fails_without_fallback_or_extra_model_call(queries):
    calls = []

    async def complete(request):
        calls.append(request)
        return ModelReply(
            json.dumps(
                {
                    "reasoning": "Invalid plan",
                    "research_strategy": "deep",
                    "focused_queries": queries,
                }
            ).encode(),
            "configured",
            1,
            1,
        )

    with pytest.raises((ValueError, ValidationError)):
        await plan_queries("objective", complete=complete, model="configured")
    assert len(calls) == 1
