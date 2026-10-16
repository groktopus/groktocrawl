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
