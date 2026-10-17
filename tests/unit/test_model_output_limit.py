"""Provider output-limit receipts expose metadata, never response content."""

import json

import httpx
import pytest
from agent.experimental.model_review import ReviewRequest
from agent.experimental.model_transport import ReviewTransport


@pytest.mark.asyncio
async def test_output_limit_is_a_safe_specific_failure():
    calls = []

    def respond(request):
        calls.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "model": "private-provider-identity",
                "choices": [
                    {
                        "finish_reason": "length",
                        "message": {
                            "content": "PRIVATE RESPONSE AND CREDENTIAL MATERIAL"
                        },
                    }
                ],
                "usage": {"prompt_tokens": 123, "completion_tokens": 8192},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        transport = ReviewTransport(
            client, base_url="https://private.invalid/v1", api_key="PRIVATE KEY"
        )
        with pytest.raises(ValueError) as caught:
            await transport(
                ReviewRequest("review", b"PRIVATE PROMPT", "configured", 8192)
            )
    error = caught.value
    assert type(error).__name__ == "ModelOutputLimitError"
    assert error.diagnostics == {
        "http_status": 200,
        "finish_reason": "length",
        "input_tokens": 123,
        "output_tokens": 8192,
    }
    assert "PRIVATE" not in str(error) + json.dumps(error.diagnostics)
    assert len(calls) == 1
    assert calls[0]["max_tokens"] == 8192


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "usage",
    [
        None,
        "PRIVATE",
        {"prompt_tokens": True, "completion_tokens": -1},
        {"prompt_tokens": "PRIVATE", "completion_tokens": 10**100},
    ],
)
async def test_untrusted_usage_is_omitted(usage):
    from agent.experimental.model_failure import ModelOutputLimitError

    def respond(_request):
        return httpx.Response(
            200, json={"choices": [{"finish_reason": "length"}], "usage": usage}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(ModelOutputLimitError) as caught:
            await ReviewTransport(
                client, base_url="https://private.invalid", api_key="PRIVATE"
            )(ReviewRequest("test", b"{}", "configured", 32))
    assert caught.value.diagnostics == {"finish_reason": "length", "http_status": 200}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", ["timeout", "disconnect", "malformed", "http_error", "unknown_finish"]
)
async def test_ambiguous_or_unrecognized_failures_are_not_output_limits(failure):
    from agent.experimental.model_failure import ModelOutputLimitError

    def respond(request):
        if failure == "timeout":
            raise httpx.ReadTimeout("PRIVATE", request=request)
        if failure == "disconnect":
            raise httpx.RemoteProtocolError("PRIVATE")
        if failure == "malformed":
            return httpx.Response(200, content=b"PRIVATE invalid JSON")
        if failure == "http_error":
            return httpx.Response(502, json={"error": "PRIVATE"})
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"finish_reason": "PRIVATE", "message": {"content": "PRIVATE"}}
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(ValueError) as caught:
            await ReviewTransport(
                client, base_url="https://private.invalid", api_key="PRIVATE"
            )(ReviewRequest("test", b"{}", "configured", 32))
    assert not isinstance(caught.value, ModelOutputLimitError)
    assert str(caught.value) == "model transport failed"
