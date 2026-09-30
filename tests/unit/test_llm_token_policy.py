"""Regression coverage for the explicit OpenAI-compatible token budget policy."""

import json
import os

import httpx
import pytest
from agent import llm as llm_module
from agent.llm import LLMClient
from agent.settings import AgentSettings


def _settings(monkeypatch, parameter="max_completion_tokens", health_budget=1):
    monkeypatch.setattr(
        llm_module,
        "load_settings",
        lambda: AgentSettings(
            LLM_TOKEN_PARAMETER=parameter,
            LLM_MAX_TOKENS=1234,
            LLM_MAX_COMPLETION_TOKENS=5678,
            LLM_HEALTH_TOKEN_BUDGET=health_budget,
        ),
    )


def _install_strict_provider(monkeypatch, expected_field):
    requests = []

    def handle(request):
        body = json.loads(request.content)
        requests.append(body)
        if expected_field not in body or (
            {"max_tokens", "max_completion_tokens"} - {expected_field}
        ).intersection(body):
            return httpx.Response(400, json={"error": "unsupported token field"})
        content = '{"answer":"ok"}' if body.get("response_format") else "ok"
        if body.get("stream"):
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=(
                    'data: {"choices":[{"delta":{"content":"ok"}}]}\n\ndata: [DONE]\n\n'
                ),
            )
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": content}}]},
        )

    original = httpx.AsyncClient

    def client_factory(*args, **kwargs):
        return original(*args, transport=httpx.MockTransport(handle), **kwargs)

    monkeypatch.setattr(llm_module.httpx, "AsyncClient", client_factory)
    return requests


@pytest.mark.parametrize(
    ("parameter", "budget"),
    [("max_tokens", 1234), ("max_completion_tokens", 5678)],
)
@pytest.mark.asyncio
async def test_selected_budget_is_shared_by_all_request_paths(
    monkeypatch, parameter, budget
):
    _settings(monkeypatch, parameter, health_budget=2)
    requests = _install_strict_provider(monkeypatch, parameter)
    client = LLMClient(base_url="https://provider.invalid/v1", model="override-model")
    try:
        assert await client.generate("sys", "question") == "ok"
        schema = {
            "type": "object",
            "properties": {"answer": {"type": "string"}},
            "required": ["answer"],
        }
        assert (
            await client.generate("sys", "question", schema=schema) == '{"answer":"ok"}'
        )
        stream = [event async for event in client.generate_stream("sys", "question")]
        assert stream[-1] == {"type": "done", "full_content": "ok"}
        structured_stream = [
            event
            async for event in client.generate_stream("sys", "question", schema=schema)
        ]
        assert structured_stream == [
            {"type": "done", "full_content": '{"answer":"ok"}'}
        ]
        assert await client.check_health()
    finally:
        await client.close()

    assert [item["model"] for item in requests] == ["override-model"] * 5
    assert [item[parameter] for item in requests] == [budget] * 4 + [2]
    assert all(
        ({"max_tokens", "max_completion_tokens"} - {parameter}).isdisjoint(item)
        for item in requests
    )


@pytest.mark.asyncio
async def test_wrong_selected_field_is_rejected_by_strict_provider(monkeypatch):
    _settings(monkeypatch, "max_tokens")
    requests = _install_strict_provider(monkeypatch, "max_completion_tokens")
    client = LLMClient(base_url="https://provider.invalid/v1", model="new-model")
    try:
        with pytest.raises(llm_module.ProviderOutputError, match="HTTP 400"):
            await client.generate("sys", "question")
    finally:
        await client.close()
    assert requests[0]["max_tokens"] == 1234


@pytest.mark.parametrize(
    ("env_name", "value"),
    [
        ("LLM_MAX_TOKENS", "0"),
        ("LLM_MAX_COMPLETION_TOKENS", "-2"),
        ("LLM_HEALTH_TOKEN_BUDGET", "0"),
        ("LLM_TOKEN_PARAMETER", "auto"),
    ],
)
def test_invalid_token_policy_settings_are_rejected(monkeypatch, env_name, value):
    with monkeypatch.context() as scoped:
        scoped.setenv(env_name, value)
        with pytest.raises(ValueError):
            AgentSettings.model_validate(dict(os.environ))


def test_token_policy_defaults_preserve_legacy_budget():
    settings = AgentSettings()
    assert settings.llm_token_parameter == "max_tokens"
    assert settings.llm_max_tokens == 8192
    assert settings.llm_max_completion_tokens == 32768
    assert settings.llm_health_token_budget == 1
