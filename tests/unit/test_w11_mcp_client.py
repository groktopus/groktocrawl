import importlib.util
import json
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).parents[2]
SPEC = importlib.util.spec_from_file_location(
    "w11_mcp_client", ROOT / "scripts" / "w11_mcp_client.py"
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def sse(value: dict) -> bytes:
    return f"event: message\ndata: {json.dumps(value)}\n\n".encode()


def test_client_initializes_session_and_uses_it_for_tool_calls() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        payload = json.loads(request.content)
        if payload["method"] == "initialize":
            return httpx.Response(
                200,
                headers={
                    "content-type": "text/event-stream",
                    "mcp-session-id": "session-1",
                },
                content=sse(
                    {
                        "jsonrpc": "2.0",
                        "id": 0,
                        "result": {"protocolVersion": "2025-11-25"},
                    }
                ),
            )
        if payload["method"] == "notifications/initialized":
            return httpx.Response(202)
        if payload["method"] == "tools/list":
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {"tools": [{"name": "slopsearx_search"}]},
                },
            )
        raise AssertionError(payload)

    transport = httpx.MockTransport(handler)
    with MODULE.W11McpClient(
        "http://service.test/mcp", "secret-token", transport=transport
    ) as client:
        assert client.list_tools() == [{"name": "slopsearx_search"}]

    assert len(requests) == 3
    assert requests[0].headers["authorization"] == "Bearer secret-token"
    assert "mcp-session-id" not in requests[0].headers
    assert requests[1].headers["mcp-session-id"] == "session-1"
    assert requests[2].headers["mcp-protocol-version"] == "2025-11-25"


@pytest.mark.parametrize("structured", [True, False])
def test_call_tool_decodes_structured_and_text_payloads(structured: bool) -> None:
    outcome = {"status": "ok", "grants": {"research": True}}

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if payload["method"] == "initialize":
            return httpx.Response(
                200,
                headers={"mcp-session-id": "session-2"},
                json={"jsonrpc": "2.0", "id": 0, "result": {}},
            )
        if payload["method"] == "notifications/initialized":
            return httpx.Response(202)
        result = (
            {"structuredContent": outcome}
            if structured
            else {"content": [{"type": "text", "text": json.dumps(outcome)}]}
        )
        return httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": 1, "result": result},
        )

    with MODULE.W11McpClient(
        "https://service.test/mcp",
        "token",
        transport=httpx.MockTransport(handler),
    ) as client:
        assert client.call_tool("slopsearx_get_service_status", {}) == outcome


def test_policy_denial_is_returned_as_a_scored_contract_outcome() -> None:
    denial = {
        "error": {
            "code": "tool_disabled",
            "message": "staged search is disabled",
            "grant": "MCP_GRANT_STAGED_SEARCH",
        }
    }

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if payload["method"] == "initialize":
            return httpx.Response(
                200,
                headers={"mcp-session-id": "session-3"},
                json={"jsonrpc": "2.0", "id": 0, "result": {}},
            )
        if payload["method"] == "notifications/initialized":
            return httpx.Response(202)
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "isError": True,
                    "content": [{"type": "text", "text": json.dumps(denial)}],
                },
            },
        )

    with MODULE.W11McpClient(
        "http://service.test/mcp",
        "token",
        transport=httpx.MockTransport(handler),
    ) as client:
        assert client.call_tool("slopsearx_search_staged", {}) == denial


def test_client_rejects_missing_session_and_credentials() -> None:
    with pytest.raises(ValueError, match="absolute HTTP"):
        MODULE.W11McpClient("service.test/mcp", "token")
    with pytest.raises(ValueError, match="must not be empty"):
        MODULE.W11McpClient("http://service.test/mcp", "")

    client = MODULE.W11McpClient(
        "http://service.test/mcp",
        "token",
        transport=httpx.MockTransport(lambda request: httpx.Response(500)),
    )
    with pytest.raises(MODULE.McpProtocolError, match="initialized first"):
        client.request("tools/list", {})
    client.close()


def test_initialize_fails_when_server_omits_session_id() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": 0, "result": {}},
        )
    )
    client = MODULE.W11McpClient(
        "http://service.test/mcp", "token", transport=transport
    )
    with pytest.raises(MODULE.McpProtocolError, match="session id"):
        client.initialize()
    client.close()
