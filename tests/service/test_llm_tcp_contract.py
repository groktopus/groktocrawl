"""TCP contract tests for the LLM fixture and its real consuming client."""

from __future__ import annotations

import asyncio
import socket

import httpx
import pytest
import pytest_asyncio
import uvicorn
from agent.exceptions import (
    ProviderOutputError,
    RetryableRateLimitError,
    StructuredOutputError,
)
from agent.llm import LLMClient
from agent.research.loop import run_answer
from agent.research.utils import _validate_json_if_schema
from llm_svc.app import create_app


async def _start_fixture():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(create_app(), host="127.0.0.1", port=port, log_level="error")
    )
    task = asyncio.create_task(server.serve())
    base_url = f"http://127.0.0.1:{port}"
    async with httpx.AsyncClient(timeout=1) as probe:
        for _ in range(40):
            try:
                if (await probe.get(f"{base_url}/health")).status_code == 200:
                    return base_url, server, task
            except httpx.HTTPError:
                await asyncio.sleep(0.025)
    server.should_exit = True
    await task
    raise AssertionError("fixture Uvicorn server did not become healthy")


@pytest_asyncio.fixture
async def fixture_url():
    value = await _start_fixture()
    yield value[0]
    value[1].should_exit = True
    await value[2]


def _client(url: str, scenario: str = "default", query: str = "") -> LLMClient:
    return LLMClient(f"{url}/v1/scenarios/{scenario}{query}", model="fixture-model")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scenario",
    ["rate-limit", "server-error", "malformed-json", "empty", "refusal", "truncated"],
)
async def test_real_client_rejects_non_success_outputs(fixture_url: str, scenario: str):
    client = _client(fixture_url, scenario)
    try:
        if scenario == "rate-limit":
            with pytest.raises(RetryableRateLimitError) as error:
                await client.generate("system", "question")
            assert 1 <= (error.value.retry_after_seconds or 0) <= 60
        else:
            with pytest.raises(ProviderOutputError) as error:
                await client.generate("system", "question")
            if scenario == "truncated":
                assert error.value.error_code == "LLM_OUTPUT_TRUNCATED"
                assert error.value.details == {
                    "finish_reason": "length",
                    "refusal_present": False,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                }
            if scenario == "refusal":
                assert error.value.error_code == "LLM_REFUSAL"
                assert error.value.details["refusal_present"] is True
                assert "fixture refusal" not in str(error.value.details)
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scenario", "error_code", "finish_reason", "refusal_present"),
    [
        ("stream-truncated", "LLM_OUTPUT_TRUNCATED", "length", False),
        ("stream-refusal", "LLM_REFUSAL", "other", True),
    ],
)
async def test_real_client_classifies_stream_output_failures(
    fixture_url: str,
    scenario: str,
    error_code: str,
    finish_reason: str,
    refusal_present: bool,
):
    client = _client(fixture_url, scenario)
    try:
        events = [event async for event in client.generate_stream("system", "question")]
        assert events[-1]["type"] == "error"
        assert events[-1]["error_code"] == error_code
        assert events[-1]["details"]["finish_reason"] == finish_reason
        assert events[-1]["details"]["refusal_present"] is refusal_present
        if scenario == "stream-truncated":
            assert events[-1]["details"]["completion_tokens"] == 8192
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_real_client_echo_and_schema_invalid_output_over_tcp(fixture_url: str):
    echo = _client(fixture_url, "echo")
    invalid = _client(fixture_url, "schema-invalid")
    try:
        assert '"model": "fixture-model"' in await echo.generate("system", "question")
        with pytest.raises(StructuredOutputError):
            _validate_json_if_schema(
                await invalid.generate(
                    "system",
                    "question",
                    schema={"type": "object", "required": ["expected"]},
                ),
                {"type": "object", "required": ["expected"]},
            )
    finally:
        await echo.close()
        await invalid.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("chunks", [1, 4])
async def test_real_client_streams_multiple_chunkings(fixture_url: str, chunks: int):
    client = _client(fixture_url, "streaming", f"?chunks={chunks}")
    try:
        events = [event async for event in client.generate_stream("system", "question")]
        assert events[-1]["type"] == "done"
        assert events[-1]["full_content"]
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_default_scenario_honors_streaming_request(fixture_url: str):
    client = _client(fixture_url)
    try:
        events = [event async for event in client.generate_stream("system", "question")]
        assert events[-1]["type"] == "done"
        assert events[-1]["full_content"] == (
            "Synthesized answer from provided context."
        )
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_streaming_and_polling_normalize_to_same_artifact(fixture_url: str):
    polling = _client(fixture_url)
    streaming = _client(fixture_url, "streaming", "?chunks=7")
    try:
        polled = await polling.generate("system", "question")
        events = [
            event async for event in streaming.generate_stream("system", "question")
        ]
        assert events[-1]["type"] == "done"
        assert events[-1]["full_content"] == polled
        assert (
            "".join(event["content"] for event in events if event["type"] == "token")
            == polled
        )
    finally:
        await polling.close()
        await streaming.close()


@pytest.mark.asyncio
async def test_real_client_timeout_is_typed_and_bounded(fixture_url: str):
    client = _client(fixture_url, "timeout")
    client._client.timeout = httpx.Timeout(0.05)
    try:
        with pytest.raises(ProviderOutputError, match="transport failed"):
            await asyncio.wait_for(client.generate("system", "question"), timeout=0.5)
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["stream-malformed", "stream-truncated"])
async def test_real_client_stream_failures_are_explicit(
    fixture_url: str, scenario: str
):
    client = _client(fixture_url, scenario)
    try:
        events = [event async for event in client.generate_stream("system", "question")]
        assert events[-1]["type"] == "error"
        assert events[-1]["classification"] in {"malformed", "truncated"}
        assert not any(event["type"] == "done" for event in events)
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["rate-limit", "server-error"])
async def test_real_client_stream_upstream_failures_are_classified(
    fixture_url: str, scenario: str
):
    client = _client(fixture_url, scenario)
    try:
        events = [event async for event in client.generate_stream("system", "question")]
        assert events[-1]["type"] == "error"
        assert events[-1]["classification"] in {"retryable", "non_retryable"}
        assert not any(event["type"] == "done" for event in events)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_answer_consumes_real_rate_limit_and_503(fixture_url: str, monkeypatch):
    async def discovered(**kwargs):
        return {"context": "source context", "source_map": [{"url": "https://source"}]}

    monkeypatch.setattr(
        "agent.research.loop._run_answer_discover_and_scrape", discovered
    )
    with pytest.raises(RetryableRateLimitError):
        await run_answer(
            "question",
            llm_base_url=f"{fixture_url}/v1/scenarios/rate-limit",
            llm_model="fixture",
        )
    with pytest.raises(ProviderOutputError):
        await run_answer(
            "question",
            llm_base_url=f"{fixture_url}/v1/scenarios/server-error",
            llm_model="fixture",
        )
