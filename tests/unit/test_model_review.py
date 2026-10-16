"""Model callbacks bind exact inputs, preserve provenance, and fail closed."""

import json
from dataclasses import replace

import httpx
import pytest
from agent.experimental.consolidated_example import example_journey
from agent.experimental.knowledge_checks import KnowledgeCheckInput
from agent.experimental.model_review import (
    ModelReply,
    ModelReviewAdapter,
    ReviewRequest,
)
from agent.experimental.model_transport import ReviewTransport


def configured(complete, **kwargs):
    adapter = ModelReviewAdapter(
        provider="configured-litellm", model="local", complete=complete, **kwargs
    )
    journey = example_journey()
    payload = journey._checks[0].model_dump(mode="json")
    payload["reviewer"] = adapter.reviewer.model_dump(mode="json")
    checked = KnowledgeCheckInput.model_validate_json(json.dumps(payload))

    class Sources:
        async def resolve(self, reference):
            return await journey._acquire[reference.snapshot_id]()

    return adapter, checked, Sources()


def reply_for(request, **changes):
    payload = json.loads(request.payload)
    value = {
        "schema_version": "model-review-decision/1",
        "input_digest": payload["input_digest"],
        "outcome": "pass",
        "reason": "Exact supplied evidence inspected.",
    }
    value.update(changes)
    return ModelReply(json.dumps(value).encode(), "resolved-model", 20, 10)


@pytest.mark.asyncio
async def test_exact_sources_and_model_provenance():
    seen = []

    async def complete(request):
        seen.append(request)
        return reply_for(request)

    adapter, checked, sources = configured(complete)
    decision = await adapter.verify(checked, sources)
    assert decision.outcome == "pass"
    assert adapter.reviewer.kind == "model"
    assert adapter.reviewer.resolved_model is None
    assert adapter.usage == (("resolved-model", 20, 10),)
    payload = json.loads(seen[0].payload)
    assert len(payload["sources"]) == 2
    assert payload["sources"][0]["text"]
    assert payload["input_digest"] == checked.input_digest()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode",
    ["stale", "bad_json", "forged_human", "bad_usage", "oversize", "provider_error"],
)
async def test_invalid_response_consumes_call_without_acceptance(mode):
    calls = []

    async def complete(request):
        calls.append(request)
        if mode == "provider_error":
            raise RuntimeError("secret-provider-canary")
        reply = reply_for(request)
        if mode == "stale":
            return reply_for(request, input_digest="0" * 64)
        if mode == "forged_human":
            return reply_for(request, human_approved=True)
        if mode == "bad_usage":
            return replace(reply, input_tokens=True)
        return replace(
            reply, content=b"x" * 20_000 if mode == "oversize" else b"invalid"
        )

    adapter, checked, sources = configured(complete, max_calls=1)
    with pytest.raises(ValueError, match="no judgment accepted") as error:
        await adapter.verify(checked, sources)
    assert "canary" not in str(error.value)
    with pytest.raises(ValueError, match="exhausted"):
        await adapter.verify(checked, sources)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_changed_source_never_dispatches():
    calls = []

    async def complete(request):
        calls.append(request)
        return reply_for(request)

    adapter, checked, sources = configured(complete)

    class Altered:
        async def resolve(self, reference):
            value = await sources.resolve(reference)
            return replace(value, body=b"x" * len(value.body))

    with pytest.raises(ValueError):
        await adapter.verify(checked, Altered())
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("finish", ["stop", "length", "tool_calls"])
async def test_transport_uses_alias_and_rejects_nonfinal_results(finish):
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "model": "actual",
                "choices": [{"finish_reason": finish, "message": {"content": "{}"}}],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        transport = ReviewTransport(
            client, base_url="https://gateway.invalid/v1", api_key="test"
        )
        request = ReviewRequest("review", b"{}", "local", 32)
        if finish == "stop":
            result = await transport(request)
            assert result.input_tokens is None
            assert result.resolved_model == "actual"
        else:
            with pytest.raises(ValueError, match="transport failed"):
                await transport(request)
    assert len(calls) == 1
    assert calls[0]["model"] == "local"
    assert calls[0]["response_format"] == {"type": "json_object"}


@pytest.mark.asyncio
async def test_transport_requests_strict_schema_when_supplied():
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "model": "local",
                "choices": [
                    {"finish_reason": "stop", "message": {"content": "{}"}}
                ],
            },
        )

    schema = {
        "type": "object",
        "properties": {"outcome": {"enum": ["pass", "fail"]}},
    }
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await ReviewTransport(
            client, base_url="https://gateway.invalid/v1", api_key="test"
        )(ReviewRequest("review", b"{}", "local", 32, schema))
    assert calls[0]["response_format"] == {
        "type": "json_schema",
        "json_schema": {
            "name": "experimental_research_response",
            "strict": True,
            "schema": schema,
        },
    }


@pytest.mark.asyncio
async def test_close_during_completion_rejects_late_judgment():
    async def complete(request):
        adapter.close()
        return reply_for(request)

    adapter, checked, sources = configured(complete)
    with pytest.raises(ValueError, match="no judgment accepted"):
        await adapter.verify(checked, sources)
    assert adapter.usage == ()


@pytest.mark.asyncio
async def test_cancellation_cannot_accept_suppressed_child_result():
    import asyncio

    started = asyncio.Event()

    async def complete(request):
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            asyncio.current_task().uncancel()
        return reply_for(request)

    adapter, checked, sources = configured(complete)
    task = asyncio.create_task(adapter.verify(checked, sources))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert adapter.usage == ()


@pytest.mark.asyncio
async def test_configured_route_denial_does_not_fallback():
    from agent.experimental.model_transport import configured_model_review

    calls = []

    def handler(request):
        calls.append(json.loads(request.content)["model"])
        return httpx.Response(403, json={"error": {"type": "user_model_access_denied"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = configured_model_review(
            client, base_url="https://gateway.invalid/v1", api_key="test"
        )
        journey = example_journey()
        value = journey._checks[0].model_dump(mode="json")
        value["reviewer"] = adapter.reviewer.model_dump(mode="json")
        checked = KnowledgeCheckInput.model_validate_json(json.dumps(value))

        class Sources:
            async def resolve(self, reference):
                return await journey._acquire[reference.snapshot_id]()

        with pytest.raises(ValueError, match="no judgment accepted"):
            await adapter.verify(checked, Sources())
    assert calls == ["local"]
    assert adapter.usage == ()


@pytest.mark.asyncio
async def test_transport_unwraps_only_whole_json_fence_and_preserves_raw_digest():
    import hashlib

    raw = '```json\n{"outcome":"pass"}\n```'

    def handler(request):
        return httpx.Response(
            200,
            json={
                "model": "local",
                "choices": [{"finish_reason": "stop", "message": {"content": raw}}],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        transport = ReviewTransport(
            client, base_url="https://gateway.invalid/v1", api_key="test"
        )
        reply = await transport(ReviewRequest("review", b"{}", "local", 32))
    assert reply.content == b'{"outcome":"pass"}'
    assert reply.raw_content_digest == hashlib.sha256(raw.encode()).hexdigest()


@pytest.mark.asyncio
async def test_fenced_response_with_extra_prose_is_not_salvaged():
    raw = 'Here is my answer:\n```json\n{"outcome":"pass"}\n```'

    def handler(request):
        return httpx.Response(
            200,
            json={
                "model": "local",
                "choices": [{"finish_reason": "stop", "message": {"content": raw}}],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        reply = await ReviewTransport(
            client, base_url="https://gateway.invalid/v1", api_key="test"
        )(ReviewRequest("review", b"{}", "local", 32))
    assert reply.content == raw.encode()  # Strict admission will reject this.


@pytest.mark.asyncio
async def test_review_schema_disallows_assessment_label_for_structural_check():
    async def complete(request):
        payload = json.loads(request.payload)
        assert payload["response_schema"]["properties"]["outcome"]["enum"] == [
            "pass",
            "fail",
            "indeterminate",
        ]
        return reply_for(request, outcome="supported")

    adapter, checked, sources = configured(complete)
    with pytest.raises(ValueError, match="no judgment accepted"):
        await adapter.verify(checked, sources)


@pytest.mark.asyncio
async def test_review_payload_repeats_check_type_and_allowed_outcomes():
    async def complete(request):
        payload = json.loads(request.payload)
        assert payload["check_type"] == "structural"
        assert payload["allowed_outcomes"] == ["pass", "fail", "indeterminate"]
        return reply_for(request, outcome="pass")

    adapter, checked, sources = configured(complete)
    result = await adapter.verify(checked, sources)
    assert result.outcome == "pass"
