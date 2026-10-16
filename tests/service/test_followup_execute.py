"""Execution admission tests; service integration separately exercises real adapters."""

from unittest.mock import AsyncMock

import pytest
from agent.followup import FollowupExecutionRequest, FollowupResponse
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
    response = await execute_followup(
        req, FollowupExecutionRequest(wording="Explain Alpha")
    )
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
        await execute_followup(
            request(), FollowupExecutionRequest(wording="Compare them")
        )
    assert caught.value.status_code == 422
    sources.assert_not_awaited()
    admit.assert_not_awaited()


@pytest.mark.asyncio
async def test_deleted_selection_never_admits(seams):
    _, sources, admit = seams
    sources.side_effect = HTTPException(410, "Deleted")
    with pytest.raises(HTTPException) as caught:
        await execute_followup(
            request(), FollowupExecutionRequest(wording="Explain Alpha")
        )
    assert caught.value.status_code == 410
    admit.assert_not_awaited()


@pytest.mark.asyncio
async def test_execution_options_reach_durable_admission_without_entering_preview(
    seams,
):
    preview, _, admit = seams
    body = FollowupExecutionRequest(
        wording="Explain Alpha",
        as_of="2026-09-01T00:00:00Z",
        webhook="https://receiver.example/research",
    )
    await execute_followup(request(), body)
    passed = admit.await_args.args[0]
    assert passed.webhook == body.webhook
    assert passed.as_of == body.as_of
    preview_body = preview.await_args.args[1]
    assert "webhook" not in preview_body.model_dump()
    assert "as_of" not in preview_body.model_dump()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("webhook", "https://receiver.example/research"),
        ("as_of", "2026-09-01T00:00:00Z"),
    ],
)
async def test_preview_rejects_execution_only_options_before_work(seams, field, value):
    import httpx
    from agent.routes.followup import router
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/v2/followup/preview", json={"wording": "Explain Alpha", field: value}
        )
    assert response.status_code == 422
    seams[0].assert_not_awaited()
    seams[2].assert_not_awaited()


@pytest.mark.asyncio
async def test_cli_subprocess_reaches_execute_http_with_options_and_idempotency(seams):
    import asyncio
    import json
    import os
    import sys
    from pathlib import Path

    from agent.routes.followup import router
    from fastapi import FastAPI

    from tests.integration.test_live_research_http_adapters import local_http_api

    seams[2].return_value = {"run_id": "new-run", "state": "accepted"}
    app = FastAPI()
    app.include_router(router)
    async with local_http_api(app) as url:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            str(Path(__file__).resolve().parents[2] / "groktocrawl"),
            "--server",
            url,
            "--json",
            "followup",
            '{"wording":"Explain Alpha"}',
            "--execute",
            "--idempotency-key",
            "http-options-1",
            "--webhook",
            "https://receiver.example/hook",
            "--as-of",
            "2026-09-01T00:00:00Z",
            env={
                **os.environ,
                "GROKTOCRAWL_API_KEY": "local-http-test-only",
                "API_KEY": "",
            },
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), 15)
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
    assert process.returncode == 0, stderr.decode()
    assert json.loads(stdout) == {
        "success": True,
        "run_id": "new-run",
        "state": "accepted",
    }
    payload, request = seams[2].await_args.args
    assert request.headers["Idempotency-Key"] == "http-options-1"
    assert payload.webhook == "https://receiver.example/hook"
    assert payload.as_of.isoformat() == "2026-09-01T00:00:00+00:00"
