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
