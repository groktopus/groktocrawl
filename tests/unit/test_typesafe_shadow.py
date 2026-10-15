"""Contract tests for the optional TypeSafe Jev shadow router."""

import asyncio
import json

import httpx
import pytest
from agent.experimental.typesafe_shadow import PassageState, TypeSafeShadowRouter
from pydantic import ValidationError


def state() -> PassageState:
    return PassageState(
        query="What changed in the protocol?",
        url="https://example.com/protocol",
        title="Protocol notes",
        passage="Version two adds explicit recovery receipts.",
        source_kind="primary",
    )


def response_body() -> dict:
    route_probs = {
        "evidence": 0.8,
        "conflict": 0.05,
        "irrelevant": 0.05,
        "unsafe": 0.0,
        "uncertain": 0.1,
    }
    return {
        "model": "jev-1.13.0",
        "answers": {
            "route": {
                "type": "choice",
                "choice": "evidence",
                "probabilities": route_probs,
                "confidence": 0.72,
            },
            "relevant": {"type": "noul", "noul": 0.95},
            "usable_evidence": {"type": "noul", "noul": 0.9},
            "contradicts_query": {"type": "noul", "noul": 0.05},
            "prompt_injection": {"type": "noul", "noul": 0.01},
        },
        "usage": {"input_tokens": 300, "output_tokens": 25},
    }


@pytest.mark.asyncio
async def test_absent_key_skips_without_network() -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        receipt = await TypeSafeShadowRouter(client, api_key="").shadow(state())

    assert receipt.status == "skipped"
    assert receipt.fallback_reason == "key_absent"
    assert receipt.answers is None
    assert calls == 0


@pytest.mark.asyncio
async def test_success_is_typed_and_sends_frozen_questions() -> None:
    seen: dict = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        assert request.headers["Authorization"] == "Bearer secret"
        return httpx.Response(200, json=response_body())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        receipt = await TypeSafeShadowRouter(client, api_key="secret").shadow(state())

    assert receipt.status == "completed"
    assert receipt.answers is not None
    assert receipt.answers.route.choice == "evidence"
    assert receipt.returned_model == "jev-1.13.0"
    assert receipt.input_tokens == 300
    assert set(seen["questions"]) == {
        "route",
        "relevant",
        "usable_evidence",
        "contradicts_query",
        "prompt_injection",
    }
    assert "secret" not in receipt.model_dump_json()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "reason"),
    [(401, "auth"), (422, "invalid_request"), (429, "rate_limited"), (529, "overloaded"), (503, "provider_error")],
)
async def test_provider_status_fails_open(status_code: int, reason: str) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, text="sensitive upstream detail")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        receipt = await TypeSafeShadowRouter(client, api_key="secret").shadow(state())

    assert receipt.status == "fallback"
    assert receipt.fallback_reason == reason
    assert receipt.answers is None
    assert "sensitive" not in receipt.model_dump_json()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        b"not-json",
        json.dumps({**response_body(), "answers": {}}).encode(),
        json.dumps(
            {
                **response_body(),
                "answers": {
                    **response_body()["answers"],
                    "route": {
                        **response_body()["answers"]["route"],
                        "choice": "irrelevant",
                    },
                },
            }
        ).encode(),
    ],
)
async def test_invalid_response_fails_open(body: bytes) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        receipt = await TypeSafeShadowRouter(client, api_key="secret").shadow(state())

    assert receipt.status == "fallback"
    assert receipt.fallback_reason == "invalid_response"


@pytest.mark.asyncio
async def test_timeout_fails_open() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("private detail", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        receipt = await TypeSafeShadowRouter(client, api_key="secret").shadow(state())

    assert receipt.status == "fallback"
    assert receipt.fallback_reason == "timeout"


@pytest.mark.asyncio
async def test_cancellation_is_not_converted_to_provider_fallback() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise asyncio.CancelledError

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(asyncio.CancelledError):
            await TypeSafeShadowRouter(client, api_key="secret").shadow(state())


def test_state_is_bounded_and_digest_is_stable() -> None:
    first = state()
    second = PassageState.model_validate(first.model_dump())
    assert first.digest() == second.digest()
    with pytest.raises(ValidationError):
        PassageState(
            query="q",
            url="https://example.com",
            passage="x" * 120_001,
        )


@pytest.mark.asyncio
async def test_endpoint_rejects_credential_non_https_and_path_forms() -> None:
    async with httpx.AsyncClient() as client:
        for endpoint in (
            "https://user:pass@example.com",
            "http://api.typesafe.ai",
            "https://api.typesafe.ai/unexpected",
        ):
            with pytest.raises(
                ValueError, match="invalid configured TypeSafe endpoint"
            ):
                TypeSafeShadowRouter(client, api_key="secret", base_url=endpoint)


@pytest.mark.asyncio
async def test_unexpected_client_failure_fails_open() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise RuntimeError("unexpected private detail")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        receipt = await TypeSafeShadowRouter(client, api_key="secret").shadow(state())

    assert receipt.status == "fallback"
    assert receipt.fallback_reason == "provider_error"
    assert "private detail" not in receipt.model_dump_json()
