"""Bounded exact evidence HTTP/MCP parity without acquisition."""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from groktocrawl_client import GroktocrawlClient


def _make_matched_client(responses):
    import httpx

    def dispatch(request):
        return responses[(request.method, request.url.path)](request)

    client = GroktocrawlClient(base_url="http://test", api_key=None)
    client._client = httpx.AsyncClient(
        base_url="http://test", transport=httpx.MockTransport(dispatch)
    )
    return client


@pytest.mark.parametrize(
    "method,path", [("create_agent", "/v2/agent"), ("answer", "/v2/answer")]
)
def test_optional_evidence_budget_preserves_default_request(method, path):
    captured = []
    import httpx

    def handler(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={"success": True, "id": "job"})

    client = _make_matched_client({("POST", path): handler})
    asyncio.run(getattr(client, method)("question"))
    assert "evidence_budget_chars" not in captured[-1]
    asyncio.run(getattr(client, method)("question", evidence_budget_chars=4096))
    assert captured[-1]["evidence_budget_chars"] == 4096


def test_evidence_http_query_encoded_and_response_unchanged():
    import httpx

    result = {
        "success": True,
        "spans": [{"quote": "abc", "start": 0, "end": 3}],
        "content_digest": "a" * 64,
        "coverage_complete": False,
        "continuation": {"params": {"offset": 3}},
    }

    def handler(request):
        assert request.url.params["query"] == "a & b"
        assert request.url.params["budget_chars"] == "256"
        assert request.url.params["expected_digest"] == "a" * 64
        return httpx.Response(200, json=result)

    client = _make_matched_client({("GET", "/v2/session/s/evidence/r"): handler})
    assert (
        asyncio.run(
            client.select_session_evidence(
                "s", "r", query="a & b", budget_chars=256, expected_digest="a" * 64
            )
        )
        == result
    )


@pytest.mark.asyncio
async def test_evidence_tool_annotation_and_parameter_response_parity(monkeypatch):
    import mcp_server

    result = {
        "success": True,
        "spans": [{"quote": "abc"}],
        "coverage_complete": False,
        "continuation": None,
    }
    client = AsyncMock(return_value=result)
    monkeypatch.setattr(mcp_server._client, "select_session_evidence", client)
    response = await mcp_server.select_session_evidence(
        "s", "r", offset=256, budget_chars=256, expected_digest="a" * 64
    )
    assert json.loads(response) == result
    client.assert_awaited_once_with(
        "s", "r", query="", offset=256, budget_chars=256, expected_digest="a" * 64
    )
    tool = next(
        t
        for t in await mcp_server.mcp.list_tools()
        if t.name == "select_session_evidence"
    )
    assert tool.annotations.readOnlyHint is True
    assert tool.annotations.destructiveHint is False


@pytest.mark.asyncio
async def test_evidence_query_offset_conflict_never_calls_api(monkeypatch):
    import mcp_server
    from mcp.server.fastmcp.exceptions import ToolError

    client = AsyncMock()
    monkeypatch.setattr(mcp_server._client, "select_session_evidence", client)
    with pytest.raises(ToolError):
        await mcp_server.select_session_evidence("s", "r", query="topic", offset=1)
    client.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments",
    [
        {"budget_chars": 255},
        {"budget_chars": 128001},
        {"offset": -1},
        {"expected_digest": "bad"},
        {"query": "x" * 10001},
    ],
)
async def test_evidence_tool_schema_rejects_invalid_values(arguments, monkeypatch):
    import mcp_server
    from mcp.server.fastmcp.exceptions import ToolError

    client = AsyncMock()
    monkeypatch.setattr(mcp_server._client, "select_session_evidence", client)
    with pytest.raises(ToolError):
        await mcp_server.mcp.call_tool(
            "select_session_evidence", {"session_id": "s", "ref_id": "r", **arguments}
        )
    client.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool,argument,method",
    [("agent", "prompt", "create_agent"), ("answer", "query", "answer")],
)
async def test_budget_tool_passthrough_and_validation(
    tool, argument, method, monkeypatch
):
    import mcp_server
    from mcp.server.fastmcp.exceptions import ToolError

    client = AsyncMock(return_value={"success": True, "id": "job"})
    monkeypatch.setattr(mcp_server._client, method, client)
    await mcp_server.mcp.call_tool(
        tool, {argument: "question", "evidence_budget_chars": 4096}
    )
    assert client.call_args.kwargs["evidence_budget_chars"] == 4096
    client.reset_mock()
    await mcp_server.mcp.call_tool(tool, {argument: "question"})
    assert "evidence_budget_chars" not in client.call_args.kwargs
    client.reset_mock()
    with pytest.raises(ToolError):
        await mcp_server.mcp.call_tool(
            tool, {argument: "question", "evidence_budget_chars": 255}
        )
    client.assert_not_awaited()
