"""Execution admission tests; service integration separately exercises real adapters."""

from unittest.mock import AsyncMock

import pytest
from agent.followup import FollowupRequest, FollowupResponse
from agent.routes.followup import execute_followup
from fastapi import HTTPException
from starlette.requests import Request


@pytest.fixture
def seams(monkeypatch):
    preview = AsyncMock(
        return_value=FollowupResponse(
            original_wording="Explain Alpha",
            proposed_query="Explain Alpha",
            status="ready",
            selected=[],
            ambiguities=[],
            correction=None,
            actions=[],
        )
    )
    sources = AsyncMock(return_value=[])
    admit = AsyncMock(return_value={"success": True, "run_id": "new-run"})
    monkeypatch.setattr("agent.routes.followup.preview_followup", preview)
    monkeypatch.setattr("agent.research_sources.resolve_selected_sources", sources)
    monkeypatch.setattr(
        "agent.routes.experimental_research.admit_research_run", admit, raising=False
    )
    return preview, sources, admit


def request():
    return Request({"type": "http", "headers": [(b"idempotency-key", b"followup-1")]})


@pytest.mark.asyncio
async def test_execution_uses_durable_admission_with_same_request(seams):
    _, sources, admit = seams
    req = request()
    response = await execute_followup(req, FollowupRequest(wording="Explain Alpha"))
    assert response["run_id"] == "new-run"
    payload, passed_request = admit.await_args.args
    assert payload.objective == "Explain Alpha"
    assert passed_request is req
    assert admit.await_args.kwargs["selected_sources"] == ()
    sources.assert_awaited_once()


@pytest.mark.asyncio
async def test_ambiguous_preview_never_admits_or_reads_bodies(seams):
    preview, sources, admit = seams
    preview.return_value = FollowupResponse(
        original_wording="Compare them",
        proposed_query=None,
        status="needs_clarification",
        selected=[],
        ambiguities=["Name the subjects"],
        correction=None,
        actions=[],
    )
    with pytest.raises(HTTPException) as caught:
        await execute_followup(request(), FollowupRequest(wording="Compare them"))
    assert caught.value.status_code == 422
    sources.assert_not_awaited()
    admit.assert_not_awaited()


@pytest.mark.asyncio
async def test_deleted_selection_never_admits(seams):
    _, sources, admit = seams
    sources.side_effect = HTTPException(410, "Deleted")
    with pytest.raises(HTTPException) as caught:
        await execute_followup(request(), FollowupRequest(wording="Explain Alpha"))
    assert caught.value.status_code == 410
    admit.assert_not_awaited()
