"""Follow-up MCP parity and read-only annotation."""

from unittest.mock import AsyncMock

import pytest
from mcp_server import mcp


@pytest.mark.asyncio
async def test_preview_preserves_full_request_and_response(monkeypatch):
    import mcp_server

    body = {
        "wording": "Compare it",
        "standalone_override": "Compare Alpha and Beta",
        "selected": [],
    }
    result = {
        "success": True,
        "executed": False,
        "original_wording": body["wording"],
        "proposed_query": body["standalone_override"],
    }
    client = AsyncMock(return_value=result)
    monkeypatch.setattr(mcp_server._client, "followup_preview", client)
    response = await mcp_server.followup_preview(body)
    import json

    assert json.loads(response) == result
    client.assert_awaited_once_with(body)
    tool = next(t for t in await mcp.list_tools() if t.name == "followup_preview")
    assert tool.annotations.readOnlyHint is True
    assert tool.annotations.destructiveHint is False


@pytest.mark.asyncio
async def test_execution_preserves_idempotency_and_is_not_read_only(monkeypatch):
    import json

    import mcp_server

    body = {"wording": "Investigate Alpha", "selected": []}
    result = {"success": True, "run_id": "independent-run"}
    client = AsyncMock(return_value=result)
    monkeypatch.setattr(mcp_server._client, "followup_execute", client)
    response = await mcp_server.followup_execute(body, "followup-key")
    assert json.loads(response) == result
    client.assert_awaited_once_with(body, "followup-key")
    tool = next(t for t in await mcp.list_tools() if t.name == "followup_execute")
    assert tool.annotations.readOnlyHint is False


@pytest.mark.asyncio
async def test_execution_options_are_explicit_and_forwarded(monkeypatch):
    import mcp_server

    client = AsyncMock(return_value={"success": True, "run_id": "new-run"})
    monkeypatch.setattr(mcp_server._client, "followup_execute", client)
    await mcp_server.followup_execute(
        {"wording": "Explain Alpha"},
        "options-1",
        webhook="https://receiver.example/hook",
        as_of="2026-09-01T00:00:00Z",
    )
    client.assert_awaited_once_with(
        {
            "wording": "Explain Alpha",
            "webhook": "https://receiver.example/hook",
            "as_of": "2026-09-01T00:00:00Z",
        },
        "options-1",
    )
    tool = next(t for t in await mcp.list_tools() if t.name == "followup_execute")
    assert {"webhook", "as_of"} <= set(tool.inputSchema["properties"])
    client.reset_mock()
    with pytest.raises(ValueError, match="Conflicting"):
        await mcp_server.followup_execute(
            {"wording": "Explain Alpha", "webhook": "https://other.example/hook"},
            "options-1",
            webhook="https://receiver.example/hook",
        )
    client.assert_not_awaited()
