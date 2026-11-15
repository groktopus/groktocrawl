"""Context-window fallback must account for all evidence without fixed caps."""

import re
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from agent.exceptions import ContextLimitError
from agent.experimental.knowledge import text_digest
from agent.llm import LLMClient, _is_context_limit_response
from agent.models import CitationStyle
from agent.research.citation_recovery import (
    generate_with_context_fallback,
    stream_cited_answer,
)
from agent.research.evidence_recovery import retain_result_evidence


@pytest.mark.asyncio
async def test_provider_limit_hierarchically_processes_every_source_block():
    blocks = [
        f"Source: https://fixture.test/{index}\n\nMARKER{index:02d} " + "x" * 180
        for index in range(20)
    ]
    accepted_leaf_text: list[str] = []
    final_contexts: list[str] = []

    async def generate(*, system_prompt, context, **_kwargs):
        if len(context) > 600:
            raise ContextLimitError()
        if "preparing evidence notes" in system_prompt:
            accepted_leaf_text.append(context)
            markers = sorted(set(re.findall(r"MARKER\d{2}", context)))
            return "Evidence markers: " + ", ".join(markers)
        final_contexts.append(context)
        return "grounded answer"

    llm = SimpleNamespace(generate=generate, context_fallback_calls=0)
    answer = await generate_with_context_fallback(
        llm,
        system_prompt="answer",
        user_prompt="question",
        context="\n\n---\n\n".join(blocks),
        context_blocks=blocks,
        stage="answer",
    )

    assert answer == "grounded answer"
    assert llm.context_fallback_calls > 0
    for index in range(20):
        marker = f"MARKER{index:02d}"
        assert any(marker in leaf for leaf in accepted_leaf_text)
        assert marker in final_contexts[0]


@pytest.mark.asyncio
async def test_stream_reduces_evidence_before_publishing_tokens():
    calls = 0

    async def generate(**_kwargs):
        return "Evidence note: marker-from-full-source"

    async def generate_stream(**_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            yield {
                "type": "error",
                "classification": "non_retryable",
                "error_code": ContextLimitError.error_code,
                "content": ContextLimitError.detail,
            }
            return
        yield {"type": "token", "content": "Grounded answer [1]"}
        yield {"type": "done", "full_content": "Grounded answer [1]"}

    llm = SimpleNamespace(generate=generate, generate_stream=generate_stream)
    events = [
        event
        async for event in stream_cited_answer(
            llm,
            system_prompt="system",
            user_prompt="question",
            context="complete source block " + "x" * 500,
            context_blocks=[
                "Source: https://fixture.test/source\n\ncomplete body "
                + "x" * 500
            ],
            source_map=[{"url": "https://fixture.test/source"}],
            citation_style=CitationStyle.inline,
            stage="answer",
        )
    ]
    assert calls == 2
    assert events[-1]["type"] == "done"
    assert events[-1]["full_content"] == "Grounded answer [1]"
    assert events[0]["type"] == "token"


def test_context_limit_classifier_uses_structured_provider_errors():
    assert _is_context_limit_response(
        httpx.Response(
            400,
            json={"error": {"code": "context_length_exceeded", "message": "no"}},
        )
    )
    assert _is_context_limit_response(
        httpx.Response(
            400,
            json={"error": {"message": "maximum context length is 8192 tokens"}},
        )
    )
    assert not _is_context_limit_response(
        httpx.Response(400, json={"error": {"code": "invalid_api_key"}})
    )


@pytest.mark.asyncio
async def test_llm_client_surfaces_provider_context_window_error():
    admission = SimpleNamespace(
        weight_for=MagicMock(return_value=1),
        acquire=AsyncMock(),
        release=MagicMock(),
    )
    client = LLMClient("http://fixture", "", "fixture-model", admission=admission)
    client._client.post = AsyncMock(
        return_value=httpx.Response(
            400,
            json={
                "error": {
                    "code": "context_length_exceeded",
                    "message": "request too large",
                }
            },
        )
    )
    try:
        with pytest.raises(ContextLimitError):
            await client.generate("system", "question", context="large evidence")
    finally:
        await client.close()
    admission.release.assert_called_once_with("llm", weight=1)


@pytest.mark.asyncio
async def test_retained_evidence_exposes_digest_pinned_pages(monkeypatch):
    import agent.session as session_module

    text = "complete source body"
    stored = {}

    class FakeSessionManager:
        def __init__(self, *, redis_url):
            assert redis_url == "redis://fixture"

        async def create_session(self, *, ttl, owner_scope):
            assert ttl == 86_400 and owner_scope == "owner-scope"
            return "session-id"

        async def _store_refs(self, session_id, refs):
            assert session_id == "session-id"
            stored.update(refs)

        async def get_session(self, _session_id):
            return {"expires_at": "tomorrow"}

        async def delete_session(self, _session_id):
            return True

    monkeypatch.setattr(session_module, "SessionManager", FakeSessionManager)
    result = {
        "source_contents": {"https://fixture.test/source": text},
        "evidence_coverage": {
            "sources": [
                {
                    "id": "https://fixture.test/source",
                    "url": "https://fixture.test/source",
                }
            ]
        },
    }
    await retain_result_evidence(
        result,
        redis_url="redis://fixture",
        owner_scope="owner-scope",
    )

    assert result["evidence_recovery"]["status"] == "retained"
    assert result["evidence_session_id"] == "session-id"
    assert "source_contents" not in result
    recovery = result["evidence_coverage"]["sources"][0]["recovery"]
    assert recovery["session_id"] == "session-id"
    assert recovery["ref_id"] == "agent_1"
    assert recovery["params"]["expected_digest"] == text_digest(text)
    assert stored["agent_1"]["markdown"] == text


@pytest.mark.asyncio
async def test_failed_retention_keeps_full_inline_source_as_fallback(monkeypatch):
    import agent.session as session_module

    class BrokenSessionManager:
        def __init__(self, **_kwargs):
            pass

        async def create_session(self, **_kwargs):
            raise OSError("fixture failure")

    monkeypatch.setattr(session_module, "SessionManager", BrokenSessionManager)
    result = {"source_contents": {"https://fixture.test/source": "full body"}}
    await retain_result_evidence(
        result,
        redis_url="redis://fixture",
        owner_scope="owner-scope",
    )
    assert result["evidence_recovery"]["status"] == "unavailable"
    assert result["source_contents"]["https://fixture.test/source"] == "full body"
