#!/usr/bin/env python3
"""Minimal MCP client for the isolated W11 SlopSearX experiment."""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Iterator
from contextlib import AbstractContextManager
from typing import Any, Self

import httpx


class McpProtocolError(RuntimeError):
    """Raised when the MCP transport or envelope violates the expected contract."""


def _decode_response(response: httpx.Response) -> dict[str, Any]:
    """Decode either an MCP JSON response or a streamable-HTTP SSE response."""
    if not response.content:
        return {}
    content_type = response.headers.get("content-type", "").casefold()
    if "text/event-stream" not in content_type:
        value = response.json()
        if not isinstance(value, dict):
            raise McpProtocolError("MCP response must be a JSON object")
        return value

    data_lines: list[str] = []
    for line in response.text.splitlines():
        if line.startswith("data:"):
            data_lines.append(line.removeprefix("data:").lstrip())
        elif not line and data_lines:
            value = json.loads("\n".join(data_lines))
            if isinstance(value, dict):
                return value
            raise McpProtocolError("MCP SSE data must decode to a JSON object")
    if data_lines:
        value = json.loads("\n".join(data_lines))
        if isinstance(value, dict):
            return value
    raise McpProtocolError("MCP SSE response contains no JSON data event")


def _tool_payload(envelope: dict[str, Any]) -> Any:
    """Return structured tool content without treating policy denial as transport loss."""
    if "error" in envelope:
        error = envelope["error"]
        message = error.get("message", "unknown MCP error") if isinstance(error, dict) else str(error)
        raise McpProtocolError(message)
    result = envelope.get("result")
    if not isinstance(result, dict):
        raise McpProtocolError("MCP tool response lacks a result object")
    structured = result.get("structuredContent")
    if structured is not None:
        return structured
    content = result.get("content")
    if not isinstance(content, list) or not content:
        return result
    first = content[0]
    if not isinstance(first, dict) or first.get("type") != "text":
        return result
    text = first.get("text")
    if not isinstance(text, str):
        raise McpProtocolError("MCP text content is not a string")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


class W11McpClient(AbstractContextManager["W11McpClient"]):
    """Small synchronous client that preserves MCP session and request identity."""

    def __init__(
        self,
        endpoint: str,
        token: str,
        *,
        timeout_seconds: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not endpoint.startswith(("http://", "https://")):
            raise ValueError("MCP endpoint must be an absolute HTTP(S) URL")
        if not token:
            raise ValueError("MCP bearer token must not be empty")
        self._endpoint = endpoint
        self._client = httpx.Client(
            timeout=timeout_seconds,
            transport=transport,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
            },
        )
        self._session_id: str | None = None
        self._next_id = 0

    def __enter__(self) -> Self:
        self.initialize()
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def _post(self, body: dict[str, Any], *, expect_body: bool = True) -> dict[str, Any]:
        headers: dict[str, str] = {}
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
            headers["MCP-Protocol-Version"] = "2025-11-25"
        response = self._client.post(self._endpoint, json=body, headers=headers)
        response.raise_for_status()
        if not expect_body and not response.content:
            return {}
        value = _decode_response(response)
        session_id = response.headers.get("mcp-session-id")
        if session_id:
            value["_session_id"] = session_id
        return value

    def initialize(self) -> dict[str, Any]:
        if self._session_id is not None:
            raise McpProtocolError("MCP client is already initialized")
        envelope = self._post(
            {
                "jsonrpc": "2.0",
                "id": self._next_id,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "groktocrawl-w11", "version": "1"},
                },
            }
        )
        self._next_id += 1
        session_id = envelope.get("_session_id")
        if not session_id:
            raise McpProtocolError("initialize response did not expose a session id")
        self._session_id = str(session_id)
        self._post(
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            expect_body=False,
        )
        return envelope

    def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if self._session_id is None:
            raise McpProtocolError("MCP client must be initialized first")
        envelope = self._post(
            {
                "jsonrpc": "2.0",
                "id": self._next_id,
                "method": method,
                "params": params,
            }
        )
        self._next_id += 1
        return envelope

    def list_tools(self) -> list[dict[str, Any]]:
        result = self.request("tools/list", {}).get("result", {})
        tools = result.get("tools") if isinstance(result, dict) else None
        if not isinstance(tools, list) or not all(isinstance(item, dict) for item in tools):
            raise McpProtocolError("tools/list response lacks a tool list")
        return tools

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        return _tool_payload(
            self.request("tools/call", {"name": name, "arguments": arguments})
        )


def _iter_tool_names(tools: list[dict[str, Any]]) -> Iterator[str]:
    for tool in sorted(tools, key=lambda item: str(item.get("name", ""))):
        name = tool.get("name")
        if isinstance(name, str):
            yield name


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--token-env", default="SLOPSEARX_MCP_AUTH_TOKEN")
    parser.add_argument("action", choices=("tools", "status"))
    args = parser.parse_args()
    token = os.environ.get(args.token_env, "")
    with W11McpClient(args.endpoint, token) as client:
        if args.action == "tools":
            print(json.dumps(list(_iter_tool_names(client.list_tools())), indent=2))
        else:
            print(
                json.dumps(
                    client.call_tool("slopsearx_get_service_status", {}),
                    indent=2,
                    sort_keys=True,
                )
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
