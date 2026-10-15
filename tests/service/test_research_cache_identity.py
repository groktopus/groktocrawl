"""Cache partitions must follow the credential accepted by API authentication."""

from __future__ import annotations

import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from agent import auth
from agent.models import AgentRequest
from agent.routes._helpers import _derive_user_id
from agent.routes.agent import _lookup_agent_cache, create_agent
from agent.session_scope import request_scope
from fastapi import Response
from starlette.requests import Request

KEY = "current-fixture-key"
KEY_HASH = hashlib.sha256(KEY.encode()).hexdigest()
HEADERS = [
    {"Authorization": f"Bearer {KEY}"},
    {"X-API-Key": KEY},
    {"Authorization": f"Bearer {KEY}", "X-API-Key": "unaccepted-key"},
    {"Authorization": "Bearer wrong-key", "X-API-Key": KEY},
    {"Authorization": "Basic ignored", "X-API-Key": KEY},
]


def make_request(headers, state=None):
    return Request(
        {
            "type": "http",
            "headers": [
                (key.lower().encode(), value.encode()) for key, value in headers.items()
            ],
            "client": ("127.0.0.1", 1234),
            "app": SimpleNamespace(state=state),
        }
    )


@pytest.fixture(autouse=True)
def authenticated_partition(monkeypatch):
    monkeypatch.setattr(auth, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth, "API_KEY", KEY)
    monkeypatch.setenv("RESEARCH_MEMORY_SCOPE", "per_user")


@pytest.mark.asyncio
@pytest.mark.parametrize("headers", HEADERS)
async def test_cache_and_session_identity_match_accepted_header(headers):
    request = make_request(headers)
    await auth.verify_api_key(request)
    assert _derive_user_id(request) == KEY_HASH[:16]
    assert request_scope(request) == "key:" + KEY_HASH[:32]


@pytest.mark.asyncio
async def test_conflicting_header_cannot_select_streaming_cache_partition():
    memory = SimpleNamespace(query=AsyncMock(return_value={"hit": False}))
    request = make_request(HEADERS[2], SimpleNamespace(research_memory=memory))
    await auth.verify_api_key(request)
    assert (
        await _lookup_agent_cache(
            request, AgentRequest(prompt="question", stream=True), "fingerprint"
        )
        is None
    )
    assert memory.query.await_args.kwargs["user_id"] == KEY_HASH[:16]


@pytest.mark.asyncio
async def test_conflicting_header_cannot_select_background_worker_partition(
    monkeypatch,
):
    worker = Mock(return_value=object())
    monkeypatch.setattr("agent.worker._process_agent_async", worker)
    monkeypatch.setattr(
        "agent.routes.agent._handle_agent_streaming", AsyncMock(return_value=None)
    )
    state = SimpleNamespace(
        rate_limiter=SimpleNamespace(
            check=AsyncMock(return_value=(True, 100)), limit=100
        ),
        max_searches_per_request=5,
        job_store=SimpleNamespace(create_job=Mock(return_value="job-fixture")),
        task_tracker=SimpleNamespace(create_background_task=Mock()),
        llm_base_url="https://model.example.test/v1",
        llm_api_key="fixture-only",
        llm_model="fixture",
        searxng_url="https://search.example.test",
        scraper_url="https://scraper.example.test",
        research_memory=object(),
    )
    request = make_request(HEADERS[2], state)
    await auth.verify_api_key(request)
    result = await create_agent(request, AgentRequest(prompt="question"), Response())
    assert result.id == "job-fixture"
    assert worker.call_args.kwargs["user_id"] == KEY_HASH[:16]


def test_global_cache_and_unauthenticated_fallback_are_preserved(monkeypatch):
    monkeypatch.setenv("RESEARCH_MEMORY_SCOPE", "global")
    assert _derive_user_id(make_request(HEADERS[2])) is None
    monkeypatch.setenv("RESEARCH_MEMORY_SCOPE", "per_user")
    monkeypatch.setattr(auth, "AUTH_ENABLED", False)
    assert _derive_user_id(make_request({})) == "127.0.0.1"
    for headers in HEADERS[:2]:
        assert _derive_user_id(make_request(headers)) == KEY_HASH[:16]
