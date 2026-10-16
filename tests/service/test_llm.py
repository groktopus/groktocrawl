"""Tests for agent-svc/agent/llm.py — LLMClient.

Tests message formatting, streaming, and error handling
using mocked HTTP responses.
"""

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from agent.settings import load_settings


@pytest.fixture
def llm():
    from agent.llm import LLMClient

    return LLMClient(
        base_url="http://llm.test/v1", api_key="test-key", model="test-model"
    )


class TestLLMClientInit:
    def test_strips_trailing_slash(self):
        from agent.llm import LLMClient

        client = LLMClient(base_url="http://example.com/v1/", model="test-model")
        assert client.base_url == "http://example.com/v1"

    def test_constructing_without_model_raises_value_error(self):
        from agent.llm import LLMClient

        with pytest.raises(ValueError, match="model is required"):
            LLMClient()


def _make_response(status_code=200, json_data=None, text=""):
    """Create a mock httpx response for LLM generate()."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    resp.text = text
    return resp


class TestLLMClientGenerate:
    @pytest.mark.asyncio
    async def test_output_budget_preserves_mainline_default_and_legacy_override(
        self, llm, monkeypatch
    ):
        monkeypatch.delenv("LLM_MAX_OUTPUT_TOKENS", raising=False)
        load_settings.cache_clear()
        response = _make_response(
            json_data={"choices": [{"message": {"content": "ok"}}]}
        )
        try:
            with patch.object(llm._client, "post", return_value=response) as post:
                assert await llm.generate("system", "user") == "ok"
                assert post.call_args.kwargs["json"]["max_tokens"] == 8192
            monkeypatch.setenv("LLM_MAX_OUTPUT_TOKENS", "32768")
            load_settings.cache_clear()
            with patch.object(llm._client, "post", return_value=response) as post:
                assert await llm.generate("system", "user") == "ok"
                assert post.call_args.kwargs["json"]["max_tokens"] == 32768
        finally:
            load_settings.cache_clear()

    @pytest.mark.asyncio
    async def test_successful_generation(self, llm):
        with patch.object(
            llm._client,
            "post",
            return_value=_make_response(
                json_data={"choices": [{"message": {"content": "Hello world!"}}]}
            ),
        ):
            result = await llm.generate(
                system_prompt="Be helpful.", user_prompt="Say hi."
            )
            assert result == "Hello world!"

    @pytest.mark.asyncio
    async def test_includes_context_when_provided(self, llm):
        with patch.object(
            llm._client,
            "post",
            return_value=_make_response(
                json_data={"choices": [{"message": {"content": "Answer"}}]}
            ),
        ) as mock_post:
            result = await llm.generate(
                system_prompt="Be helpful.",
                user_prompt="What do you see?",
                context="The sky is blue.\n\nThe grass is green.",
            )
            assert result == "Answer"
            call_kwargs = mock_post.call_args[1]
            body = call_kwargs["json"]
            assert len(body["messages"]) == 2
            user_msg = body["messages"][1]
            assert "The sky is blue." in user_msg["content"]

    @pytest.mark.asyncio
    async def test_includes_schema_in_system_prompt(self, llm):
        schema = {"type": "object", "properties": {"key": {"type": "string"}}}
        with patch.object(
            llm._client,
            "post",
            return_value=_make_response(
                json_data={"choices": [{"message": {"content": '{"key": "value"}'}}]}
            ),
        ) as mock_post:
            result = await llm.generate(
                system_prompt="Extract data.",
                user_prompt="Extract from this.",
                schema=schema,
            )
            assert result == '{"key": "value"}'
            body = mock_post.call_args[1]["json"]
            # json_object mode (not json_schema) for provider compatibility
            assert body["response_format"]["type"] == "json_object"
            # Schema injected into system prompt as fallback
            assert (
                "You MUST respond with valid JSON matching this schema"
                in body["messages"][0]["content"]
            )

    @pytest.mark.asyncio
    async def test_empty_schema_treated_as_no_schema(self, llm):
        """Empty schema {} should NOT send response_format to the LLM."""
        with patch.object(
            llm._client,
            "post",
            return_value=_make_response(
                json_data={"choices": [{"message": {"content": "Prose answer"}}]}
            ),
        ) as mock_post:
            result = await llm.generate(
                system_prompt="Be helpful.",
                user_prompt="Say hi.",
                schema={},
            )
            assert result == "Prose answer"
            body = mock_post.call_args[1]["json"]
            assert "response_format" not in body
            # Schema injection should NOT appear in system prompt
            assert (
                "You MUST respond with valid JSON" not in body["messages"][0]["content"]
            )

    @pytest.mark.asyncio
    async def test_no_response_format_when_schema_none(self, llm):
        """When schema is None, no response_format should be sent."""
        with patch.object(
            llm._client,
            "post",
            return_value=_make_response(
                json_data={"choices": [{"message": {"content": "Hello"}}]}
            ),
        ) as mock_post:
            result = await llm.generate(system_prompt="x", user_prompt="y")
            assert result == "Hello"
            body = mock_post.call_args[1]["json"]
            assert "response_format" not in body

    @pytest.mark.asyncio
    async def test_sets_authorization_header(self, llm):
        with patch.object(
            llm._client,
            "post",
            return_value=_make_response(
                json_data={"choices": [{"message": {"content": "ok"}}]}
            ),
        ) as mock_post:
            await llm.generate(system_prompt="x", user_prompt="y")
            headers = mock_post.call_args[1]["headers"]
            assert headers["Authorization"] == "Bearer test-key"

    @pytest.mark.asyncio
    async def test_no_auth_when_key_empty(self):
        from agent.llm import LLMClient

        no_key = LLMClient(base_url="http://test/v1", api_key="", model="test-model")
        with patch.object(
            no_key._client,
            "post",
            return_value=_make_response(
                json_data={"choices": [{"message": {"content": "ok"}}]}
            ),
        ) as mock_post:
            await no_key.generate(system_prompt="x", user_prompt="y")
            headers = mock_post.call_args[1]["headers"]
            assert "Authorization" not in headers

    @pytest.mark.asyncio
    async def test_classifies_rate_limit_as_retryable(self, llm):
        from agent.exceptions import RetryableRateLimitError

        response = _make_response(status_code=429, text="Rate limited")
        response.headers = {"Retry-After": "2"}
        with patch.object(
            llm._client,
            "post",
            return_value=response,
        ):
            with pytest.raises(RetryableRateLimitError) as exc_info:
                await llm.generate(system_prompt="x", user_prompt="y")
            assert exc_info.value.retry_after_seconds == 2

    @pytest.mark.asyncio
    async def test_non_retryable_api_error_raises_typed_error(self, llm):
        from agent.exceptions import ProviderOutputError

        with patch.object(
            llm._client,
            "post",
            return_value=_make_response(status_code=503, text="Unavailable"),
        ):
            with pytest.raises(ProviderOutputError):
                await llm.generate(system_prompt="x", user_prompt="y")

    @pytest.mark.asyncio
    async def test_non_object_message_raises_typed_error(self, llm):
        from agent.exceptions import ProviderOutputError

        with patch.object(
            llm._client,
            "post",
            return_value=_make_response(
                json_data={"choices": [{"message": None, "finish_reason": "stop"}]}
            ),
        ):
            with pytest.raises(ProviderOutputError):
                await llm.generate(system_prompt="x", user_prompt="y")

    @pytest.mark.asyncio
    async def test_handles_network_error(self, llm):
        import httpx
        from agent.exceptions import ProviderOutputError

        with patch.object(
            llm._client, "post", side_effect=httpx.ConnectError("Connection refused")
        ):
            with pytest.raises(ProviderOutputError, match="transport failed"):
                await llm.generate(system_prompt="x", user_prompt="y")

    @pytest.mark.asyncio
    async def test_thinking_omitted_by_default(self, llm):
        with patch.object(
            llm._client,
            "post",
            return_value=_make_response(
                json_data={"choices": [{"message": {"content": "ok"}}]}
            ),
        ) as mock_post:
            await llm.generate(system_prompt="x", user_prompt="y")
            body = mock_post.call_args[1]["json"]
            assert "enable_thinking" not in body
            assert "chat_template_kwargs" not in body

    @pytest.mark.asyncio
    async def test_thinking_enabled_via_env(self):
        from agent.settings import load_settings as agent_load_settings

        try:
            agent_load_settings.cache_clear()
            with patch.dict(os.environ, {"LLM_ENABLE_THINKING": "true"}, clear=False):
                agent_load_settings.cache_clear()
                from agent.llm import LLMClient

                client = LLMClient(base_url="http://test/v1", api_key="k", model="ds")
                with patch.object(
                    client._client,
                    "post",
                    return_value=_make_response(
                        json_data={"choices": [{"message": {"content": "ok"}}]}
                    ),
                ) as mock_post:
                    await client.generate(system_prompt="x", user_prompt="y")
                    body = mock_post.call_args[1]["json"]
                    assert body.get("enable_thinking") is True
        finally:
            agent_load_settings.cache_clear()

    @pytest.mark.asyncio
    async def test_close(self, llm):
        with patch.object(llm._client, "aclose") as mock_close:
            await llm.close()
            mock_close.assert_called_once()


class TestLLMClientGenerateStream:
    @staticmethod
    def _setup_stream_mock(lines, status_code=200):
        """Create a properly nested mock for the httpx stream pattern."""

        async def async_iter():
            for line in lines:
                yield line

        async def async_read():
            return b"Error"

        mock_resp = MagicMock()
        mock_resp.status_code = status_code
        mock_resp.aiter_lines = async_iter
        if status_code != 200:
            mock_resp.aread = async_read
        mock_resp.__aenter__.return_value = mock_resp
        mock_resp.__aexit__.return_value = None

        mock_client = MagicMock()
        mock_client.stream.return_value = mock_resp
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None

        mock_client_cls = MagicMock()
        mock_client_cls.return_value = mock_client

        return mock_client_cls, mock_client

    @pytest.mark.asyncio
    async def test_yields_tokens(self, llm):
        mock_client_cls, _ = self._setup_stream_mock(
            [
                'data: {"choices":[{"delta":{"content":"Hello"}}]}',
                'data: {"choices":[{"delta":{"content":" "}}]}',
                'data: {"choices":[{"delta":{"content":"world"}}]}',
                "data: [DONE]",
            ]
        )

        with patch("httpx.AsyncClient", mock_client_cls):
            tokens = []
            async for event in llm.generate_stream(system_prompt="x", user_prompt="y"):
                tokens.append(event)

            assert len(tokens) == 4
            assert tokens[0] == {"type": "token", "content": "Hello"}
            assert tokens[1] == {"type": "token", "content": " "}
            assert tokens[2] == {"type": "token", "content": "world"}
            assert tokens[3]["type"] == "done"
            assert "Hello world" in tokens[3]["full_content"]

    @pytest.mark.asyncio
    async def test_stream_length_stop_is_not_a_completed_answer(self, llm):
        mock_client_cls, _ = self._setup_stream_mock(
            [
                'data: {"choices":[{"delta":{"content":"Partial"}}]}',
                'data: {"choices":[{"delta":{},"finish_reason":"length"}]}',
                "data: [DONE]",
            ]
        )
        with patch("httpx.AsyncClient", mock_client_cls):
            events = [event async for event in llm.generate_stream("x", "y")]
        assert events[0] == {"type": "token", "content": "Partial"}
        assert events[-1]["type"] == "error"
        assert events[-1]["classification"] == "truncated"
        assert not any(event["type"] == "done" for event in events)

    @pytest.mark.asyncio
    async def test_stream_output_budget_preserves_mainline_default_and_legacy_override(
        self, llm, monkeypatch
    ):
        mock_client_cls, mock_client = self._setup_stream_mock(["data: [DONE]"])
        monkeypatch.delenv("LLM_MAX_OUTPUT_TOKENS", raising=False)
        load_settings.cache_clear()
        try:
            with patch("httpx.AsyncClient", mock_client_cls):
                _ = [event async for event in llm.generate_stream("x", "y")]
                assert mock_client.stream.call_args.kwargs["json"]["max_tokens"] == 8192
                monkeypatch.setenv("LLM_MAX_OUTPUT_TOKENS", "32768")
                load_settings.cache_clear()
                _ = [event async for event in llm.generate_stream("x", "y")]
                assert (
                    mock_client.stream.call_args.kwargs["json"]["max_tokens"] == 32768
                )
        finally:
            load_settings.cache_clear()

    @pytest.mark.asyncio
    async def test_yields_error_on_non_200(self, llm):
        mock_client_cls, _ = self._setup_stream_mock([], status_code=500)

        with patch("httpx.AsyncClient", mock_client_cls):
            events = []
            async for event in llm.generate_stream(system_prompt="x", user_prompt="y"):
                events.append(event)
            assert len(events) == 1
            assert events[0]["type"] == "error"
            assert "500" in events[0]["content"]

    @pytest.mark.asyncio
    async def test_yields_error_on_exception(self, llm):
        import httpx

        mock_client = MagicMock()
        mock_client.stream.side_effect = httpx.ConnectError("timeout")
        mock_client.__aenter__.return_value = mock_client

        mock_client_cls = MagicMock()
        mock_client_cls.return_value = mock_client

        with patch("httpx.AsyncClient", mock_client_cls):
            events = []
            async for event in llm.generate_stream(system_prompt="x", user_prompt="y"):
                events.append(event)
            assert len(events) == 1
            assert events[0]["type"] == "error"

    @pytest.mark.asyncio
    async def test_rejects_invalid_json_lines(self, llm):
        mock_client_cls, _ = self._setup_stream_mock(
            [
                "data: invalid json",
                'data: {"choices":[{"delta":{"content":"ok"}}]}',
                "data: [DONE]",
            ]
        )

        with patch("httpx.AsyncClient", mock_client_cls):
            tokens = []
            async for event in llm.generate_stream(system_prompt="x", user_prompt="y"):
                tokens.append(event)
            assert len(tokens) == 1
            assert tokens[0]["type"] == "error"
            assert tokens[0]["classification"] == "malformed"

    @pytest.mark.asyncio
    async def test_rejects_non_object_sse_choice(self, llm):
        mock_client_cls, _ = self._setup_stream_mock(
            ['data: {"choices":[null]}', "data: [DONE]"]
        )

        with patch("httpx.AsyncClient", mock_client_cls):
            events = [
                event
                async for event in llm.generate_stream(
                    system_prompt="x", user_prompt="y"
                )
            ]
        assert events == [
            {
                "type": "error",
                "classification": "malformed",
                "content": "LLM provider returned malformed SSE",
            }
        ]

    @pytest.mark.asyncio
    async def test_includes_context(self, llm):
        mock_client_cls, mock_client = self._setup_stream_mock(
            [
                'data: {"choices":[{"delta":{"content":"answer"}}]}',
                "data: [DONE]",
            ]
        )

        with patch("httpx.AsyncClient", mock_client_cls):
            async for _event in llm.generate_stream(
                system_prompt="x", user_prompt="what?", context="Some context here."
            ):
                pass

            body = mock_client.stream.call_args[1]["json"]
            user_msg = body["messages"][1]["content"]
            assert "Some context here." in user_msg
            assert "chat_template_kwargs" not in body

    @pytest.mark.asyncio
    async def test_schema_mode_delegates_to_generate(self, llm):
        """When schema is provided, generate_stream calls generate() non-streaming."""
        schema = {"type": "object", "properties": {"result": {"type": "string"}}}
        with patch.object(
            llm._client,
            "post",
            return_value=_make_response(
                json_data={
                    "choices": [{"message": {"content": '{"result": "structured"}'}}]
                }
            ),
        ):
            events = []
            async for event in llm.generate_stream(
                system_prompt="Extract.",
                user_prompt="Get data.",
                schema=schema,
            ):
                events.append(event)

            assert len(events) == 1
            assert events[0]["type"] == "done"
            assert events[0]["full_content"] == '{"result": "structured"}'

    @pytest.mark.asyncio
    async def test_schema_mode_error_propagates(self, llm):
        """When schema + generate() fails, generate_stream yields a typed error event."""
        schema = {"type": "object", "properties": {"result": {"type": "string"}}}
        with patch.object(
            llm._client,
            "post",
            return_value=_make_response(status_code=500, text="Server error"),
        ):
            events = [
                event
                async for event in llm.generate_stream(
                    system_prompt="Extract.",
                    user_prompt="Get data.",
                    schema=schema,
                )
            ]
            assert events == [
                {
                    "type": "error",
                    "classification": "non_retryable",
                    "content": "LLM provider returned HTTP 500",
                }
            ]

    @pytest.mark.asyncio
    async def test_empty_schema_streams_normally(self, llm):
        """Empty schema {} should NOT trigger schema mode — stream as usual."""
        mock_client_cls, _mock_client = self._setup_stream_mock(
            [
                'data: {"choices":[{"delta":{"content":"normal"}}]}',
                "data: [DONE]",
            ]
        )

        with patch("httpx.AsyncClient", mock_client_cls):
            tokens = []
            async for event in llm.generate_stream(
                system_prompt="x",
                user_prompt="y",
                schema={},
            ):
                tokens.append(event)

            # Should stream normally (not delegate to generate)
            assert tokens[0]["type"] == "token"
            assert tokens[0]["content"] == "normal"
            assert tokens[1]["type"] == "done"


@pytest.mark.asyncio
async def test_llama_cpp_disable_thinking_generate():
    """When LLM_LLAMA_CPP_DISABLE_THINKING=true, chat_template_kwargs is set (generate)."""
    from agent.llm import LLMClient

    load_settings.cache_clear()
    try:
        with patch.dict(
            os.environ,
            {"LLM_LLAMA_CPP_DISABLE_THINKING": "true"},
            clear=False,
        ):
            load_settings.cache_clear()
            client = LLMClient(
                base_url="http://test/v1", api_key="k", model="test-model"
            )
            with patch.object(
                client._client,
                "post",
                return_value=_make_response(
                    json_data={"choices": [{"message": {"content": "ok"}}]}
                ),
            ) as mock_post:
                await client.generate(system_prompt="x", user_prompt="y")
                body = mock_post.call_args[1]["json"]
                assert body.get("chat_template_kwargs") == {"enable_thinking": False}
    finally:
        load_settings.cache_clear()


@pytest.mark.asyncio
async def test_llama_cpp_disable_thinking_stream():
    """When LLM_LLAMA_CPP_DISABLE_THINKING=true, chat_template_kwargs is set (stream)."""
    from agent.llm import LLMClient

    load_settings.cache_clear()
    try:
        with patch.dict(
            os.environ,
            {"LLM_LLAMA_CPP_DISABLE_THINKING": "true"},
            clear=False,
        ):
            load_settings.cache_clear()
            client = LLMClient(
                base_url="http://test/v1", api_key="k", model="test-model"
            )
            mock_client_cls, mock_client = (
                TestLLMClientGenerateStream._setup_stream_mock(
                    [
                        'data: {"choices":[{"delta":{"content":"ok"}}]}',
                        "data: [DONE]",
                    ]
                )
            )

            with patch("httpx.AsyncClient", mock_client_cls):
                async for _event in client.generate_stream(
                    system_prompt="x", user_prompt="y"
                ):
                    pass

                body = mock_client.stream.call_args[1]["json"]
                assert body.get("chat_template_kwargs") == {"enable_thinking": False}
    finally:
        load_settings.cache_clear()


# ─────────────────────────────────────────────────────────────────────────────
# Issue #589 — configurable LLM call timeout (LLM_CALL_TIMEOUT)
#
# Contract under test:
#   * AgentSettings.llm_call_timeout (alias LLM_CALL_TIMEOUT, float, default 120.0).
#   * Both LLM httpx.AsyncClient construction sites honor the setting
#     (persistent client in __init__, per-call client in the schema-less
#     generate_stream path); the check_health probe stays at its deliberate 5s.
#   * Unset env preserves today's behavior exactly (120s / 5s envelopes).
#   * Timeout failures log type(exc).__name__ so empty-str exceptions
#     (httpx read timeouts) stay diagnosable.
#
# Time semantics: an httpx scalar timeout=T bounds per-operation idleness
# (connect/read/write), NOT whole-request duration. Tests use scaled-down
# REAL delays against real sockets/servers — never virtual-clock mocking,
# which httpx's timeout machinery cannot see.
# ─────────────────────────────────────────────────────────────────────────────

import asyncio
import json as _json
import logging
import socket
import time as _time
from pathlib import Path

import httpx
import uvicorn
from agent.exceptions import ProviderOutputError
from agent.settings import AgentSettings
from llm_svc.app import create_app
from pydantic import ValidationError


class TestLLMCallTimeoutSetting:
    """Settings-field wiring: alias, float coercion, validation (VAL-LLM-013)."""

    def test_settings_default_is_120_when_unset(self, monkeypatch):
        monkeypatch.delenv("LLM_CALL_TIMEOUT", raising=False)
        load_settings.cache_clear()
        try:
            settings = load_settings()
            assert settings.llm_call_timeout == 120.0
            assert isinstance(settings.llm_call_timeout, float)

            direct = AgentSettings()
            assert direct.llm_call_timeout == 120.0
            assert isinstance(direct.llm_call_timeout, float)
        finally:
            load_settings.cache_clear()

    def test_settings_env_flows_with_float_coercion(self, monkeypatch):
        monkeypatch.setenv("LLM_CALL_TIMEOUT", "300")
        load_settings.cache_clear()
        try:
            settings = load_settings()
            assert settings.llm_call_timeout == 300.0
            assert isinstance(settings.llm_call_timeout, float)

            direct = AgentSettings.model_validate(dict(os.environ))
            assert direct.llm_call_timeout == 300.0
        finally:
            load_settings.cache_clear()

    def test_settings_invalid_value_raises_validation_error(self, monkeypatch):
        """Malformed values must fail loudly, not silently fall back."""
        monkeypatch.setenv("LLM_CALL_TIMEOUT", "abc")
        load_settings.cache_clear()
        try:
            with pytest.raises(ValidationError):
                AgentSettings.model_validate(dict(os.environ))
        finally:
            load_settings.cache_clear()


class _CapturedAsyncClient(httpx.AsyncClient):
    """Real httpx.AsyncClient that records constructor kwargs."""

    last_init_kwargs: dict | None = None

    def __init__(self, **kwargs):
        type(self).last_init_kwargs = dict(kwargs)
        super().__init__(**kwargs)


def _capture_async_client_kwargs():
    """Patch httpx.AsyncClient with a recording-but-real subclass."""
    return patch("httpx.AsyncClient", _CapturedAsyncClient)


async def _drain_schemaless_stream(client) -> list[dict]:
    """Drive the schema-less generate_stream path against a dead endpoint."""
    return [
        event
        async for event in client.generate_stream(system_prompt="x", user_prompt="y")
    ]


class TestLLMClientTimeoutWiring:
    """LLM_CALL_TIMEOUT reaches both AsyncClient construction sites."""

    @pytest.mark.asyncio
    async def test_unset_env_defaults_to_120_at_both_sites(self, monkeypatch):
        """VAL-LLM-002: unset env → exactly today's 120s at both sites."""
        monkeypatch.delenv("LLM_CALL_TIMEOUT", raising=False)
        load_settings.cache_clear()
        try:
            from agent.llm import LLMClient

            with _capture_async_client_kwargs():
                client = LLMClient(base_url="http://127.0.0.1:9/v1", model="test-model")
            init_kwargs = dict(_CapturedAsyncClient.last_init_kwargs)
            assert _effective_timeout_seconds(client._client.timeout) == 120.0

            with _capture_async_client_kwargs():
                # The schema-less stream path yields an error event on
                # transport failure; the construction site is still reached.
                events = await asyncio.wait_for(
                    _drain_schemaless_stream(client), timeout=10
                )
            assert events and events[-1]["type"] == "error"
            stream_kwargs = dict(_CapturedAsyncClient.last_init_kwargs)

            assert init_kwargs.get("timeout") == 120
            assert stream_kwargs.get("timeout") == 120
            assert float(client._client.timeout.connect) == 120.0
            assert float(client._client.timeout.read) == 120.0

            await client.close()
        finally:
            load_settings.cache_clear()

    @pytest.mark.asyncio
    async def test_env_flows_into_init_client(self, monkeypatch):
        """VAL-LLM-003: LLM_CALL_TIMEOUT=300 → persistent client timeout 300."""
        monkeypatch.setenv("LLM_CALL_TIMEOUT", "300")
        load_settings.cache_clear()
        try:
            from agent.llm import LLMClient

            with _capture_async_client_kwargs():
                client = LLMClient(base_url="http://127.0.0.1:9/v1", model="test-model")
            assert _effective_timeout_seconds(client._client.timeout) == 300.0
            assert _CapturedAsyncClient.last_init_kwargs.get("timeout") == 300
            await client.close()
        finally:
            load_settings.cache_clear()

    @pytest.mark.asyncio
    async def test_env_flows_into_generate_stream_client(self, monkeypatch):
        """VAL-LLM-004: schema-less stream path builds its client at 300 too."""
        monkeypatch.setenv("LLM_CALL_TIMEOUT", "300")
        load_settings.cache_clear()
        try:
            from agent.llm import LLMClient

            client = LLMClient(base_url="http://127.0.0.1:9/v1", model="test-model")
            with _capture_async_client_kwargs():
                events = await asyncio.wait_for(
                    _drain_schemaless_stream(client), timeout=10
                )
            assert events and events[-1]["type"] == "error"
            assert _CapturedAsyncClient.last_init_kwargs.get("timeout") == 300
            await client.close()
        finally:
            load_settings.cache_clear()


class TestCheckHealthProbeTimeoutInvariant:
    """VAL-LLM-010: check_health's probe stays at its deliberate 5s."""

    @staticmethod
    async def _probe_construction_timeout(monkeypatch, env_value: str | None) -> float:
        monkeypatch.delenv("LLM_CALL_TIMEOUT", raising=False)
        if env_value is not None:
            monkeypatch.setenv("LLM_CALL_TIMEOUT", env_value)
        load_settings.cache_clear()
        try:
            from agent.llm import LLMClient

            captured: dict = {}

            class RecordingClient(httpx.AsyncClient):
                def __init__(self, **kwargs):
                    captured.update(kwargs)
                    super().__init__(**kwargs)

            client = LLMClient(base_url="http://llm.test/v1", model="probe-model")
            with patch("httpx.AsyncClient", RecordingClient):
                # Dead port: the probe fails fast either way; we only care
                # about the timeout the probe client was constructed with.
                client.base_url = "http://127.0.0.1:9/v1"
                await client.check_health()

            timeout = captured["timeout"]
            assert _effective_timeout_seconds(timeout) == 5.0
            return _effective_timeout_seconds(timeout)
        finally:
            load_settings.cache_clear()

    @pytest.mark.asyncio
    async def test_check_health_stays_5s_when_llm_call_timeout_set(self, monkeypatch):
        assert await self._probe_construction_timeout(monkeypatch, "300") == 5.0

    @pytest.mark.asyncio
    async def test_check_health_stays_5s_when_llm_call_timeout_unset(self, monkeypatch):
        assert await self._probe_construction_timeout(monkeypatch, None) == 5.0


# ── Real-socket slow-provider harness (scaled delays, no virtual clock) ────


async def _start_raw_sse_fixture():
    """Boot the llm-svc fixture app plus a raw-SSE stall/drip test route.

    The fixture app's own scenarios cap delays at 2s and cannot hold a
    stream silent mid-flight, so the timeout semantics tests add a route
    that speaks plain OpenAI-style SSE with controllable timing, driven by
    an in-process state handle (query strings cannot be injected through
    the client's fixed completion URL):

      * ``stall_after=N``: emit N token chunks instantly, then go silent
        forever (mid-stream stall → quiet-period read timeout).
      * ``drip_chunks``/``drip_interval``: emit chunks every interval so
        cumulative duration exceeds the call timeout while per-chunk
        quiet time never does (no aggregate deadline).
      * ``quiet_seconds``: pre-[DONE] silence for the envelope tests.

    The route lives at ``/v1/raw-sse/chat/completions`` so a client whose
    base_url ends at ``/v1/raw-sse`` reaches it via the standard
    ``_completion_url()`` path construction.
    """
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    app = create_app()
    timing_state: dict = {
        "stall_after": 0,
        "quiet_seconds": 0.0,
        "drip_chunks": 0,
        "drip_interval": 0.0,
    }

    from fastapi.responses import StreamingResponse

    @app.post("/v1/raw-sse/chat/completions")
    async def raw_sse_route():
        async def gen():
            async def sse(token: str) -> str:
                payload = {
                    "id": "chatcmpl-raw",
                    "object": "chat.completion.chunk",
                    "model": "fixture-model",
                    "choices": [{"index": 0, "delta": {"content": token}}],
                }
                return f"data: {_json.dumps(payload)}\n\n"

            for i in range(
                max(timing_state["stall_after"], timing_state["drip_chunks"])
            ):
                yield await sse(f"t{i} ")
                if timing_state["drip_interval"]:
                    await asyncio.sleep(timing_state["drip_interval"])
            if not timing_state["drip_chunks"]:
                await asyncio.sleep(timing_state["quiet_seconds"])
            yield "data: [DONE]\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream")

    class _Handle:
        base_url = f"http://127.0.0.1:{port}/v1/raw-sse"

        @staticmethod
        def configure(**kwargs) -> None:
            timing_state.update(kwargs)

    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    )
    task = asyncio.create_task(server.serve())
    health_url = f"http://127.0.0.1:{port}"
    async with httpx.AsyncClient(timeout=2) as probe_client:
        for _ in range(80):
            try:
                if (await probe_client.get(f"{health_url}/health")).status_code == 200:
                    return _Handle(), server, task
            except httpx.HTTPError:
                await asyncio.sleep(0.025)
    server.should_exit = True
    await task
    raise AssertionError("raw-SSE fixture did not become healthy")


@pytest_asyncio.fixture
async def raw_sse():
    handle, server, task = await _start_raw_sse_fixture()
    try:
        yield handle
    finally:
        server.should_exit = True
        await task


def _make_slow_fixture(delay_ms: int):
    """Build a fixture-url factory honoring ?delay_ms= (max 2000).

    Uses ``pytest_asyncio.fixture`` (like test_llm_tcp_contract.py) so the
    async generator fixture works under BOTH asyncio_mode=auto (local
    pyproject) and the strict default mode inside the CI agent container.
    """

    @pytest_asyncio.fixture
    async def url():
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        server = uvicorn.Server(
            uvicorn.Config(create_app(), host="127.0.0.1", port=port, log_level="error")
        )
        task = asyncio.create_task(server.serve())
        base_url = f"http://127.0.0.1:{port}"
        async with httpx.AsyncClient(timeout=2) as probe_client:
            for _ in range(80):
                try:
                    if (
                        await probe_client.get(f"{base_url}/health")
                    ).status_code == 200:
                        break
                except httpx.HTTPError:
                    await asyncio.sleep(0.025)
        try:
            yield f"{base_url}/v1/scenarios/default?delay_ms={delay_ms}"
        finally:
            server.should_exit = True
            await task

    return url


slow_1500ms_url = _make_slow_fixture(1500)
over_default_url = _make_slow_fixture(2000)


class TestSlowProviderTimeoutEnvelope:
    """Scaled-down real-delay provider behavior across the timeout boundary."""

    @pytest.mark.asyncio
    async def test_slow_provider_within_raised_timeout_succeeds(
        self, slow_1500ms_url, monkeypatch
    ):
        """VAL-LLM-001/005: provider slower than the unset default regime but
        faster than the configured timeout completes successfully.

        Real delay D=1500ms < configured T=3000ms; the legacy hardcoded
        120s would also pass this, so the boundary twin below proves the
        knob actually binds at smaller values.
        """
        monkeypatch.setenv("LLM_CALL_TIMEOUT", "3")
        load_settings.cache_clear()
        try:
            from agent.llm import LLMClient

            client = LLMClient(slow_1500ms_url, api_key="", model="fixture-model")
            started = _time.monotonic()
            result = await asyncio.wait_for(
                client.generate(system_prompt="Be helpful.", user_prompt="Say hi."),
                timeout=10,
            )
            elapsed = _time.monotonic() - started
            assert "Synthesized answer from provided context." in result
            assert 1.5 <= elapsed < 3.0
            await client.close()
        finally:
            load_settings.cache_clear()

    @pytest.mark.asyncio
    async def test_call_over_configured_timeout_times_out_cleanly(
        self, slow_1500ms_url, monkeypatch
    ):
        """Twin boundary: same provider, tighter configured timeout fails."""
        monkeypatch.setenv("LLM_CALL_TIMEOUT", "0.05")
        load_settings.cache_clear()
        try:
            from agent.llm import LLMClient

            client = LLMClient(slow_1500ms_url, api_key="", model="fixture-model")
            with pytest.raises(ProviderOutputError):
                await asyncio.wait_for(
                    client.generate(system_prompt="x", user_prompt="y"), timeout=10
                )
            await client.close()
        finally:
            load_settings.cache_clear()

    @pytest.mark.asyncio
    async def test_over_default_generate_fails_via_provider_output_error_envelope(
        self, over_default_url, monkeypatch
    ):
        """VAL-LLM-006 (generate side): with LLM_CALL_TIMEOUT unset, a call
        exceeding the 120s default fails via the path-appropriate envelope —
        ProviderOutputError raised (never a success string, never a hang).

        The full 120s wait is not exercised; the envelope identity is proven
        by binding the client to a small timeout, which drives the identical
        transport-failure branch.
        """
        monkeypatch.delenv("LLM_CALL_TIMEOUT", raising=False)
        load_settings.cache_clear()
        try:
            from agent.llm import LLMClient

            client = LLMClient(over_default_url, api_key="", model="fixture-model")
            assert _effective_timeout_seconds(client._client.timeout) == 120.0
            client._client.timeout = httpx.Timeout(0.05)
            with pytest.raises(ProviderOutputError):
                await asyncio.wait_for(
                    client.generate(system_prompt="x", user_prompt="y"),
                    timeout=2.5,
                )
            await client.close()
        finally:
            load_settings.cache_clear()

    @pytest.mark.asyncio
    async def test_over_default_stream_fails_via_error_event_envelope(
        self, over_default_url, monkeypatch
    ):
        """VAL-LLM-006 (stream side): unset env, over-default stream surfaces
        an {"type": "error"} event (no exception raised to the caller)."""
        monkeypatch.delenv("LLM_CALL_TIMEOUT", raising=False)
        load_settings.cache_clear()
        try:
            from agent.llm import LLMClient

            client = LLMClient(over_default_url, api_key="", model="fixture-model")

            async def timed_out_stream():
                # generate_stream's contract is event-based: a transport
                # timeout yields an error event rather than raising. Bind
                # the per-call client small via env so the failure fires now.
                monkeypatch.setenv("LLM_CALL_TIMEOUT", "0.05")
                load_settings.cache_clear()
                try:
                    async for event in client.generate_stream(
                        system_prompt="x", user_prompt="y"
                    ):
                        yield event
                finally:
                    monkeypatch.delenv("LLM_CALL_TIMEOUT", raising=False)
                    load_settings.cache_clear()

            events = await asyncio.wait_for(_collect(timed_out_stream()), timeout=2.5)
            assert events
            assert events[-1]["type"] == "error"
            assert events[-1].get("classification") == "non_retryable"
            assert not any(event["type"] == "done" for event in events)
            await client.close()
        finally:
            load_settings.cache_clear()


async def _collect(agen) -> list:
    """Drain an async generator into a list (usable inside wait_for)."""
    return [item async for item in agen]


def _effective_timeout_seconds(timeout: httpx.Timeout | float) -> float:
    """Normalize httpx.Timeout | float into a single comparable number."""
    if isinstance(timeout, httpx.Timeout):
        # Tests always construct the client with an explicit scalar timeout,
        # so the per-op values are set (not None).
        assert timeout.read is not None
        return float(timeout.read)
    return float(timeout)


class TestTimeoutDiagnosabilityLogging:
    """Empty-str exceptions must stay identifiable in logs."""

    @pytest.mark.asyncio
    async def test_forced_read_timeout_logs_exception_type_generate(self, llm, caplog):
        """VAL-LLM-007 regression guard: transport branch logs type name and
        raises ProviderOutputError."""
        exc = httpx.ReadTimeout(
            "", request=httpx.Request("POST", "http://llm.test/v1/chat/completions")
        )
        with caplog.at_level(logging.ERROR, logger="agent.llm"):
            with patch.object(llm._client, "post", side_effect=exc):
                with pytest.raises(ProviderOutputError, match="transport failed"):
                    await llm.generate(system_prompt="x", user_prompt="y")

        assert any(
            record.levelno == logging.ERROR
            and "LLM transport failed:" in record.getMessage()
            and "ReadTimeout" in record.getMessage()
            for record in caplog.records
        )

    @pytest.mark.asyncio
    async def test_forced_read_timeout_logs_exception_type_generate_stream(
        self, llm, caplog
    ):
        """VAL-LLM-008: generic catch-all logs type(exc).__name__ alongside the
        message and yields an error event (no exception escapes)."""
        mock_client = MagicMock()
        mock_client.stream.side_effect = httpx.ReadTimeout(
            "", request=httpx.Request("POST", "http://llm.test/v1/chat/completions")
        )
        mock_client.__aenter__.return_value = mock_client
        mock_client_cls = MagicMock()
        mock_client_cls.return_value = mock_client

        with caplog.at_level(logging.ERROR, logger="agent.llm"):
            with patch("httpx.AsyncClient", mock_client_cls):
                events = [
                    event
                    async for event in llm.generate_stream(
                        system_prompt="x", user_prompt="y"
                    )
                ]

        assert len(events) == 1
        assert events[0]["type"] == "error"
        assert events[0]["classification"] == "non_retryable"
        assert any(
            record.levelno == logging.ERROR
            and "LLM stream call failed" in record.getMessage()
            and "ReadTimeout" in record.getMessage()
            and record.getMessage().rstrip() != "LLM stream call failed: "
            for record in caplog.records
        )


class TestCleanTimeoutFailure:
    """Deterministic failure with admission-slot hygiene (VAL-LLM-009)."""

    @pytest.mark.asyncio
    async def test_generate_timeout_fails_cleanly_and_releases_admission_slot(
        self, slow_1500ms_url, monkeypatch
    ):
        from agent.admission import get_admission
        from agent.llm import LLMClient

        monkeypatch.setenv("LLM_CALL_TIMEOUT", "0.05")
        load_settings.cache_clear()
        try:
            admission = get_admission()
            assert admission.active("llm") == 0
            client = LLMClient(slow_1500ms_url, api_key="", model="fixture-model")
            with pytest.raises(ProviderOutputError):
                await asyncio.wait_for(
                    client.generate(system_prompt="x", user_prompt="y"),
                    timeout=10,
                )
            # Bounded-wait completed above; the slot must be released and the
            # persistent client must still close without warnings/hangs.
            assert admission.active("llm") == 0
            await asyncio.wait_for(client.close(), timeout=5)
        finally:
            load_settings.cache_clear()


class TestStreamQuietPeriodSemantics:
    """Per-operation idle-bound semantics on the schema-less stream path."""

    @staticmethod
    def _client_for(url: str, call_timeout: str, monkeypatch):
        from agent.llm import LLMClient

        monkeypatch.setenv("LLM_CALL_TIMEOUT", call_timeout)
        load_settings.cache_clear()
        return LLMClient(f"{url}/v1", api_key="", model="fixture-model")

    @pytest.mark.asyncio
    async def test_mid_stream_stall_aborts_at_configured_quiet_period(
        self, raw_sse, monkeypatch
    ):
        """VAL-LLM-014: tokens then silence → error event within ~T, not ∞."""
        from agent.llm import LLMClient

        call_timeout = 1.0
        quiet_seconds = 3.0  # stall longer than T: abort happens at T, not at stall end
        raw_sse.configure(stall_after=2, quiet_seconds=quiet_seconds)
        monkeypatch.setenv("LLM_CALL_TIMEOUT", str(call_timeout))
        load_settings.cache_clear()
        try:
            client = LLMClient(raw_sse.base_url, api_key="", model="fixture-model")
            started = _time.monotonic()
            events = await asyncio.wait_for(
                _collect(client.generate_stream(system_prompt="x", user_prompt="y")),
                timeout=call_timeout + 5,
            )
            elapsed = _time.monotonic() - started
            assert events
            assert events[-1]["type"] == "error"
            assert events[-1].get("classification") == "non_retryable"
            assert not any(event["type"] == "done" for event in events)
            # Aborted by the configured quiet-period bound (~T) BEFORE the
            # stall would have ended — not at stall end, and not never.
            assert call_timeout <= elapsed < quiet_seconds
            await asyncio.wait_for(client.close(), timeout=5)
        finally:
            load_settings.cache_clear()

    @pytest.mark.asyncio
    async def test_continuously_progressing_stream_exceeds_call_timeout(
        self, raw_sse, monkeypatch
    ):
        """VAL-LLM-015: cumulative duration >> T with inter-chunk gaps < T
        completes with done — no aggregate deadline may exist."""
        from agent.llm import LLMClient

        call_timeout = 1.0
        drip_chunks = 8
        drip_interval = 0.15  # cumulative 1.2s > T, gaps 0.15s << T
        raw_sse.configure(drip_chunks=drip_chunks, drip_interval=drip_interval)
        monkeypatch.setenv("LLM_CALL_TIMEOUT", str(call_timeout))
        load_settings.cache_clear()
        try:
            client = LLMClient(raw_sse.base_url, api_key="", model="fixture-model")
            events = await asyncio.wait_for(
                _collect(client.generate_stream(system_prompt="x", user_prompt="y")),
                timeout=30,
            )
            cumulative = drip_chunks * drip_interval
            assert cumulative > call_timeout
            assert events[-1]["type"] == "done"
            assert events[-1]["full_content"].strip()
            assert not any(event["type"] == "error" for event in events)
            await asyncio.wait_for(client.close(), timeout=5)
        finally:
            load_settings.cache_clear()


class TestPipelineSurfacesTimedOutSynthesis:
    """VAL-LLM-016: research/answer pipelines surface synthesis timeouts."""

    @pytest.mark.asyncio
    async def test_answer_stream_surfaces_timed_out_synthesis_as_error_event(
        self, raw_sse, monkeypatch
    ):
        """(a) Streaming answer path: stalled synthesis → error event with
        classification, and never a successful-looking done."""
        from agent.research.loop import run_answer_stream
        from agent.research.sources import SourceArtifact
        from agent.searxng_client import SearchHealth

        call_timeout = 1.0
        # Stall longer than T so the configured quiet period (not the stall's
        # natural end) is what terminates the synthesis stream.
        raw_sse.configure(stall_after=1, quiet_seconds=3.0)
        monkeypatch.setenv("LLM_CALL_TIMEOUT", str(call_timeout))
        load_settings.cache_clear()
        try:

            async def scraped(target_urls, rerank_artifacts, scraper, num_sources):
                return [
                    SourceArtifact(
                        url="https://source.test/page",
                        title="S",
                        markdown="Authoritative context about the topic.",
                    )
                ]

            monkeypatch.setattr("agent.research.loop._scrape_answer_sources", scraped)

            async def searched(*args, **kwargs):
                return (
                    [{"url": "https://source.test/page", "title": "S"}],
                    SearchHealth(),
                )

            monkeypatch.setattr("agent.research.loop.SearXNGClient.search", searched)

            frames: list[dict] = await asyncio.wait_for(
                _collect(
                    run_answer_stream(
                        query="what?",
                        num_sources=1,
                        searxng_url="http://127.0.0.1:9",
                        scraper_url="http://127.0.0.1:9",
                        llm_base_url=raw_sse.base_url,
                        llm_api_key="",
                        llm_model="fixture-model",
                    )
                ),
                timeout=call_timeout + 10,
            )
            errors = [p for p in frames if p.get("type") == "error"]
            assert errors, f"expected an error event, got: {frames!r}"
            assert errors[-1].get("classification") == "non_retryable"
            assert errors[-1].get("content")
            dones = [p for p in frames if p.get("type") == "done"]
            assert not dones, "timed-out synthesis must not look successful"
        finally:
            load_settings.cache_clear()

    @pytest.mark.asyncio
    async def test_answer_pipeline_propagates_provider_output_error_on_synthesis_timeout(
        self, monkeypatch
    ):
        """(b) Schema path: ProviderOutputError from synthesis surfaces as a
        visible pipeline error event with the identifiable reason."""
        from agent.research.loop import run_answer_stream
        from agent.research.sources import SourceArtifact
        from agent.searxng_client import SearchHealth

        async def scraped(target_urls, rerank_artifacts, scraper, num_sources):
            return [
                SourceArtifact(
                    url="https://source.test/page",
                    title="S",
                    markdown="Context for the question.",
                )
            ]

        monkeypatch.setattr("agent.research.loop._scrape_answer_sources", scraped)

        async def searched(*args, **kwargs):
            return ([{"url": "https://source.test/page", "title": "S"}], SearchHealth())

        monkeypatch.setattr("agent.research.loop.SearXNGClient.search", searched)

        class TimedOutSynthesis:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc_info):
                return False

            async def generate(self, *args, **kwargs):
                raise ProviderOutputError(detail="synthesis timed out")

            def generate_stream(self, *args, **kwargs):
                raise AssertionError("schema path must use generate()")

            async def close(self):
                return None

        monkeypatch.setattr("agent.research.loop.LLMClient", TimedOutSynthesis)

        frames = [
            frame
            async for frame in run_answer_stream(
                query="what?",
                num_sources=1,
                searxng_url="http://127.0.0.1:9",
                scraper_url="http://127.0.0.1:9",
                llm_base_url="http://llm.invalid/v1",
                llm_api_key="",
                llm_model="fixture-model",
                output_schema={"type": "object"},
            )
        ]
        errors = [p for p in frames if p.get("type") == "error"]
        assert errors, f"expected surfaced synthesis failure, got: {frames!r}"
        assert errors[-1].get("classification") == "non_retryable"
        assert "timed out" in errors[-1]["content"]
        assert not any(p.get("type") == "done" for p in frames)


class TestScopeBoundaryNoOtherLLMClients:
    """VAL-LLM-017 guard: only agent/llm.py talks to chat/completions."""

    def test_no_other_agent_module_posts_to_chat_completions(self):
        agent_dir = Path(__file__).resolve().parents[2] / "agent-svc" / "agent"
        offenders = sorted(
            path.name
            for path in agent_dir.glob("*.py")
            if path.name != "llm.py"
            and "chat/completions" in path.read_text(encoding="utf-8")
        )
        assert offenders == [], (
            f"modules besides agent/llm.py reference chat/completions: {offenders}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Regression-hardening promotions (validator scratch tests, #589/#588 follow-up)
#
# These pin semantic gaps the milestone-1 user-testing validators covered with
# throwaway tests; they claim no new contract assertions (those milestones are
# sealed). Time semantics are unchanged: scaled-down REAL delays against real
# sockets — never virtual-clock mocking, never httpx.MockTransport for
# timeout behavior (MockTransport handler sleeps are invisible to httpx's
# timeout machinery).
# ─────────────────────────────────────────────────────────────────────────────


class TestDefaultTimeoutThreePointBoundary:
    """VAL-LLM-001/005 three-point boundary at the unset-default anchor.

    Scaled 100x against production numbers: unset default 120s ↔ 1.2s
    (default equivalent), raised config 300s ↔ 3.0s (configured T),
    provider delay 150s ↔ 1.5s (D). So 1.2 < 1.5 < 3.0: the same D that
    is abandoned under the default-equivalent bound succeeds once the knob
    is raised past D. The repo's TestSlowProviderTimeoutEnvelope covers the
    configured-T side (D=1.5s vs T=3s success and T=0.05s failure); the
    default-equivalent point below is the piece only the scratch validator
    exercised.
    """

    @pytest.mark.asyncio
    async def test_unset_default_is_120_at_full_scale(self, monkeypatch):
        """Anchor the scaling: env absent → the real default is 120.0."""
        monkeypatch.delenv("LLM_CALL_TIMEOUT", raising=False)
        load_settings.cache_clear()
        try:
            assert load_settings().llm_call_timeout == 120.0
        finally:
            load_settings.cache_clear()

    @pytest.mark.asyncio
    async def test_delay_exceeding_default_equivalent_times_out(
        self, slow_1500ms_url, monkeypatch
    ):
        """Point A: with the effective bound AT the scaled default
        equivalent (1.2s), the D=1.5s provider is abandoned before it can
        respond — proving D genuinely exceeds the default-equivalent
        scale (ProviderOutputError, bounded elapsed < D)."""
        from agent.llm import LLMClient

        monkeypatch.setenv("LLM_CALL_TIMEOUT", "1.2")
        load_settings.cache_clear()
        try:
            client = LLMClient(slow_1500ms_url, api_key="", model="fixture-model")
            started = _time.monotonic()
            with pytest.raises(ProviderOutputError):
                await asyncio.wait_for(
                    client.generate(system_prompt="x", user_prompt="y"), timeout=10
                )
            elapsed = _time.monotonic() - started
            # The ProviderOutputError itself proves the configured bound
            # aborted the call before the provider could succeed (point B
            # shows the same D succeeds under a raised T); the generous
            # elapsed ceiling keeps the wall-clock check flake-free on
            # loaded CI runners while still bounding the failure path.
            assert elapsed < 3.0
            await asyncio.wait_for(client.close(), timeout=5)
        finally:
            load_settings.cache_clear()

    @pytest.mark.asyncio
    async def test_same_delay_under_raised_timeout_succeeds(
        self, slow_1500ms_url, monkeypatch
    ):
        """Point B: identical D=1.5s provider completes once T is raised to
        3s (scaled image of LLM_CALL_TIMEOUT=300 vs the unset 120 default)."""
        from agent.llm import LLMClient

        monkeypatch.setenv("LLM_CALL_TIMEOUT", "3")
        load_settings.cache_clear()
        try:
            client = LLMClient(slow_1500ms_url, api_key="", model="fixture-model")
            started = _time.monotonic()
            result = await asyncio.wait_for(
                client.generate(system_prompt="Be helpful.", user_prompt="Say hi."),
                timeout=10,
            )
            elapsed = _time.monotonic() - started
            assert "Synthesized answer from provided context." in result
            # Waited out the full real delay D, and finished under T.
            assert 1.5 <= elapsed < 3.0
            await asyncio.wait_for(client.close(), timeout=5)
        finally:
            load_settings.cache_clear()


class TestStreamSuccessEnvelope:
    """Success-side stream envelope on the raw-SSE fixture path.

    The repo's stream tests drive generate_stream through mocked httpx
    clients; these pin the same contract against the real uvicorn raw-SSE
    fixture (real socket, real httpx per-op timeout machinery).
    """

    @pytest.mark.asyncio
    async def test_fast_provider_stream_ends_with_done_event(self, raw_sse):
        """VAL-LLM-006 success side: a fast provider yields token events
        ending in a done event with full content, and never an error."""
        from agent.llm import LLMClient

        # Three instant token chunks, no stall, straight through to [DONE].
        raw_sse.configure(stall_after=3, quiet_seconds=0.0)
        client = LLMClient(raw_sse.base_url, api_key="", model="fixture-model")
        try:
            events = await asyncio.wait_for(
                _collect(client.generate_stream(system_prompt="x", user_prompt="y")),
                timeout=10,
            )
        finally:
            await asyncio.wait_for(client.close(), timeout=5)
        assert events
        assert events[-1]["type"] == "done"
        assert events[-1].get("full_content", "").strip()
        assert not any(event["type"] == "error" for event in events)

    @pytest.mark.asyncio
    async def test_stream_forced_timeout_yields_error_releases_slot_no_leak(
        self, raw_sse, monkeypatch
    ):
        """VAL-LLM-009 stream side: a forced timeout inside generate_stream
        yields the error event, releases the admission slot (generator
        finally), and closes without a dangling-client hang. The repo's
        TestCleanTimeoutFailure covers only the generate() path."""
        from agent.admission import get_admission
        from agent.llm import LLMClient

        call_timeout = 0.5
        raw_sse.configure(stall_after=2, quiet_seconds=3.0)
        monkeypatch.setenv("LLM_CALL_TIMEOUT", str(call_timeout))
        load_settings.cache_clear()
        try:
            admission = get_admission()
            assert admission.active("llm") == 0
            client = LLMClient(raw_sse.base_url, api_key="", model="fixture-model")
            started = _time.monotonic()
            events = await asyncio.wait_for(
                _collect(client.generate_stream(system_prompt="x", user_prompt="y")),
                timeout=call_timeout + 5,
            )
            elapsed = _time.monotonic() - started
            assert events
            assert events[-1]["type"] == "error"
            assert events[-1]["classification"] == "non_retryable"
            assert not any(e["type"] == "done" for e in events)
            # Bounded by ~T, not ∞.
            assert call_timeout <= elapsed < 3.0
            # Admission slot released by the generator's finally block.
            assert admission.active("llm") == 0
            # Persistent client still closes cleanly — no dangling-client hang.
            await asyncio.wait_for(client.close(), timeout=5)
        finally:
            load_settings.cache_clear()

    @pytest.mark.asyncio
    async def test_mid_stream_stall_aborts_and_releases_admission_slot(
        self, raw_sse, monkeypatch
    ):
        """VAL-LLM-014 slot hygiene: abort after a mid-stream stall leaves
        the admission controller clean (the existing quiet-period test
        asserts timing + error event but not slot release)."""
        from agent.admission import get_admission
        from agent.llm import LLMClient

        call_timeout = 0.5
        raw_sse.configure(stall_after=1, quiet_seconds=3.0)
        monkeypatch.setenv("LLM_CALL_TIMEOUT", str(call_timeout))
        load_settings.cache_clear()
        try:
            admission = get_admission()
            assert admission.active("llm") == 0
            client = LLMClient(raw_sse.base_url, api_key="", model="fixture-model")
            events = await asyncio.wait_for(
                _collect(client.generate_stream(system_prompt="x", user_prompt="y")),
                timeout=call_timeout + 5,
            )
            assert events
            assert events[-1]["type"] == "error"
            assert events[-1].get("classification") == "non_retryable"
            assert not any(e["type"] == "done" for e in events)
            assert admission.active("llm") == 0  # released on abort
            await asyncio.wait_for(client.close(), timeout=5)
        finally:
            load_settings.cache_clear()


class TestWorkerRecordsSynthesisTimeoutReason:
    """VAL-LLM-016(b): a schema-path synthesis ProviderOutputError reaches
    store.fail_job with the provider reason via the real worker wrapper —
    job failure is never a silent truncation."""

    @pytest.mark.asyncio
    async def test_schema_synthesis_timeout_fails_job_with_reason(self, monkeypatch):
        """ProviderOutputError from run_research propagates through the real
        _run_job_with_observability wrapper into store.fail_job."""
        from agent.research.loop import run_research
        from agent.worker import _run_job_with_observability

        async def observed_work():
            # Mirrors worker._process_agent_async's work_fn: the awaited
            # run_research re-raises the pipeline's ProviderOutputError.
            return await run_research(
                prompt="question",
                schema={"type": "object"},
                llm_model="fixture-model",
            )

        searxng = MagicMock()
        searxng.close = AsyncMock()
        scraper = MagicMock()
        scraper.close = AsyncMock()
        llm = MagicMock()
        llm.close = AsyncMock()
        llm.generate = AsyncMock(
            side_effect=ProviderOutputError(detail="LLM synthesis timed out")
        )
        plan = {
            "focused_queries": ["question"],
            "research_strategy": "focused",
            "reasoning": "fixture",
        }
        discovered = {
            "context": "source context",
            "source_details": [
                {"url": "https://example.com", "source": "fixture", "char_count": 14}
            ],
            "search_results": [{"url": "https://example.com"}],
        }
        captured_fail: dict = {}

        class StoreSpy:
            def fail_job(self, job_id: str, error_message: str) -> None:
                captured_fail["reason"] = error_message

        with (
            patch("agent.research.loop.SearXNGClient", return_value=searxng),
            patch("agent.research.loop.ScraperClient", return_value=scraper),
            patch("agent.research.loop.LLMClient", return_value=llm),
            patch(
                "agent.research.loop._generate_research_plan",
                new=AsyncMock(return_value=plan),
            ),
            patch(
                "agent.research.loop._run_research_discover_and_scrape",
                new=AsyncMock(return_value=discovered),
            ),
            patch(
                # The wrapper calls its own module-level import, so the
                # interception point is agent.worker.deliver_webhook.
                "agent.worker.deliver_webhook",
                new=AsyncMock(return_value=None),
            ) as webhook_mock,
        ):
            await asyncio.wait_for(
                _run_job_with_observability(
                    "job-fixture",
                    "agent",
                    StoreSpy(),  # type: ignore[arg-type]
                    None,  # no webhook config → deliver_webhook no-ops anyway
                    observed_work,
                ),
                timeout=10,
            )

        assert captured_fail.get("reason"), "job must be marked failed"
        assert "timed out" in captured_fail["reason"]
        # No webhook configured: the wrapper still routes the failure event
        # through deliver_webhook, but with webhook_config=None, which makes
        # delivery a no-op (no HTTP POST is ever attempted).
        webhook_mock.assert_called_once()
        call = webhook_mock.call_args
        assert call.args[0] is None  # webhook_config
        assert call.args[1] == "failed"
        assert call.args[2] == "job-fixture"
        assert call.args[3] == {"error": "LLM synthesis timed out"}
