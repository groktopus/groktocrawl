"""Continuation is visible and executable through the actual MCP tool boundary."""

import json
from unittest.mock import AsyncMock

import httpx
import pytest
from groktocrawl_client import GroktocrawlClient
from pagination import with_pagination


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["get_crawl_status", "get_batch_scrape_status"])
async def test_job_pages_follow_next_call(monkeypatch, tool):
    import mcp_server

    calls = []

    async def response(job_id, offset=0):
        calls.append((job_id, offset))
        return {
            "success": True,
            "status": "completed",
            "data": [],
            "next": f"https://untrusted.invalid/job?offset={offset + 1024}"
            if offset < 2048
            else None,
        }

    monkeypatch.setattr(mcp_server._client, tool, response)
    args = {"job_id": "job", "offset": 0}
    for state in ("more_available", "more_available", "exhausted"):
        content, _ = await mcp_server.mcp.call_tool(tool, args)
        raw = content[0].text
        assert raw.startswith('{\n  "pagination":')
        result = json.loads(raw)
        assert result["data"] == []  # Empty does not mean complete.
        assert result["pagination"]["state"] == state
        nxt = result["pagination"]["next_call"]
        if nxt:
            assert nxt["tool"] == tool
            args = nxt["arguments"]
    assert calls == [("job", 0), ("job", 1024), ("job", 2048)]


@pytest.mark.asyncio
async def test_search_preserves_every_original_filter(monkeypatch):
    import mcp_server

    args = {
        "query": "test",
        "limit": 1,
        "page": 2,
        "offset": 0,
        "sources": ["news"],
        "categories": ["science"],
        "search_type": "fast",
        "retrieval_mode": "keyword",
        "date_from": "2026-01-01",
        "date_to": "2026-02-01",
        "engines": ["openalex"],
    }
    response = {
        "success": True,
        "data": {"news": []},
        "continuation": {"page": 2, "offset": 1},
        "coverage": {"has_more_in_page": True, "upstream_has_more": None},
    }
    mock = AsyncMock(return_value=response)
    monkeypatch.setattr(mcp_server._client, "search", mock)
    content, _ = await mcp_server.mcp.call_tool("search", args)
    result = json.loads(content[0].text)
    nxt = result["pagination"]["next_call"]
    for key, value in args.items():
        assert nxt["arguments"][key] == (1 if key == "offset" else value)
    await mcp_server.mcp.call_tool(nxt["tool"], nxt["arguments"])
    assert mock.await_args.kwargs["offset"] == 1
    assert "unknown" in result["pagination"]["notice"]
    assert all(result[key] == value for key, value in response.items())


@pytest.mark.asyncio
async def test_query_selection_continues_sequentially_with_digest(monkeypatch):
    import mcp_server

    result = {
        "success": True,
        "content_digest": "a" * 64,
        "continuation": {"params": {"offset": 0, "expected_digest": "a" * 64}},
        "coverage_complete": False,
        "spans": [],
    }
    mock = AsyncMock(return_value=result)
    monkeypatch.setattr(mcp_server._client, "select_session_evidence", mock)
    content, _ = await mcp_server.mcp.call_tool(
        "select_session_evidence",
        {"session_id": "s", "ref_id": "r", "query": "topic", "budget_chars": 256},
    )
    page = json.loads(content[0].text)["pagination"]
    assert "not a sequential page" in page["notice"]
    assert page["next_call"]["arguments"] == {
        "session_id": "s",
        "ref_id": "r",
        "query": "",
        "offset": 0,
        "budget_chars": 256,
        "expected_digest": "a" * 64,
    }
    await mcp_server.mcp.call_tool(
        **{
            "name": page["next_call"]["tool"],
            "arguments": page["next_call"]["arguments"],
        }
    )
    assert mock.await_args.kwargs["query"] == ""


@pytest.mark.parametrize(
    "tool,args,data,state",
    [
        ("search", {"offset": 2}, {"continuation": {"offset": 2}}, "unknown"),
        (
            "search",
            {},
            {
                "continuation": None,
                "coverage": {"has_more_in_page": False, "next_upstream_page": 2},
            },
            "exhausted",
        ),
        ("search", {}, {}, "unknown"),
        ("get_crawl_status", {}, {"status": "processing", "next": None}, "pending"),
        ("get_crawl_status", {}, {"status": "failed", "next": None}, "unknown"),
        ("get_crawl_status", {}, {"next": "https://x/?offset=-1"}, "unknown"),
        ("get_crawl_status", {}, {"next": "https://x/?offset=1&offset=2"}, "unknown"),
        (
            "select_session_evidence",
            {},
            {"continuation": {"params": {"offset": 1, "expected_digest": "wrong"}}},
            "unknown",
        ),
        (
            "select_session_evidence",
            {},
            {"continuation": None, "coverage_complete": False},
            "exhausted",
        ),
    ],
)
def test_states_and_invalid_continuations(tool, args, data, state):
    result = with_pagination(data, tool, args)
    assert result["pagination"]["state"] == state
    assert result["pagination"]["next_call"] is None
    assert all(result[key] == value for key, value in data.items())


def test_errors_unchanged():
    data = {"success": False, "error": "nope"}
    assert with_pagination(data, "search", {}) == data


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool,path",
    [
        ("get_crawl_status", "/v2/crawl/job"),
        ("get_batch_scrape_status", "/v2/batch/scrape/job"),
    ],
)
async def test_http_client_forwards_offset(tool, path):
    def handle(request):
        assert request.url.path == path
        assert request.url.params["offset"] == "1024"
        return httpx.Response(200, json={"success": True})

    client = GroktocrawlClient(base_url="http://test", api_key=None)
    client._client = httpx.AsyncClient(
        base_url="http://test", transport=httpx.MockTransport(handle)
    )
    assert await getattr(client, tool)("job", offset=1024) == {"success": True}
    await client._client.aclose()


@pytest.mark.asyncio
async def test_evidence_pages_preserve_digest_and_reach_end(monkeypatch):
    import mcp_server

    seen = []

    async def response(session_id, ref_id, **args):
        seen.append(args)
        offset = args["offset"]
        return {
            "success": True,
            "content_digest": "b" * 64,
            "coverage_complete": False,
            "spans": [{"quote": str(offset)}],
            "continuation": {
                "params": {"offset": offset + 256, "expected_digest": "b" * 64}
            }
            if offset < 512
            else None,
        }

    monkeypatch.setattr(mcp_server._client, "select_session_evidence", response)
    args = {"session_id": "s", "ref_id": "r", "budget_chars": 256}
    for state in ("more_available", "more_available", "exhausted"):
        content, _ = await mcp_server.mcp.call_tool("select_session_evidence", args)
        result = json.loads(content[0].text)
        assert result["coverage_complete"] is False
        assert result["pagination"]["state"] == state
        if result["pagination"]["next_call"]:
            args = result["pagination"]["next_call"]["arguments"]
    assert [v["offset"] for v in seen] == [0, 256, 512]
    assert seen[1]["expected_digest"] == seen[2]["expected_digest"] == "b" * 64


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["get_crawl_status", "get_batch_scrape_status"])
async def test_negative_offset_rejected_before_api(monkeypatch, tool):
    import mcp_server
    from mcp.server.fastmcp.exceptions import ToolError

    mock = AsyncMock()
    monkeypatch.setattr(mcp_server._client, tool, mock)
    with pytest.raises(ToolError):
        await mcp_server.mcp.call_tool(tool, {"job_id": "j", "offset": -1})
    mock.assert_not_awaited()
