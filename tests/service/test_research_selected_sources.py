"""Explicit selected evidence resolves real stored bytes, not supplied labels."""

import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from agent.followup import EvidenceChoice
from agent.research_sources import resolve_selected_sources
from fastapi import HTTPException
from starlette.requests import Request


def request():
    return Request(
        {
            "type": "http",
            "headers": [],
            "app": SimpleNamespace(state=SimpleNamespace(redis_url="redis://unused")),
        }
    )


def choice():
    return EvidenceChoice(
        kind="root",
        container_id="research-1",
        ref_id="snapshot-1",
        subject="Caller label",
    )


@pytest.mark.asyncio
async def test_retained_root_bytes_and_digest_are_preserved(monkeypatch):
    body = "A retained finding beyond the opening paragraphs."
    digest = hashlib.sha256(body.encode()).hexdigest()
    reader = AsyncMock(return_value={"body": body, "content_digest": digest})
    monkeypatch.setattr(
        "agent.routes.experimental_research.get_experimental_evidence", reader
    )
    result = await resolve_selected_sources([choice()], request())
    assert result[0]["markdown"] == body
    assert result[0]["content_digest"] == digest
    assert result[0]["selection"]["ref_id"] == "snapshot-1"
    assert result[0]["retrieved_at"] is None  # Do not invent freshness.
    reader.assert_awaited_once()


@pytest.mark.asyncio
async def test_integrity_failure_rejects_selected_source(monkeypatch):
    monkeypatch.setattr(
        "agent.routes.experimental_research.get_experimental_evidence",
        AsyncMock(return_value={"body": "altered", "content_digest": "0" * 64}),
    )
    with pytest.raises(HTTPException) as exc:
        await resolve_selected_sources([choice()], request())
    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_deleted_source_is_not_replaced_with_subject(monkeypatch):
    monkeypatch.setattr(
        "agent.routes.experimental_research.get_experimental_evidence",
        AsyncMock(side_effect=HTTPException(410, "Deleted")),
    )
    with pytest.raises(HTTPException) as exc:
        await resolve_selected_sources([choice()], request())
    assert exc.value.status_code == 410


@pytest.mark.asyncio
async def test_duplicate_selection_rejected_before_reads(monkeypatch):
    reader = AsyncMock()
    monkeypatch.setattr(
        "agent.routes.experimental_research.get_experimental_evidence", reader
    )
    with pytest.raises(HTTPException) as exc:
        await resolve_selected_sources([choice(), choice()], request())
    assert exc.value.status_code == 422
    reader.assert_not_called()


@pytest.mark.asyncio
async def test_session_selection_checks_owner_and_reads_retained_text(monkeypatch):
    store = SimpleNamespace(
        aget_ref=AsyncMock(
            return_value={
                "markdown": "Owned source",
                "url": "https://example.org/source",
            }
        )
    )
    authorization = AsyncMock(return_value={"owner_scope": "owned"})
    monkeypatch.setattr("agent.research_sources.SessionStore", lambda **kwargs: store)
    monkeypatch.setattr("agent.research_sources.authorize_session", authorization)
    selected = EvidenceChoice(
        kind="session",
        container_id="session-1",
        ref_id="ref-1",
        subject="Not the evidence",
    )
    result = await resolve_selected_sources([selected], request())
    authorization.assert_awaited_once()
    assert result[0]["markdown"] == "Owned source"
    assert result[0]["url"] == "https://example.org/source"


@pytest.mark.asyncio
async def test_byte_budget_counts_utf8_not_characters(monkeypatch):
    body = "é" * 128001
    monkeypatch.setattr(
        "agent.routes.experimental_research.get_experimental_evidence",
        AsyncMock(
            return_value={
                "body": body,
                "content_digest": hashlib.sha256(body.encode()).hexdigest(),
            }
        ),
    )
    with pytest.raises(HTTPException) as exc:
        await resolve_selected_sources([choice()], request())
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_attached_reference_resolves_current_root_bytes(monkeypatch):
    from agent.research_sources import resolve_attached_reference

    body = "Current retained source"
    digest = hashlib.sha256(body.encode()).hexdigest()
    reader = AsyncMock(return_value={"body": body, "content_digest": digest})
    monkeypatch.setattr(
        "agent.routes.experimental_research.get_experimental_evidence", reader
    )
    ref = {"research_source": choice().model_dump(), "content_digest": digest}
    result = await resolve_attached_reference(ref, request())
    assert result["markdown"] == body
    assert "markdown" not in ref
    reader.side_effect = HTTPException(410, "Deleted")
    with pytest.raises(HTTPException) as exc:
        await resolve_attached_reference(ref, request())
    assert exc.value.status_code == 410


@pytest.mark.asyncio
async def test_attached_reference_rejects_changed_digest(monkeypatch):
    from agent.research_sources import resolve_attached_reference

    body = "Changed retained source"
    monkeypatch.setattr(
        "agent.routes.experimental_research.get_experimental_evidence",
        AsyncMock(
            return_value={
                "body": body,
                "content_digest": hashlib.sha256(body.encode()).hexdigest(),
            }
        ),
    )
    with pytest.raises(HTTPException) as exc:
        await resolve_attached_reference(
            {"research_source": choice().model_dump(), "content_digest": "0" * 64},
            request(),
        )
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_session_manager_requires_scoped_resolver_for_attached_ref():
    from agent.session import SessionManager

    manager = SessionManager()
    ref = {"research_source": choice().model_dump(), "content_digest": "0" * 64}
    with pytest.raises(ValueError, match="scoped resolution"):
        await manager._resolve_attached_ref(ref)


@pytest.mark.asyncio
async def test_query_does_not_publish_if_linked_root_disappears_during_model(
    monkeypatch,
):
    from agent.session import SessionManager

    pointer = {"research_source": choice().model_dump(), "content_digest": "0" * 64}
    resolver = AsyncMock(
        side_effect=[
            {**pointer, "markdown": "Retained fact", "url": "https://example.org"},
            HTTPException(410, "Deleted"),
        ]
    )
    manager = SessionManager(reference_resolver=resolver)
    manager.store = SimpleNamespace(
        aget_artifact=AsyncMock(return_value=""),
        aget_refs=AsyncMock(return_value={"research_ref": pointer}),
        aappend_step=AsyncMock(),
        aappend_artifact=AsyncMock(),
    )
    model = SimpleNamespace(
        generate=AsyncMock(return_value="Fact [research_ref]"), close=AsyncMock()
    )
    monkeypatch.setattr("agent.session.LLMClient", lambda *args: model)
    with pytest.raises(HTTPException) as exc:
        await manager._step_query(
            "session",
            {"question": "Fact?", "ref_ids": ["research_ref"]},
            "http://unused",
            "test",
            "test",
        )
    assert exc.value.status_code == 410
    manager.store.aappend_step.assert_not_awaited()
    manager.store.aappend_artifact.assert_not_awaited()


@pytest.mark.asyncio
async def test_unowned_legacy_session_rejected_before_copying_evidence(monkeypatch):
    store = SimpleNamespace(aget_ref=AsyncMock())
    monkeypatch.setattr("agent.research_sources.SessionStore", lambda **kwargs: store)
    monkeypatch.setattr(
        "agent.research_sources.authorize_session", AsyncMock(return_value={})
    )
    selected = EvidenceChoice(
        kind="session", container_id="legacy", ref_id="ref", subject="Legacy"
    )
    with pytest.raises(HTTPException) as exc:
        await resolve_selected_sources([selected], request())
    assert exc.value.status_code == 422
    store.aget_ref.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_status", [410, 409])
async def test_deepen_does_not_publish_if_linked_root_changes_during_model(
    monkeypatch, failure_status
):
    from agent.session import SessionManager

    pointer = {"research_source": choice().model_dump(), "content_digest": "0" * 64}
    changed = False

    async def resolve(_ref):
        if changed:
            raise HTTPException(
                failure_status, "Retained source unavailable or changed"
            )
        return {
            **pointer,
            "markdown": "Original retained fact",
            "url": "https://example.org/original",
        }

    manager = SessionManager(reference_resolver=resolve)
    manager.store = SimpleNamespace(
        aget_ref=AsyncMock(return_value=pointer),
        aget_refs=AsyncMock(return_value={"research_ref": pointer}),
        aappend_step=AsyncMock(),
        aadd_refs=AsyncMock(),
        aappend_artifact=AsyncMock(),
    )
    search = SimpleNamespace(
        search=AsyncMock(return_value=([{"url": "https://example.org/new"}], {})),
        close=AsyncMock(),
    )
    scraper = SimpleNamespace(
        scrape_with_fallback=AsyncMock(
            return_value={
                "success": True,
                "data": {"markdown": "Additional retained fact"},
            }
        ),
        close=AsyncMock(),
    )

    async def generate(**_kwargs):
        nonlocal changed
        changed = True
        return "Derived finding from both sources"

    model = SimpleNamespace(generate=AsyncMock(side_effect=generate), close=AsyncMock())
    monkeypatch.setattr("agent.session.SearXNGClient", lambda *args: search)
    monkeypatch.setattr("agent.session.ScraperClient", lambda *args: scraper)
    monkeypatch.setattr("agent.session.LLMClient", lambda *args: model)
    with pytest.raises(HTTPException) as exc:
        await manager._step_deepen(
            "session",
            {"ref_id": "research_ref", "sub_topic": "Fact?", "max_sources": 1},
            "http://unused",
            "http://unused",
            "http://unused",
            "test",
            "test",
        )
    assert exc.value.status_code == failure_status
    model.generate.assert_awaited_once()
    manager.store.aappend_step.assert_not_awaited()
    manager.store.aadd_refs.assert_not_awaited()
    manager.store.aappend_artifact.assert_not_awaited()
