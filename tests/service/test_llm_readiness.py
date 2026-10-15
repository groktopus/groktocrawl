"""Readiness uncertainty must not masquerade as definitive model failure."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from agent.llm import LLMClient, LLMReadiness
from agent.models import AgentRequest
from agent.routes.agent import _handle_agent_streaming
from fastapi import HTTPException

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize(
    "status,outcome",
    [
        (200, LLMReadiness.READY),
        (429, LLMReadiness.RATE_LIMITED),
        (401, LLMReadiness.REJECTED),
        (503, LLMReadiness.UNAVAILABLE),
    ],
)
async def test_probe_once_and_redacts_provider_body(
    monkeypatch, caplog, status, outcome
):
    post = AsyncMock(return_value=httpx.Response(status, text="private-provider-body"))
    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    client = LLMClient(base_url="https://provider.test/v1", model="test")
    try:
        assert await client.probe_readiness() == outcome
        post.assert_awaited_once()
        assert "private-provider-body" not in caplog.text
    finally:
        await client.close()


@pytest.mark.parametrize(
    "error,outcome",
    [
        (httpx.ReadTimeout("private-endpoint"), LLMReadiness.TIMED_OUT),
        (httpx.ConnectTimeout("private-endpoint"), LLMReadiness.UNAVAILABLE),
        (httpx.ConnectError("private-endpoint"), LLMReadiness.UNAVAILABLE),
    ],
)
async def test_probe_classifies_transport_without_retry(
    monkeypatch, caplog, error, outcome
):
    post = AsyncMock(side_effect=error)
    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    client = LLMClient(base_url="https://provider.test/v1", model="test")
    try:
        assert await client.probe_readiness() == outcome
        post.assert_awaited_once()
        assert "private-endpoint" not in caplog.text
    finally:
        await client.close()


async def test_real_delayed_provider_is_unknown_within_probe_deadline():
    writers = []
    tasks = []
    reached = asyncio.Event()

    async def delayed(reader, writer):
        writers.append(writer)
        tasks.append(asyncio.current_task())
        await reader.readuntil(b"\r\n\r\n")
        reached.set()
        await asyncio.sleep(10)
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}")
        await writer.drain()

    server = await asyncio.start_server(delayed, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    client = LLMClient(base_url=f"http://127.0.0.1:{port}/v1", model="test")
    try:
        assert (
            await asyncio.wait_for(client.probe_readiness(), 7)
            == LLMReadiness.TIMED_OUT
        )
        assert reached.is_set()
        assert len(writers) == 1
    finally:
        await client.close()
        server.close()
        for task in tasks:
            task.cancel()
        await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 2)
        for writer in writers:
            writer.close()
            await asyncio.wait_for(writer.wait_closed(), 2)
        await asyncio.wait_for(server.wait_closed(), 2)


def request():
    state = SimpleNamespace(
        rate_limiter=SimpleNamespace(limit=10),
        llm_base_url="https://provider.test/v1",
        llm_api_key="",
        llm_model="test",
        searxng_url="https://search.test",
        scraper_url="https://scraper.test",
        research_memory=None,
    )
    return SimpleNamespace(app=SimpleNamespace(state=state))


async def test_unknown_probe_runs_one_generation_and_preserves_failure(monkeypatch):
    monkeypatch.setenv("RESEARCH_MEMORY_SCOPE", "global")
    probe = AsyncMock(return_value=LLMReadiness.TIMED_OUT)
    close = AsyncMock()
    monkeypatch.setattr(LLMClient, "probe_readiness", probe)
    monkeypatch.setattr(LLMClient, "close", close)
    calls = []

    async def failed_generation(**kwargs):
        calls.append(kwargs)
        yield 'event: error\ndata: {"content":"generation timed out"}\n\n'

    monkeypatch.setattr(
        "agent.research.streaming.stream_research_live", failed_generation
    )
    response = await _handle_agent_streaming(
        request(), AgentRequest(prompt="test", stream=True), None, "fingerprint", 9, 1
    )
    assert response.headers["X-LLM-Readiness"] == "timed_out"
    events = [event async for event in response.body_iterator]
    assert len(calls) == 1
    assert len(events) == 1 and "event: error" in events[0]
    assert "event: done" not in events[0]
    probe.assert_awaited_once()
    close.assert_awaited_once()


async def test_definite_probe_failure_refuses_before_generation(monkeypatch):
    monkeypatch.setattr(
        LLMClient, "probe_readiness", AsyncMock(return_value=LLMReadiness.UNAVAILABLE)
    )
    close = AsyncMock()
    monkeypatch.setattr(LLMClient, "close", close)
    with pytest.raises(HTTPException) as error:
        await _handle_agent_streaming(
            request(),
            AgentRequest(prompt="test", stream=True),
            None,
            "fingerprint",
            9,
            1,
        )
    assert error.value.status_code == 503
    assert error.value.headers["X-LLM-Readiness"] == "unavailable"
    close.assert_awaited_once()


async def test_cancelled_probe_closes_client_and_does_not_open_stream(monkeypatch):
    entered = asyncio.Event()

    async def probe(self):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(LLMClient, "probe_readiness", probe)
    close = AsyncMock()
    monkeypatch.setattr(LLMClient, "close", close)
    task = asyncio.create_task(
        _handle_agent_streaming(
            request(),
            AgentRequest(prompt="test", stream=True),
            None,
            "fingerprint",
            9,
            1,
        )
    )
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    close.assert_awaited_once()


async def test_application_preserves_readiness_header_on_http_error():
    from agent.app import create_app

    app = create_app()

    @app.get("/test-readiness-failure")
    async def failure():
        raise HTTPException(
            status_code=503,
            detail="LLM readiness probe failed.",
            headers={"X-LLM-Readiness": "rate_limited"},
        )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/test-readiness-failure")
    assert response.status_code == 503
    assert response.headers["X-LLM-Readiness"] == "rate_limited"
    assert response.json()["error"] == "LLM readiness probe failed."
