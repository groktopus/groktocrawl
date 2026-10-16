"""Single-attempt durable webhook transport preserves bytes and destination policy."""

import hashlib
import hmac
from types import SimpleNamespace

import httpx
import pytest
from agent import webhook

from common.url import (
    WebhookDestinationDNSRetryableError,
    WebhookDestinationValidationError,
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,outcome",
    [
        (204, "delivered"),
        (503, "retry"),
        (429, "retry"),
        (302, "rejected"),
        (400, "rejected"),
    ],
)
async def test_recorded_attempt_is_signed_exact_and_does_not_redirect(
    monkeypatch, status, outcome
):
    body = b'{"webhookId":"stable-event","data":{"state":"completed"}}'
    calls = []
    monkeypatch.setattr(webhook, "validate_outbound_webhook_url", lambda _url: None)
    monkeypatch.setattr(
        webhook, "_webhook_settings", SimpleNamespace(webhook_secret="test-signing-key")
    )

    def handle(request):
        calls.append(request)
        assert request.content == body
        expected = hmac.new(b"test-signing-key", body, hashlib.sha256).hexdigest()
        assert request.headers["X-Webhook-Signature"] == "sha256=" + expected
        return httpx.Response(
            status, headers={"Location": "http://127.0.0.1/forbidden"}
        )

    original = httpx.AsyncClient

    def client(**kwargs):
        assert kwargs["follow_redirects"] is False
        return original(transport=httpx.MockTransport(handle), **kwargs)

    monkeypatch.setattr(webhook.httpx, "AsyncClient", client)
    assert (
        await webhook.deliver_recorded_webhook("https://receiver.example/hook", body)
        == outcome
    )
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error,outcome",
    [
        (WebhookDestinationValidationError("restricted destination"), "rejected"),
        (WebhookDestinationDNSRetryableError("temporary DNS"), "retry"),
    ],
)
async def test_recorded_attempt_revalidates_destination_before_dispatch(
    monkeypatch, error, outcome
):
    def reject(_url):
        raise error

    def unexpected(**_kwargs):
        raise AssertionError("unvalidated webhook dispatched")

    monkeypatch.setattr(webhook, "validate_outbound_webhook_url", reject)
    monkeypatch.setattr(webhook.httpx, "AsyncClient", unexpected)
    assert (
        await webhook.deliver_recorded_webhook("http://127.0.0.1/private", b"{}")
        == outcome
    )


@pytest.mark.asyncio
async def test_receiver_response_body_is_never_downloaded(monkeypatch):
    class ReceiverBody(httpx.AsyncByteStream):
        iterated = False
        closed = False

        async def __aiter__(self):
            self.iterated = True
            yield b"A receiver-controlled body must not be downloaded"

        async def aclose(self):
            self.closed = True

    response_body = ReceiverBody()
    original = httpx.AsyncClient
    monkeypatch.setattr(webhook, "validate_outbound_webhook_url", lambda _url: None)
    monkeypatch.setattr(
        webhook.httpx,
        "AsyncClient",
        lambda **kwargs: original(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(204, stream=response_body)
            ),
            **kwargs,
        ),
    )
    assert (
        await webhook.deliver_recorded_webhook("https://receiver.example/hook", b"{}")
        == "delivered"
    )
    assert response_body.closed
    assert not response_body.iterated


@pytest.mark.asyncio
@pytest.mark.parametrize("body", ["not-bytes", b"x" * 1_048_577])
async def test_invalid_recorded_body_is_rejected_before_destination_lookup(
    monkeypatch, body
):
    def unexpected(_url):
        pytest.fail("invalid callback body must not cause destination lookup")

    monkeypatch.setattr(webhook, "validate_outbound_webhook_url", unexpected)
    assert (
        await webhook.deliver_recorded_webhook("https://receiver.example/hook", body)
        == "rejected"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        httpx.ConnectError("connection failed"),
        httpx.ReadTimeout("no acknowledgement"),
        TimeoutError(),
    ],
)
async def test_transport_failure_returns_retry_without_repeating_dispatch(
    monkeypatch, error
):
    body = b'{"webhookId":"same-event-on-retry"}'
    requests = []
    original = httpx.AsyncClient
    monkeypatch.setattr(webhook, "validate_outbound_webhook_url", lambda _url: None)

    def handle(request):
        requests.append(request)
        raise error

    monkeypatch.setattr(
        webhook.httpx,
        "AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(handle), **kwargs),
    )
    result = await webhook.deliver_recorded_webhook(
        "https://receiver.example/hook",
        body,
        {"X-Webhook-Signature": "saved-signature"},
    )
    assert result == "retry"
    assert len(requests) == 1
    assert requests[0].content == body
    assert requests[0].headers["X-Webhook-Signature"] == "saved-signature"
