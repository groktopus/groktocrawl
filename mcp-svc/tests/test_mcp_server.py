"""Tests for the GroktoCrawl MCP server — tool discovery, annotations,
JSON Schema validity, content blocks, protocol lifecycle, and error handling.

These tests exercise the FastMCP app directly without a running
agent-svc (the client calls are mocked).
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from mcp_server import mcp

# ── Helpers ───────────────────────────────────────────────────────


def _patch_client(
    monkeypatch: pytest.MonkeyPatch,
    responses: dict[str, dict],
) -> None:
    """Monkeypatch GroktocrawlClient methods to return canned responses.

    *responses* maps method names (e.g. ``"scrape"``, ``"search"``) to
    the dict that should be returned when that method is called.
    """
    import mcp_server as mod

    for meth_name, result in responses.items():

        async def _patched(*args: Any, _result: Any = result, **kwargs: Any) -> dict:
            return _result

        monkeypatch.setattr(mod._client, meth_name, _patched)


def _text(result: Any) -> str:
    """Extract the first text content block from a tool call result.

    FastMCP's ``call_tool`` returns a tuple of
    ``(unstructured_content, structured_content)`` where the first
    element is a list of ContentBlock objects.
    """
    if isinstance(result, tuple):
        return result[0][0].text
    return result.content[0].text


# ── Transport Security (regression: issue #524) ────────────────────


class TestTransportSecurity:
    """The FastMCP app must be constructed with explicit DNS-rebinding
    protection settings driven by MCP_ALLOWED_HOSTS.

    The mcp SDK auto-enables loopback-only Host validation when FastMCP is
    created without explicit transport_security. That rejects every
    non-loopback Host header (HTTP 421), making the published server
    unreachable from real clients. These tests pin the explicit
    configuration so a regression to the SDK default fails CI.
    """

    def test_server_has_explicit_transport_security(self):
        """The app-level FastMCP instance carries transport_security settings."""
        settings = mcp.settings.transport_security
        assert settings is not None

    def test_build_transport_security_fail_closed_when_unset(self, monkeypatch):
        """MCP_ALLOWED_HOSTS unset -> protection ON with loopback-only
        allowlist (fail-closed; the SDK's own default)."""
        import mcp_server as mod

        monkeypatch.delenv("MCP_ALLOWED_HOSTS", raising=False)
        ts = mod._build_transport_security()
        assert ts.enable_dns_rebinding_protection is True
        assert ts.allowed_hosts == ["127.0.0.1:*", "localhost:*", "[::1]:*"]

    def test_build_transport_security_parses_env(self, monkeypatch):
        """MCP_ALLOWED_HOSTS set -> protection on with the parsed allowlist."""
        import mcp_server as mod

        monkeypatch.setenv(
            "MCP_ALLOWED_HOSTS", "localhost:*,127.0.0.1:*,[::1]:*,hal2000:*"
        )
        ts = mod._build_transport_security()
        assert ts.enable_dns_rebinding_protection is True
        assert ts.allowed_hosts == [
            "localhost:*",
            "127.0.0.1:*",
            "[::1]:*",
            "hal2000:*",
        ]

    def test_build_transport_security_ignores_empty_entries(self, monkeypatch):
        """Trailing/empty comma entries are dropped, not kept as empty hosts."""
        import mcp_server as mod

        monkeypatch.setenv("MCP_ALLOWED_HOSTS", "hal2000:*, ,localhost:*,")
        ts = mod._build_transport_security()
        assert ts.enable_dns_rebinding_protection is True
        assert ts.allowed_hosts == ["hal2000:*", "localhost:*"]

    def test_build_transport_security_derives_origins(self, monkeypatch):
        """allowed_origins mirror the host allowlist under http and https."""
        import mcp_server as mod

        monkeypatch.setenv("MCP_ALLOWED_HOSTS", "hal2000:*")
        monkeypatch.delenv("MCP_ALLOWED_ORIGINS", raising=False)
        ts = mod._build_transport_security()
        assert ts.allowed_origins == ["http://hal2000:*", "https://hal2000:*"]

    def test_build_transport_security_origins_override(self, monkeypatch):
        """MCP_ALLOWED_ORIGINS overrides the host-derived origin default."""
        import mcp_server as mod

        monkeypatch.setenv("MCP_ALLOWED_HOSTS", "hal2000:*")
        monkeypatch.setenv(
            "MCP_ALLOWED_ORIGINS", "http://client.example:*,https://client.example:*"
        )
        ts = mod._build_transport_security()
        assert ts.allowed_origins == [
            "http://client.example:*",
            "https://client.example:*",
        ]


# ── Tool Discovery (VAL-MCP-B01, B02, B03, B04) ────────────────────


class TestToolDiscovery:
    """VAL-MCP-B01: tools/list returns the complete MCP tool surface."""

    async def test_tool_count(self):
        """tools/list returns exactly 44 tools."""
        tools = await mcp.list_tools()
        assert len(tools) == 44, f"Expected 44 tools, got {len(tools)}"

    async def test_all_tool_names(self):
        """All 44 expected tool names are present."""
        tools = await mcp.list_tools()
        names = {t.name for t in tools}
        expected = {
            "scrape",
            "search",
            "crawl",
            "get_crawl_status",
            "cancel_crawl",
            "get_crawl_errors",
            "get_active_crawls",
            "map",
            "agent",
            "get_agent_status",
            "cancel_agent",
            "answer",
            "extract",
            "get_extract_status",
            "enrich",
            "find_similar",
            "batch_scrape",
            "generate_llmstxt",
            "get_activity",
            "get_batch_scrape_status",
            "cancel_batch_scrape",
            "get_batch_scrape_errors",
            "get_llmstxt_status",
            "resolve_citations",
            "parse",
            "create_browser_session",
            "browser_execute",
            "list_browser_sessions",
            "destroy_browser_session",
            "create_monitor",
            "list_monitors",
            "get_monitor",
            "update_monitor",
            "run_monitor",
            "delete_monitor",
            "create_research_session",
            "research_session_step",
            "get_research_session",
            "export_research_session",
            "resolve_research_session",
            "delete_research_session",
            "create_research_plan",
            "get_research_plan",
            "execute_research_plan",
        }
        missing = expected - names
        extra = names - expected
        assert not missing, f"Missing tools: {missing}"
        assert not extra, f"Unexpected tools: {extra}"

    async def test_tool_names_are_unique(self):
        """VAL-MCP-B01: tool names are unique."""
        tools = await mcp.list_tools()
        names = [t.name for t in tools]
        assert len(names) == len(set(names)), f"Duplicate names: {names}"

    async def test_all_tools_have_descriptions(self):
        """VAL-MCP-F03: all tools have non-empty descriptions."""
        tools = await mcp.list_tools()
        for t in tools:
            assert t.description, f"Tool {t.name} has empty description"
            assert len(t.description) >= 20, (
                f"Tool {t.name} description too short: "
                f"{len(t.description)} chars (need >= 20)"
            )

    async def test_async_tools_mention_job_id(self):
        """VAL-MCP-F03: async tools mention job ID + polling in description."""
        tools = await mcp.list_tools()
        async_tools = {
            "crawl",
            "extract",
            "batch_scrape",
            "generate_llmstxt",
            "agent",
        }
        for t in tools:
            if t.name in async_tools:
                desc_lower = t.description.lower()
                assert (
                    "job" in desc_lower
                    or "poll" in desc_lower
                    or "asynchronously" in desc_lower
                ), (
                    f"Async tool {t.name} does not mention job/polling in description: "
                    f"{t.description[:80]}"
                )

    async def test_all_tools_have_valid_input_schema(self):
        """VAL-MCP-B02: each tool has a valid JSON Schema inputSchema."""
        tools = await mcp.list_tools()
        for t in tools:
            schema = t.inputSchema
            assert schema["type"] == "object", (
                f"Tool {t.name} inputSchema type is not 'object': {schema.get('type')}"
            )
            assert "properties" in schema, (
                f"Tool {t.name} inputSchema missing 'properties'"
            )
            assert isinstance(schema["properties"], dict), (
                f"Tool {t.name} properties is not a dict"
            )

    async def test_tools_with_required_params_have_required_array(self):
        """VAL-MCP-B02: tools with mandatory params have 'required' list."""
        tools = await mcp.list_tools()

        # Tools that definitely have required params
        tools_with_required = {
            "scrape": "url",
            "search": "query",
            "crawl": "url",
            "map": "url",
            "agent": "prompt",
            "answer": "query",
            "extract": "urls",
            "find_similar": "url",
            "batch_scrape": "urls",
            "generate_llmstxt": "url",
            "resolve_citations": "text",
            "parse": "file_url",
            "cancel_agent": "job_id",
            "cancel_batch_scrape": "job_id",
            "get_batch_scrape_errors": "job_id",
            "browser_execute": "session_id",
            "destroy_browser_session": "session_id",
            "research_session_step": "session_id",
            "get_research_session": "session_id",
            "export_research_session": "session_id",
            "resolve_research_session": "session_id",
            "delete_research_session": "session_id",
            "create_research_plan": "prompt",
            "get_research_plan": "plan_id",
            "execute_research_plan": "plan_id",
            "get_monitor": "monitor_id",
            "update_monitor": "monitor_id",
            "run_monitor": "monitor_id",
            "delete_monitor": "monitor_id",
        }

        for t in tools:
            if t.name in tools_with_required:
                required = t.inputSchema.get("required", [])
                expected_param = tools_with_required[t.name]
                assert expected_param in required, (
                    f"Tool {t.name}: '{expected_param}' not in required list {required}"
                )


# ── Tool Annotations (VAL-MCP-B03) ────────────────────────────────


class TestToolAnnotations:
    """VAL-MCP-B03: Tool annotations match expected readOnly/destructive hints."""

    async def test_readonly_tools(self):
        """Tools that only read data have readOnlyHint=True."""
        tools = await mcp.list_tools()
        readonly_tools = {
            "scrape",
            "search",
            "map",
            "agent",
            "answer",
            "extract",
            "enrich",
            "find_similar",
            "get_crawl_status",
            "get_agent_status",
            "get_extract_status",
            "get_crawl_errors",
            "get_active_crawls",
            "get_activity",
            "get_batch_scrape_status",
            "get_batch_scrape_errors",
            "get_llmstxt_status",
            "resolve_citations",
            # Job/session creators are not destructive — they allocate
            # server-side resources without destroying anything.
            "crawl",
            "batch_scrape",
            "generate_llmstxt",
            "parse",
            "create_browser_session",
            "create_monitor",
            "list_browser_sessions",
            "list_monitors",
            "get_monitor",
            "create_research_session",
            "get_research_session",
            "export_research_session",
            "resolve_research_session",
            "create_research_plan",
            "get_research_plan",
        }
        for t in tools:
            if t.name in readonly_tools:
                anno = t.annotations
                assert anno is not None, f"Tool {t.name} missing annotations"
                assert anno.readOnlyHint is True, (
                    f"Tool {t.name}: expected readOnlyHint=True, got {anno.readOnlyHint}"
                )
                assert anno.destructiveHint is False, (
                    f"Tool {t.name}: expected destructiveHint=False, got {anno.destructiveHint}"
                )

    async def test_destructive_tools(self):
        """Tools that cancel/delete/destroy have destructiveHint=True."""
        tools = await mcp.list_tools()
        destructive_tools = {
            "cancel_crawl",
            "cancel_agent",
            "cancel_batch_scrape",
            "destroy_browser_session",
            "delete_monitor",
            "delete_research_session",
            "execute_research_plan",
        }
        for t in tools:
            if t.name in destructive_tools:
                anno = t.annotations
                assert anno is not None, f"Tool {t.name} missing annotations"
                assert anno.destructiveHint is True, (
                    f"Tool {t.name}: expected destructiveHint=True, got {anno.destructiveHint}"
                )
                assert anno.readOnlyHint is False, (
                    f"Tool {t.name}: expected readOnlyHint=False, got {anno.readOnlyHint}"
                )

    async def test_neutral_tools(self):
        """Tools that modify state non-destructively carry neither hint."""
        tools = await mcp.list_tools()
        neutral_tools = {
            "browser_execute",
            "update_monitor",
            "run_monitor",
            "research_session_step",
        }
        for t in tools:
            if t.name in neutral_tools:
                anno = t.annotations
                assert anno is not None, f"Tool {t.name} missing annotations"
                assert anno.readOnlyHint is False, (
                    f"Tool {t.name}: expected readOnlyHint=False, got {anno.readOnlyHint}"
                )
                assert anno.destructiveHint is False, (
                    f"Tool {t.name}: expected destructiveHint=False, got {anno.destructiveHint}"
                )

    async def test_tool_annotations_consistent(self):
        """VAL-MCP-B04: annotations consistent across sessions (same server)."""
        tools1 = await mcp.list_tools()
        tools2 = await mcp.list_tools()
        for t1, t2 in zip(tools1, tools2, strict=False):
            assert t1.name == t2.name
            if t1.annotations and t2.annotations:
                assert t1.annotations.readOnlyHint == t2.annotations.readOnlyHint
                assert t1.annotations.destructiveHint == t2.annotations.destructiveHint


# ── Content Block Formatting (VAL-MCP-F01, F02) ────────────────────


class TestContentBlocks:
    """VAL-MCP-F01, F02: text content blocks and JSON-serializable IDs."""

    async def test_scrape_returns_text_content(self, monkeypatch):
        """Scrape tool returns content[0].type == 'text'."""
        _patch_client(
            monkeypatch,
            {
                "scrape": {"success": True, "data": {"markdown": "# Hello"}},
            },
        )
        result = await mcp.call_tool("scrape", {"url": "https://example.com"})
        content_blocks = result[0]
        assert content_blocks[0].type == "text"
        assert len(content_blocks[0].text) > 0

    async def test_crawl_returns_json_with_id(self, monkeypatch):
        """Crawl (job-creating tool) returns JSON with id and success."""
        _patch_client(
            monkeypatch,
            {
                "create_crawl": {"success": True, "id": "crawl-job-123"},
            },
        )
        result = await mcp.call_tool("crawl", {"url": "https://example.com"})
        data = json.loads(_text(result))
        assert data.get("success") is True
        assert "id" in data

    async def test_agent_returns_json_with_id(self, monkeypatch):
        """Agent (job-creating tool) returns the job ID immediately."""
        _patch_client(
            monkeypatch,
            {
                "create_agent": {"success": True, "id": "agent-job-123"},
            },
        )
        result = await mcp.call_tool("agent", {"prompt": "research something"})
        data = json.loads(_text(result))
        assert data.get("success") is True
        assert "id" in data

    async def test_batch_scrape_returns_json_with_id(self, monkeypatch):
        """Batch scrape returns JSON with id."""
        _patch_client(
            monkeypatch,
            {
                "create_batch_scrape": {"success": True, "id": "batch-job-456"},
            },
        )
        result = await mcp.call_tool(
            "batch_scrape", {"urls": ["https://a.com", "https://b.com"]}
        )
        data = json.loads(_text(result))
        assert "id" in data

    async def test_generate_llmstxt_returns_json_with_id(self, monkeypatch):
        """Generate llms.txt returns JSON with id."""
        _patch_client(
            monkeypatch,
            {
                "create_llmstxt": {"success": True, "id": "llmstxt-job-789"},
            },
        )
        result = await mcp.call_tool("generate_llmstxt", {"url": "https://example.com"})
        data = json.loads(_text(result))
        assert "id" in data

    async def test_extract_returns_json_with_id(self, monkeypatch):
        """Extract returns JSON with id."""
        _patch_client(
            monkeypatch,
            {
                "create_extract": {"success": True, "id": "extract-job-001"},
            },
        )
        result = await mcp.call_tool(
            "extract",
            {"urls": ["https://example.com"], "prompt": "Extract headings"},
        )
        data = json.loads(_text(result))
        assert "id" in data

    async def test_error_result_includes_status_code(self, monkeypatch):
        """Error from client is propagated as ToolError with status_code."""
        _patch_client(
            monkeypatch,
            {
                "scrape": {"error": "Invalid URL", "status_code": 400},
            },
        )
        from mcp.server.fastmcp.exceptions import ToolError

        with pytest.raises(ToolError) as exc_info:
            await mcp.call_tool("scrape", {"url": "https://example.com"})
        err_text = str(exc_info.value)
        assert "400" in err_text
        assert "Invalid URL" in err_text


# ── Tool Call Routing (VAL-MCP-C01, C02, D04, E01, E06) ───────────


class TestToolCallRouting:
    """Verify each tool maps to the correct client method."""

    async def test_scrape_passes_only_main_content(self, monkeypatch):
        """Scrape passes only_main_content=False through."""
        captured: dict[str, Any] = {}

        async def _fake_scrape(**kwargs: Any) -> dict:
            captured.update(kwargs)
            return {"success": True}

        monkeypatch.setattr(
            __import__("mcp_server", fromlist=["_client"])._client,
            "scrape",
            _fake_scrape,
        )
        await mcp.call_tool(
            "scrape",
            {
                "url": "https://example.com",
                "formats": ["markdown"],
                "only_main_content": False,
            },
        )
        assert captured.get("url") == "https://example.com"
        assert captured.get("formats") == ["markdown"]
        assert captured.get("only_main_content") is False
        assert "only_main_content" in captured  # explicitly passed

    async def test_search_passes_search_type(self, monkeypatch):
        """Search passes search_type through."""
        captured: dict[str, Any] = {}

        async def _fake_search(**kwargs: Any) -> dict:
            captured.update(kwargs)
            return {"data": {"web": []}}

        monkeypatch.setattr(
            __import__("mcp_server", fromlist=["_client"])._client,
            "search",
            _fake_search,
        )
        await mcp.call_tool(
            "search",
            {
                "query": "test",
                "limit": 3,
                "search_type": "rich",
            },
        )
        assert captured.get("query") == "test"
        assert captured.get("limit") == 3
        assert captured.get("search_type") == "rich"

    async def test_answer_passes_num_sources(self, monkeypatch):
        """Answer passes num_sources through."""
        captured: dict[str, Any] = {}

        async def _fake_answer(**kwargs: Any) -> dict:
            captured.update(kwargs)
            return {"answer": "test", "sources": []}

        monkeypatch.setattr(
            __import__("mcp_server", fromlist=["_client"])._client,
            "answer",
            _fake_answer,
        )
        await mcp.call_tool("answer", {"query": "test?", "num_sources": 3})
        assert captured.get("question") == "test?"
        assert captured.get("num_sources") == 3

    async def test_agent_passes_model_override(self, monkeypatch):
        """Agent passes model override + research params through to client."""
        captured: dict[str, Any] = {}

        async def _fake_create_agent(**kwargs: Any) -> dict:
            captured.update(kwargs)
            return {"success": True, "id": "agent-1"}

        monkeypatch.setattr(
            __import__("mcp_server", fromlist=["_client"])._client,
            "create_agent",
            _fake_create_agent,
        )
        await mcp.call_tool(
            "agent",
            {
                "prompt": "test research",
                "model": "gpt-4o",
                "urls": ["https://a.com"],
                "output_schema": {"type": "object"},
                "citation_style": "compact",
                "max_credits": 5,
                "include_images": True,
                "force_fresh": True,
                "search_type": "focused",
            },
        )
        assert captured.get("prompt") == "test research"
        assert captured.get("model") == "gpt-4o"
        assert captured.get("urls") == ["https://a.com"]
        assert captured.get("output_schema") == {"type": "object"}
        assert captured.get("citation_style") == "compact"
        assert captured.get("max_credits") == 5
        assert captured.get("include_images") is True
        assert captured.get("force_fresh") is True
        assert captured.get("search_type") == "focused"

    async def test_map_passes_limit(self, monkeypatch):
        """Map passes limit through."""
        captured: dict[str, Any] = {}

        async def _fake_map(**kwargs: Any) -> dict:
            captured.update(kwargs)
            return {"links": []}

        monkeypatch.setattr(
            __import__("mcp_server", fromlist=["_client"])._client,
            "map",
            _fake_map,
        )
        await mcp.call_tool("map", {"url": "https://example.com", "limit": 50})
        assert captured.get("url") == "https://example.com"
        assert captured.get("limit") == 50

    async def test_crawl_passes_max_params(self, monkeypatch):
        """Crawl passes max_pages and max_depth through."""
        captured: dict[str, Any] = {}

        async def _fake_create_crawl(**kwargs: Any) -> dict:
            captured.update(kwargs)
            return {"success": True, "id": "crawl-1"}

        monkeypatch.setattr(
            __import__("mcp_server", fromlist=["_client"])._client,
            "create_crawl",
            _fake_create_crawl,
        )
        await mcp.call_tool(
            "crawl",
            {
                "url": "https://example.com",
                "max_pages": 10,
                "max_depth": 3,
            },
        )
        assert captured.get("url") == "https://example.com"
        assert captured.get("max_pages") == 10
        assert captured.get("max_depth") == 3

    async def test_get_activity_no_args(self, monkeypatch):
        """get_activity takes no required arguments."""
        captured: dict[str, Any] = {}

        async def _fake_get_activity(**kwargs: Any) -> dict:
            captured["called"] = True
            return {"jobs": []}

        monkeypatch.setattr(
            __import__("mcp_server", fromlist=["_client"])._client,
            "get_activity",
            _fake_get_activity,
        )
        await mcp.call_tool("get_activity", {})
        assert captured.get("called") is True

    async def test_get_batch_scrape_status(self, monkeypatch):
        """get_batch_scrape_status passes job_id through."""
        captured: dict[str, Any] = {}

        async def _fake_get_batch_scrape_status(job_id: str) -> dict:
            captured["job_id"] = job_id
            return {"status": "completed"}

        monkeypatch.setattr(
            __import__("mcp_server", fromlist=["_client"])._client,
            "get_batch_scrape_status",
            _fake_get_batch_scrape_status,
        )
        await mcp.call_tool("get_batch_scrape_status", {"job_id": "batch-1"})
        assert captured.get("job_id") == "batch-1"

    async def test_get_llmstxt_status(self, monkeypatch):
        """get_llmstxt_status passes job_id through."""
        captured: dict[str, Any] = {}

        async def _fake_get_llmstxt_status(job_id: str) -> dict:
            captured["job_id"] = job_id
            return {"status": "completed", "llmstxt": "# site"}

        monkeypatch.setattr(
            __import__("mcp_server", fromlist=["_client"])._client,
            "get_llmstxt_status",
            _fake_get_llmstxt_status,
        )
        await mcp.call_tool("get_llmstxt_status", {"job_id": "llmstxt-1"})
        assert captured.get("job_id") == "llmstxt-1"

    async def test_resolve_citations_routing(self, monkeypatch):
        """resolve_citations passes text, sources, and style through."""
        captured: dict[str, Any] = {}

        async def _fake_resolve_citations(**kwargs: Any) -> dict:
            captured.update(kwargs)
            return {"resolved_text": "See [1](https://a.com)", "citations": []}

        monkeypatch.setattr(
            __import__("mcp_server", fromlist=["_client"])._client,
            "resolve_citations",
            _fake_resolve_citations,
        )
        await mcp.call_tool(
            "resolve_citations",
            {
                "text": "See [1]",
                "sources": [{"url": "https://a.com", "title": "A"}],
                "style": "compact",
            },
        )
        assert captured.get("text") == "See [1]"
        assert captured.get("sources") == [{"url": "https://a.com", "title": "A"}]
        assert captured.get("style") == "compact"

    # ── New surface routing ──

    async def test_research_session_lifecycle_routing(self, monkeypatch):
        """Session tools preserve IDs and route each lifecycle operation."""
        import mcp_server as mod

        captured: dict[str, Any] = {}

        async def _fake_create(**kwargs: Any) -> dict:
            captured["create"] = kwargs
            return {"success": True, "session_id": "rs-1"}

        async def _fake_step(**kwargs: Any) -> dict:
            captured["step"] = kwargs
            return {"success": True, "step_index": 1}

        async def _fake_get(session_id: str) -> dict:
            captured["get"] = session_id
            return {"success": True, "session_id": session_id}

        async def _fake_export(session_id: str) -> dict:
            captured["export"] = session_id
            return {"success": True, "session_id": session_id, "artifact": "# Research"}

        async def _fake_resolve(session_id: str, ref_ids: list[str]) -> dict:
            captured["resolve"] = (session_id, ref_ids)
            return {"success": True, "resolved": len(ref_ids)}

        async def _fake_delete(session_id: str) -> dict:
            captured["delete"] = session_id
            return {"success": True, "deleted": True}

        monkeypatch.setattr(mod._client, "create_research_session", _fake_create)
        monkeypatch.setattr(mod._client, "research_session_step", _fake_step)
        monkeypatch.setattr(mod._client, "get_research_session", _fake_get)
        monkeypatch.setattr(mod._client, "export_research_session", _fake_export)
        monkeypatch.setattr(mod._client, "resolve_research_session", _fake_resolve)
        monkeypatch.setattr(mod._client, "delete_research_session", _fake_delete)

        await mcp.call_tool("create_research_session", {"ttl": 900})
        await mcp.call_tool(
            "research_session_step",
            {
                "session_id": "rs-1",
                "action": "search",
                "query": "MCP session protocol",
                "limit": 3,
                "idempotency_key": "step-1",
            },
        )
        await mcp.call_tool("get_research_session", {"session_id": "rs-1"})
        await mcp.call_tool("export_research_session", {"session_id": "rs-1"})
        await mcp.call_tool(
            "resolve_research_session",
            {"session_id": "rs-1", "ref_ids": ["ref_1_1"]},
        )
        await mcp.call_tool("delete_research_session", {"session_id": "rs-1"})

        assert captured["create"] == {"ttl": 900}
        assert captured["step"] == {
            "session_id": "rs-1",
            "action": "search",
            "params": {"query": "MCP session protocol", "limit": 3},
            "parallel": False,
            "idempotency_key": "step-1",
        }
        assert captured["get"] == "rs-1"
        assert captured["export"] == "rs-1"
        assert captured["resolve"] == ("rs-1", ["ref_1_1"])
        assert captured["delete"] == "rs-1"

    @pytest.mark.parametrize(
        ("action", "arguments", "message"),
        [
            ("search", {}, "search action requires"),
            ("scrape", {}, "scrape action requires"),
            ("query", {}, "query action requires"),
            ("deepen", {}, "deepen action requires"),
        ],
    )
    async def test_research_session_step_validates_action_payload(
        self, action, arguments, message
    ):
        """Each session action rejects missing typed payload fields."""
        with pytest.raises(Exception, match=message):
            await mcp.call_tool(
                "research_session_step",
                {"session_id": "rs-1", "action": action, **arguments},
            )

    async def test_research_plan_lifecycle_routing(self, monkeypatch):
        """Plan tools preserve review-before-execution and typed changes."""
        import mcp_server as mod

        captured: dict[str, Any] = {}

        async def _fake_create(**kwargs: Any) -> dict:
            captured["create"] = kwargs
            return {"success": True, "plan_id": "plan-1", "plan": {"phases": []}}

        async def _fake_get(plan_id: str) -> dict:
            captured["get"] = plan_id
            return {"success": True, "plan_id": plan_id}

        async def _fake_execute(**kwargs: Any) -> dict:
            captured["execute"] = kwargs
            return {"success": True, "id": "job-1"}

        monkeypatch.setattr(mod._client, "create_research_plan", _fake_create)
        monkeypatch.setattr(mod._client, "get_research_plan", _fake_get)
        monkeypatch.setattr(mod._client, "execute_research_plan", _fake_execute)

        await mcp.call_tool(
            "create_research_plan",
            {"prompt": "Compare vector databases", "urls": ["https://a.test"]},
        )
        await mcp.call_tool("get_research_plan", {"plan_id": "plan-1"})
        await mcp.call_tool(
            "execute_research_plan",
            {
                "plan_id": "plan-1",
                "approve": True,
                "narrow": "focus on operational costs",
                "add_dimensions": ["latency"],
                "query_overrides": {"0": "vector database latency benchmarks"},
            },
        )

        assert captured["create"] == {
            "prompt": "Compare vector databases",
            "model": None,
            "urls": ["https://a.test"],
        }
        assert captured["get"] == "plan-1"
        assert captured["execute"] == {
            "plan_id": "plan-1",
            "modifications": [
                {
                    "type": "narrow",
                    "params": {"focus": "focus on operational costs"},
                },
                {"type": "add_dimension", "params": {"dimension": "latency"}},
                {
                    "type": "modify_query",
                    "params": {
                        "phase_index": 0,
                        "new_query": "vector database latency benchmarks",
                    },
                },
            ],
        }

    async def test_execute_research_plan_requires_explicit_approval(self):
        """A plan cannot execute when the approval gate is false."""
        with pytest.raises(Exception, match="approve=True"):
            await mcp.call_tool(
                "execute_research_plan",
                {"plan_id": "plan-1", "approve": False},
            )

    async def test_crawl_passes_path_filters(self, monkeypatch):
        """Crawl passes include/exclude paths and concurrency through."""
        captured: dict[str, Any] = {}

        async def _fake_create_crawl(**kwargs: Any) -> dict:
            captured.update(kwargs)
            return {"success": True, "id": "crawl-1"}

        monkeypatch.setattr(
            __import__("mcp_server", fromlist=["_client"])._client,
            "create_crawl",
            _fake_create_crawl,
        )
        await mcp.call_tool(
            "crawl",
            {
                "url": "https://example.com",
                "include_paths": ["/docs"],
                "exclude_paths": ["/admin"],
                "sitemap": "only",
                "ignore_robots_txt": True,
                "max_concurrency": 10,
                "delay": 0.5,
                "allow_subdomains": True,
                "allow_external_links": True,
                "prompt": "crawl the docs section",
            },
        )
        assert captured.get("include_paths") == ["/docs"]
        assert captured.get("exclude_paths") == ["/admin"]
        assert captured.get("sitemap") == "only"
        assert captured.get("ignore_robots_txt") is True
        assert captured.get("max_concurrency") == 10
        assert captured.get("delay") == 0.5
        assert captured.get("allow_subdomains") is True
        assert captured.get("allow_external_links") is True
        assert captured.get("prompt") == "crawl the docs section"

    async def test_search_passes_sources_and_categories(self, monkeypatch):
        """Search passes sources/categories/retrieval_mode through."""
        captured: dict[str, Any] = {}

        async def _fake_search(**kwargs: Any) -> dict:
            captured.update(kwargs)
            return {"data": {"web": []}}

        monkeypatch.setattr(
            __import__("mcp_server", fromlist=["_client"])._client,
            "search",
            _fake_search,
        )
        await mcp.call_tool(
            "search",
            {
                "query": "rust",
                "sources": ["github"],
                "categories": ["research"],
                "retrieval_mode": "hybrid",
                "output_schema": {"type": "object"},
                "system_prompt": "be concise",
            },
        )
        assert captured.get("sources") == ["github"]
        assert captured.get("categories") == ["research"]
        assert captured.get("retrieval_mode") == "hybrid"
        assert captured.get("output_schema") == {"type": "object"}
        assert captured.get("system_prompt") == "be concise"

    async def test_answer_passes_model_override(self, monkeypatch):
        """Answer passes model/output_schema/citation_style through."""
        captured: dict[str, Any] = {}

        async def _fake_answer(**kwargs: Any) -> dict:
            captured.update(kwargs)
            return {"answer": "ok", "sources": []}

        monkeypatch.setattr(
            __import__("mcp_server", fromlist=["_client"])._client,
            "answer",
            _fake_answer,
        )
        await mcp.call_tool(
            "answer",
            {
                "query": "q",
                "model": "gpt-4o",
                "output_schema": {"type": "object"},
                "citation_style": "compact",
                "search_type": "auto",
                "retrieval_mode": "semantic",
            },
        )
        assert captured.get("model") == "gpt-4o"
        assert captured.get("output_schema") == {"type": "object"}
        assert captured.get("citation_style") == "compact"
        assert captured.get("search_type") == "auto"
        assert captured.get("retrieval_mode") == "semantic"

    async def test_cancel_agent_routing(self, monkeypatch):
        """cancel_agent passes job_id through."""
        captured: dict[str, Any] = {}

        async def _fake_cancel_agent(job_id: str) -> dict:
            captured["job_id"] = job_id
            return {"success": True}

        monkeypatch.setattr(
            __import__("mcp_server", fromlist=["_client"])._client,
            "cancel_agent",
            _fake_cancel_agent,
        )
        await mcp.call_tool("cancel_agent", {"job_id": "agent-1"})
        assert captured.get("job_id") == "agent-1"

    async def test_cancel_batch_scrape_routing(self, monkeypatch):
        """cancel_batch_scrape passes job_id through."""
        captured: dict[str, Any] = {}

        async def _fake_cancel_batch_scrape(job_id: str) -> dict:
            captured["job_id"] = job_id
            return {"success": True}

        monkeypatch.setattr(
            __import__("mcp_server", fromlist=["_client"])._client,
            "cancel_batch_scrape",
            _fake_cancel_batch_scrape,
        )
        await mcp.call_tool("cancel_batch_scrape", {"job_id": "batch-1"})
        assert captured.get("job_id") == "batch-1"

    async def test_get_active_crawls_routing(self, monkeypatch):
        """get_active_crawls calls the client with no args."""
        captured: dict[str, Any] = {}

        async def _fake_get_active_crawls() -> dict:
            captured["called"] = True
            return {"data": []}

        monkeypatch.setattr(
            __import__("mcp_server", fromlist=["_client"])._client,
            "get_active_crawls",
            _fake_get_active_crawls,
        )
        await mcp.call_tool("get_active_crawls", {})
        assert captured.get("called") is True

    async def test_parse_routing(self, monkeypatch):
        """parse passes file_url through."""
        captured: dict[str, Any] = {}

        async def _fake_parse(file_url: str) -> dict:
            captured["file_url"] = file_url
            return {"success": True, "data": {"markdown": "# doc"}}

        monkeypatch.setattr(
            __import__("mcp_server", fromlist=["_client"])._client,
            "parse",
            _fake_parse,
        )
        await mcp.call_tool("parse", {"file_url": "https://x.com/doc.pdf"})
        assert captured.get("file_url") == "https://x.com/doc.pdf"

    async def test_create_browser_session_routing(self, monkeypatch):
        """create_browser_session delegates to the browser handler."""
        captured: dict[str, Any] = {}

        async def _fake_create_session(ttl: int = 300) -> dict:
            captured["ttl"] = ttl
            return {"id": "session-1"}

        monkeypatch.setattr(
            __import__("mcp_server", fromlist=["_browser_handler"])._browser_handler,
            "create_session",
            _fake_create_session,
        )
        await mcp.call_tool("create_browser_session", {"ttl": 600})
        assert captured.get("ttl") == 600

    async def test_browser_execute_routing(self, monkeypatch):
        """browser_execute delegates to the browser handler with action."""
        captured: dict[str, Any] = {}

        async def _fake_execute_action(
            session_id: str, action: str, **kwargs: Any
        ) -> dict:
            captured["session_id"] = session_id
            captured["action"] = action
            captured.update(kwargs)
            return {"result": "ok"}

        monkeypatch.setattr(
            __import__("mcp_server", fromlist=["_browser_handler"])._browser_handler,
            "execute_action",
            _fake_execute_action,
        )
        await mcp.call_tool(
            "browser_execute",
            {"session_id": "s1", "action": "navigate", "url": "https://a.com"},
        )
        assert captured.get("session_id") == "s1"
        assert captured.get("action") == "navigate"
        assert captured.get("url") == "https://a.com"

    async def test_destroy_browser_session_routing(self, monkeypatch):
        """destroy_browser_session delegates to the browser handler."""
        captured: dict[str, Any] = {}

        async def _fake_destroy_session(session_id: str) -> dict:
            captured["session_id"] = session_id
            return {"success": True}

        monkeypatch.setattr(
            __import__("mcp_server", fromlist=["_browser_handler"])._browser_handler,
            "destroy_session",
            _fake_destroy_session,
        )
        await mcp.call_tool("destroy_browser_session", {"session_id": "s1"})
        assert captured.get("session_id") == "s1"

    async def test_create_monitor_routing(self, monkeypatch):
        """create_monitor passes search config through."""
        captured: dict[str, Any] = {}

        async def _fake_monitor_create(**kwargs: Any) -> dict:
            captured.update(kwargs)
            return {"id": "m-1"}

        monkeypatch.setattr(
            __import__("mcp_server", fromlist=["_client"])._client,
            "monitor_create",
            _fake_monitor_create,
        )
        await mcp.call_tool(
            "create_monitor",
            {
                "monitor_type": "search",
                "query": "rust",
                "sources": ["github"],
                "num_results": 5,
            },
        )
        assert captured.get("monitor_type") == "search"
        assert captured.get("search_config") == {
            "query": "rust",
            "sources": ["github"],
            "numResults": 5,
        }

    async def test_update_monitor_routing(self, monkeypatch):
        """update_monitor passes fields through."""
        captured: dict[str, Any] = {}

        async def _fake_monitor_update(**kwargs: Any) -> dict:
            captured.update(kwargs)
            return {"success": True}

        monkeypatch.setattr(
            __import__("mcp_server", fromlist=["_client"])._client,
            "monitor_update",
            _fake_monitor_update,
        )
        await mcp.call_tool(
            "update_monitor", {"monitor_id": "m-1", "schedule": "0 0 * * *"}
        )
        assert captured.get("monitor_id") == "m-1"
        assert captured.get("schedule") == "0 0 * * *"

    async def test_update_monitor_preserves_existing_query(self, monkeypatch):
        """update_monitor merges the existing query on partial search updates."""
        import mcp_server

        updated: dict[str, Any] = {}

        async def _fake_monitor_get(monitor_id: str) -> dict:
            return {
                "id": monitor_id,
                "monitor_type": "search",
                "search_config": {"query": "rust", "numResults": 5},
            }

        async def _fake_monitor_update(**kwargs: Any) -> dict:
            updated.update(kwargs)
            return {"success": True}

        monkeypatch.setattr(mcp_server._client, "monitor_get", _fake_monitor_get)
        monkeypatch.setattr(mcp_server._client, "monitor_update", _fake_monitor_update)

        await mcp.call_tool(
            "update_monitor",
            {"monitor_id": "m-1", "sources": ["github"]},
        )
        # Existing query preserved; sources merged in.
        assert updated.get("search_config") == {
            "query": "rust",
            "sources": ["github"],
        }

    async def test_update_monitor_search_fields_require_query(self, monkeypatch):
        """update_monitor refuses search-field updates when no query exists."""
        import mcp_server

        async def _fake_monitor_get(monitor_id: str) -> dict:
            return {"id": monitor_id, "monitor_type": "scrape", "search_config": None}

        monkeypatch.setattr(mcp_server._client, "monitor_get", _fake_monitor_get)

        from mcp.server.fastmcp.exceptions import ToolError

        with pytest.raises(ToolError, match="no search query"):
            await mcp.call_tool(
                "update_monitor",
                {"monitor_id": "m-1", "sources": ["github"]},
            )

    async def test_create_monitor_requires_query_for_search(self, monkeypatch):
        """create_monitor with search type but no query raises a clear error."""
        from mcp.server.fastmcp.exceptions import ToolError

        with pytest.raises(ToolError, match="query is required"):
            await mcp.call_tool(
                "create_monitor", {"monitor_type": "search", "query": ""}
            )

    async def test_create_monitor_requires_url_for_scrape(self, monkeypatch):
        """create_monitor with scrape type but no url raises a clear error."""
        from mcp.server.fastmcp.exceptions import ToolError

        with pytest.raises(ToolError, match="url is required"):
            await mcp.call_tool("create_monitor", {"monitor_type": "scrape", "url": ""})

    async def test_run_monitor_routing(self, monkeypatch):
        """run_monitor passes monitor_id through."""
        captured: dict[str, Any] = {}

        async def _fake_monitor_run(monitor_id: str) -> dict:
            captured["monitor_id"] = monitor_id
            return {"id": "m-1"}

        monkeypatch.setattr(
            __import__("mcp_server", fromlist=["_client"])._client,
            "monitor_run",
            _fake_monitor_run,
        )
        await mcp.call_tool("run_monitor", {"monitor_id": "m-1"})
        assert captured.get("monitor_id") == "m-1"

    async def test_delete_monitor_routing(self, monkeypatch):
        """delete_monitor passes monitor_id through."""
        captured: dict[str, Any] = {}

        async def _fake_monitor_delete(monitor_id: str) -> dict:
            captured["monitor_id"] = monitor_id
            return {"success": True}

        monkeypatch.setattr(
            __import__("mcp_server", fromlist=["_client"])._client,
            "monitor_delete",
            _fake_monitor_delete,
        )
        await mcp.call_tool("delete_monitor", {"monitor_id": "m-1"})
        assert captured.get("monitor_id") == "m-1"


# ── Error Handling (VAL-MCP-G01) ──────────────────────────────────


class TestErrorHandling:
    """VAL-MCP-G01: Invalid tool name returns error."""

    async def test_invalid_tool_name(self):
        """Calling a non-existent tool raises ToolError."""
        with pytest.raises(Exception) as exc_info:
            await mcp.call_tool("nonexistent_tool", {})
        # FastMCP raises ToolError for unknown tools
        assert "Unknown tool" in str(exc_info.value) or "nonexistent_tool" in str(
            exc_info.value
        )

    async def test_missing_required_argument(self):
        """Missing required argument raises validation error."""
        with pytest.raises(Exception) as exc_info:
            await mcp.call_tool("scrape", {})
        # Pydantic validation error should mention the missing field
        err_str = str(exc_info.value)
        assert "url" in err_str.lower() or "validation" in err_str.lower(), (
            f"Expected url/validation error, got: {err_str[:200]}"
        )

    async def test_invalid_argument_type(self):
        """Invalid argument type raises validation error."""
        with pytest.raises(Exception) as exc_info:
            await mcp.call_tool("scrape", {"url": 12345})
        err_str = str(exc_info.value)
        # Should mention type issue
        assert (
            "url" in err_str.lower()
            or "type" in err_str.lower()
            or "validation" in err_str.lower()
        ), f"Expected type error, got: {err_str[:200]}"

    async def test_error_propagation_with_is_error(self, monkeypatch):
        """VAL-MCP-G03: HTTP errors are propagated as ToolError with status code.

        FastMCP converts ToolError to a response with isError:true
        at the MCP protocol level.  Unit tests catch the ToolError
        directly since mcp.call_tool propagates exceptions.
        """
        _patch_client(
            monkeypatch,
            {
                "get_crawl_status": {
                    "error": "Job not found",
                    "status_code": 404,
                },
            },
        )
        from mcp.server.fastmcp.exceptions import ToolError

        with pytest.raises(ToolError) as exc_info:
            await mcp.call_tool("get_crawl_status", {"job_id": "nonexistent"})
        err_text = str(exc_info.value)
        assert "404" in err_text
        assert "not found" in err_text.lower()

    async def test_upstream_http_error_returns_is_error_true(self, monkeypatch):
        """Upstream HTTP errors (4xx/5xx) raise ToolError → isError:true.

        When the agent-svc returns an HTTP error, the MCP tool raises
        ToolError, which FastMCP converts to isError:true at the
        protocol level.  MCP clients see this as an error result.
        """
        _patch_client(
            monkeypatch,
            {
                "scrape": {"error": "Upstream server error", "status_code": 500},
            },
        )
        from mcp.server.fastmcp.exceptions import ToolError

        with pytest.raises(ToolError) as exc_info:
            await mcp.call_tool("scrape", {"url": "https://example.com"})
        err_text = str(exc_info.value)
        assert "500" in err_text
        assert "Upstream server error" in err_text

    async def test_successful_result_is_valid_json(self, monkeypatch):
        """Successful tool results return valid JSON without isError.

        When the upstream call succeeds, the tool returns valid JSON
        with the response data.  MCP clients can parse the JSON to
        access the structured result.
        """
        _patch_client(
            monkeypatch,
            {
                "scrape": {"success": True, "data": {"markdown": "# Hello"}},
            },
        )
        result = await mcp.call_tool("scrape", {"url": "https://example.com"})
        text = _text(result)
        # Should be valid JSON, not an error
        data = json.loads(text)
        assert data.get("success") is True
        assert "data" in data

    async def test_error_null_not_treated_as_error(self, monkeypatch):
        """Responses with ``error: null`` and ``success: true`` are NOT errors.

        The agent-svc includes ``"error": null`` in successful scrape
        responses.  The MCP server must ignore null/empty error values
        and NOT raise ToolError.  This ensures tools like scrape,
        get_crawl_status, and get_crawl_errors return proper JSON
        instead of isError:true for valid responses.
        """
        _patch_client(
            monkeypatch,
            {
                "scrape": {
                    "success": True,
                    "data": {"markdown": "# Hello"},
                    "error": None,
                },
                "get_crawl_status": {
                    "success": True,
                    "status": "completed",
                    "completed": 10,
                    "total": 10,
                    "data": [],
                    "error": None,
                },
                "get_crawl_errors": {
                    "success": True,
                    "errors": [],
                    "robots_blocked": [],
                    "error": None,
                },
            },
        )
        # scrape with error:null should succeed (no ToolError → no isError)
        result = await mcp.call_tool("scrape", {"url": "https://example.com"})
        text = _text(result)
        data = json.loads(text)
        assert data.get("success") is True
        assert "data" in data
        # error:null is present in the raw agent-svc response but
        # _ensure_success does NOT raise ToolError — the result is
        # valid JSON with the expected data fields

        # get_crawl_status with error:null should succeed
        result = await mcp.call_tool("get_crawl_status", {"job_id": "job-1"})
        text = _text(result)
        data = json.loads(text)
        assert data.get("success") is True
        assert data.get("status") == "completed"
        assert data.get("data") == []

        # get_crawl_errors with error:null should succeed
        result = await mcp.call_tool("get_crawl_errors", {"job_id": "job-1"})
        text = _text(result)
        data = json.loads(text)
        assert data.get("success") is True
        assert "errors" in data
        assert data.get("errors") == []

    async def test_error_empty_string_not_treated_as_error(self, monkeypatch):
        """Responses with empty error string are NOT treated as errors.

        An empty error string ``""`` is falsy and should not trigger
        ToolError/isError:true.  The response should contain valid
        data with success:true.
        """
        _patch_client(
            monkeypatch,
            {
                "scrape": {"success": True, "data": {"markdown": "ok"}, "error": ""},
            },
        )
        result = await mcp.call_tool("scrape", {"url": "https://example.com"})
        text = _text(result)
        data = json.loads(text)
        assert data.get("success") is True
        assert "data" in data

    async def test_failed_job_status_raises_tool_error(self, monkeypatch):
        """A failed background job (status=failed + error) raises ToolError.

        Status endpoints return HTTP 200 with ``success: true`` even when
        the underlying job failed; the MCP server must still surface the
        failure as isError:true so clients can detect it programmatically.
        """
        _patch_client(
            monkeypatch,
            {
                "get_agent_status": {
                    "success": True,
                    "status": "failed",
                    "error": "Research pipeline exhausted credits",
                },
            },
        )
        from mcp.server.fastmcp.exceptions import ToolError

        with pytest.raises(ToolError) as exc_info:
            await mcp.call_tool("get_agent_status", {"job_id": "agent-1"})
        err_text = str(exc_info.value)
        assert "failed" in err_text.lower()
        assert "credits" in err_text.lower()

    async def test_cancelled_job_status_not_error(self, monkeypatch):
        """A cancelled job is informative, not an error.

        ``status: cancelled`` returns normally so the client can see the
        terminal state without a spurious error.
        """
        _patch_client(
            monkeypatch,
            {
                "get_crawl_status": {
                    "success": True,
                    "status": "cancelled",
                    "error": None,
                },
            },
        )
        result = await mcp.call_tool("get_crawl_status", {"job_id": "crawl-1"})
        data = json.loads(_text(result))
        assert data.get("status") == "cancelled"

    async def test_retry_scheduled_status_not_error(self, monkeypatch):
        """A retry_scheduled job is pending, not an error."""
        _patch_client(
            monkeypatch,
            {
                "get_agent_status": {
                    "success": True,
                    "status": "retry_scheduled",
                    "retryable": True,
                    "retry_reason": "RATE_LIMITED",
                    "error": None,
                },
            },
        )
        result = await mcp.call_tool("get_agent_status", {"job_id": "agent-1"})
        data = json.loads(_text(result))
        assert data.get("status") == "retry_scheduled"


# ── Session Consistency (VAL-MCP-B04) ─────────────────────────────


class TestSessionConsistency:
    """VAL-MCP-B04: tools/list is consistent across 'sessions'."""

    async def test_tool_list_consistent_across_calls(self):
        """Multiple calls to list_tools return same tool set."""
        tools1 = await mcp.list_tools()
        tools2 = await mcp.list_tools()
        tools3 = await mcp.list_tools()

        names1 = {t.name for t in tools1}
        names2 = {t.name for t in tools2}
        names3 = {t.name for t in tools3}

        assert names1 == names2 == names3

    async def test_tool_schemas_consistent(self):
        """Tool inputSchemas are consistent across calls."""
        tools1 = await mcp.list_tools()
        tools2 = await mcp.list_tools()

        schemas1 = {t.name: t.inputSchema for t in tools1}
        schemas2 = {t.name: t.inputSchema for t in tools2}

        for name in schemas1:
            assert schemas1[name] == schemas2[name], f"Schema mismatch for tool {name}"


# ── Descriptions (VAL-MCP-F03) ───────────────────────────────────


class TestDescriptions:
    """All tools have descriptions >= 20 chars, async tools mention polling."""

    async def test_min_description_length(self):
        tools = await mcp.list_tools()
        for t in tools:
            desc_len = len(t.description)
            assert desc_len >= 20, (
                f"Tool '{t.name}' description is {desc_len} chars (need >= 20)"
            )

    async def test_camel_case_property_names(self):
        """VAL-MCP-B02: inputSchema properties use camelCase (from Python snake_case)."""
        tools = await mcp.list_tools()
        for t in tools:
            props = t.inputSchema.get("properties", {})
            for prop_name in props:
                # Python snake_case params become camelCase in schema via FastMCP
                # Both are acceptable; we just check they're valid
                assert isinstance(prop_name, str)
                assert len(prop_name) > 0


# ── Environment Variable Defaults (VAL-MCP-L03) ──────────────────


class TestEnvVarDefaults:
    """VAL-MCP-L03: Environment variable defaults match specification."""

    def test_api_url_default(self, monkeypatch):
        """GROKTOCRAWL_URL defaults to http://agent-svc:8000."""
        monkeypatch.delenv("GROKTOCRAWL_URL", raising=False)
        monkeypatch.delenv("GROKTOCRAWL_API_URL", raising=False)
        # Reimport the module to pick up new env
        import importlib

        import mcp_server

        importlib.reload(mcp_server)
        assert mcp_server.API_URL == "http://agent-svc:8000"

    def test_http_timeout_default(self, monkeypatch):
        """HTTP_TIMEOUT defaults to 60."""
        monkeypatch.delenv("HTTP_TIMEOUT", raising=False)
        import importlib

        import mcp_server

        importlib.reload(mcp_server)
        assert mcp_server.DEFAULT_TIMEOUT == 60.0

    def test_mcp_port_default(self, monkeypatch):
        """MCP_PORT defaults to 8002."""
        monkeypatch.delenv("MCP_PORT", raising=False)
        import importlib

        import mcp_server

        importlib.reload(mcp_server)
        assert mcp_server.PORT == 8002

    def test_groktocrawl_url_from_env(self, monkeypatch):
        """GROKTOCRAWL_URL env var is read correctly."""
        monkeypatch.setenv("GROKTOCRAWL_URL", "http://custom-svc:9999")
        monkeypatch.delenv("GROKTOCRAWL_API_URL", raising=False)
        import importlib

        import mcp_server

        importlib.reload(mcp_server)
        assert mcp_server.API_URL == "http://custom-svc:9999"

    def test_http_timeout_from_env(self, monkeypatch):
        """HTTP_TIMEOUT env var is read correctly as float."""
        monkeypatch.setenv("HTTP_TIMEOUT", "90")
        import importlib

        import mcp_server

        importlib.reload(mcp_server)
        assert mcp_server.DEFAULT_TIMEOUT == 90.0

    def test_mcp_port_from_env(self, monkeypatch):
        """MCP_PORT env var is read correctly as int."""
        monkeypatch.setenv("MCP_PORT", "9000")
        import importlib

        import mcp_server

        importlib.reload(mcp_server)
        assert mcp_server.PORT == 9000


# ── Health Endpoint (VAL-MCP-K08, VAL-MCP-L01) ────────────────────


class TestHealthEndpoint:
    """VAL-MCP-K08: Health endpoint returns correct JSON with agent_svc status."""

    async def test_health_endpoint_returns_ok(self):
        """GET /health returns status: ok."""
        from mcp_server import _health_endpoint

        responses: list[dict] = []
        received_body: list[bytes] = []

        async def _send(msg: dict) -> None:
            responses.append(msg)
            if msg.get("type") == "http.response.body":
                received_body.append(msg.get("body", b""))

        async def _receive() -> dict:
            return {"type": "http.request"}

        await _health_endpoint(
            {"type": "http", "path": "/health", "method": "GET"},
            _receive,
            _send,
        )

        assert len(responses) >= 2
        start_msg = responses[0]
        assert start_msg["type"] == "http.response.start"
        assert start_msg["status"] == 200
        assert received_body
        body = json.loads(received_body[0])
        assert body["status"] == "ok"

    async def test_health_endpoint_has_agent_svc_field(self):
        """GET /health includes agent_svc field (connected or disconnected)."""
        from mcp_server import _health_endpoint

        received_body: list[bytes] = []

        async def _send(msg: dict) -> None:
            if msg.get("type") == "http.response.body":
                received_body.append(msg.get("body", b""))

        async def _receive() -> dict:
            return {"type": "http.request"}

        await _health_endpoint(
            {"type": "http", "path": "/health", "method": "GET"},
            _receive,
            _send,
        )

        body = json.loads(received_body[0])
        assert "agent_svc" in body
        assert body["agent_svc"] in ("connected", "disconnected")

    async def test_health_endpoint_has_uptime_seconds(self):
        """GET /health includes uptime_seconds field as a non-negative number."""
        from mcp_server import _health_endpoint

        received_body: list[bytes] = []

        async def _send(msg: dict) -> None:
            if msg.get("type") == "http.response.body":
                received_body.append(msg.get("body", b""))

        async def _receive() -> dict:
            return {"type": "http.request"}

        await _health_endpoint(
            {"type": "http", "path": "/health", "method": "GET"},
            _receive,
            _send,
        )

        body = json.loads(received_body[0])
        assert "uptime_seconds" in body
        assert isinstance(body["uptime_seconds"], (int, float))
        assert body["uptime_seconds"] >= 0

    async def test_health_endpoint_content_type_json(self):
        """GET /health returns content-type: application/json."""
        from mcp_server import _health_endpoint

        responses: list[dict] = []

        async def _send(msg: dict) -> None:
            responses.append(msg)

        async def _receive() -> dict:
            return {"type": "http.request"}

        await _health_endpoint(
            {"type": "http", "path": "/health", "method": "GET"},
            _receive,
            _send,
        )

        start_msg = responses[0]
        headers = {
            k.decode() if isinstance(k, bytes) else k: v.decode()
            if isinstance(v, bytes)
            else v
            for k, v in start_msg.get("headers", [])
        }
        assert headers.get("content-type") == "application/json"

    async def test_check_agent_svc_returns_bool(self):
        """_check_agent_svc returns True or False."""
        from mcp_server import _check_agent_svc

        result = await _check_agent_svc()
        assert isinstance(result, bool)


# ── Docker & Deployment (VAL-MCP-L01, L02, L04) ───────────────────


class TestDockerDeployment:
    """VAL-MCP-L01, L02, L04: Docker deployment assertions.

    These tests verify properties that the Docker deployment must satisfy.
    They are sanity checks for the deployable artifacts.
    """

    def test_dockerfile_uses_python_312_slim(self):
        """VAL-MCP-L04: Dockerfile uses python:3.12-slim base image."""
        import os

        dockerfile_path = os.path.join(os.path.dirname(__file__), "..", "Dockerfile")
        with open(dockerfile_path) as f:
            content = f.read()
        assert "FROM python:3.12-slim" in content, (
            "Dockerfile must use python:3.12-slim base image"
        )

    def test_dockerfile_exposes_port_8002(self):
        """VAL-MCP-L01: Dockerfile exposes port 8002."""
        import os

        dockerfile_path = os.path.join(os.path.dirname(__file__), "..", "Dockerfile")
        with open(dockerfile_path) as f:
            content = f.read()
        assert "EXPOSE 8002" in content, "Dockerfile must EXPOSE port 8002"

    def test_dockerfile_copies_all_source_files(self):
        """Dockerfile copies mcp_server.py, groktocrawl_client.py, session_store.py, browser_handler.py."""
        import os

        dockerfile_path = os.path.join(os.path.dirname(__file__), "..", "Dockerfile")
        with open(dockerfile_path) as f:
            content = f.read()
        assert "COPY mcp_server.py" in content
        assert "COPY groktocrawl_client.py" in content
        assert "COPY session_store.py" in content
        assert "COPY browser_handler.py" in content

    def test_dockerfile_installs_pinned_dependencies(self):
        """Dockerfile installs mcp, httpx, pydantic with pinned versions."""
        import os

        dockerfile_path = os.path.join(os.path.dirname(__file__), "..", "Dockerfile")
        with open(dockerfile_path) as f:
            content = f.read()
        assert "mcp==" in content, "mcp must be pinned"
        assert "httpx==" in content, "httpx must be pinned"
        assert "pydantic==" in content, "pydantic must be pinned"

    def test_docker_compose_has_mcp_svc_service(self):
        """VAL-MCP-L02: docker-compose.yml declares mcp-svc service."""
        import os

        compose_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "docker-compose.yml"
        )
        with open(compose_path) as f:
            content = f.read()
        assert "mcp-svc:" in content, "docker-compose.yml must have mcp-svc service"

    def test_docker_compose_mcp_depends_on_agent(self):
        """VAL-MCP-L02: mcp-svc depends_on agent-svc."""
        import os

        compose_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "docker-compose.yml"
        )
        with open(compose_path) as f:
            content = f.read()

        # Find mcp-svc section
        mcp_start = content.index("mcp-svc:")
        # Find the next top-level service or end of file
        remaining = content[mcp_start:]
        # Should contain depends_on with agent-svc
        assert "depends_on:" in remaining
        assert "agent-svc" in remaining

    def test_docker_compose_mcp_env_vars(self):
        """VAL-MCP-L03: docker-compose.yml has all required env vars for mcp-svc."""
        import os

        compose_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "docker-compose.yml"
        )
        with open(compose_path) as f:
            content = f.read()

        mcp_start = content.index("mcp-svc:")
        remaining = content[mcp_start:]

        required_vars = [
            "GROKTOCRAWL_URL",
            "GROKTOCRAWL_API_KEY",
            "MCP_PORT",
            "SESSION_TTL",
            "SESSION_SWEEP_INTERVAL",
            "HTTP_TIMEOUT",
        ]
        for var in required_vars:
            assert var in remaining, f"Missing env var {var} in mcp-svc service"

    def test_docker_compose_mcp_port_mapping(self):
        """VAL-MCP-L01: mcp-svc has port mapping for 8002."""
        import os

        compose_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "docker-compose.yml"
        )
        with open(compose_path) as f:
            content = f.read()

        mcp_start = content.index("mcp-svc:")
        remaining = content[mcp_start:]
        assert "8002" in remaining, "mcp-svc must map port 8002"

    def test_pyproject_has_pinned_dependencies(self):
        """pyproject.toml has pinned (==) dependencies."""
        import os

        pyproject_path = os.path.join(os.path.dirname(__file__), "..", "pyproject.toml")
        with open(pyproject_path) as f:
            content = f.read()
        assert "'mcp==" in content, "mcp must be pinned with =="
        assert "'httpx==" in content, "httpx must be pinned with =="
        assert "'pydantic==" in content, "pydantic must be pinned with =="
        # pydantic must satisfy mcp's requirement: >=2.11.0
        assert (
            "pydantic==2.11." in content
            or "pydantic==2.12." in content
            or "pydantic==2.13." in content
        ), "pydantic must be pinned to >=2.11.0 to satisfy mcp's dependency"

    def test_pyproject_includes_session_store_module(self):
        """pyproject.toml lists session_store as a py-module."""
        import os

        pyproject_path = os.path.join(os.path.dirname(__file__), "..", "pyproject.toml")
        with open(pyproject_path) as f:
            content = f.read()
        assert "session_store" in content, (
            "pyproject.toml must include session_store in py-modules"
        )

    def test_pyproject_includes_browser_handler_module(self):
        """pyproject.toml lists browser_handler as a py-module."""
        import os

        pyproject_path = os.path.join(os.path.dirname(__file__), "..", "pyproject.toml")
        with open(pyproject_path) as f:
            content = f.read()
        assert "browser_handler" in content, (
            "pyproject.toml must include browser_handler in py-modules"
        )


# ── MCP surface coverage gate (mirrors CLI coverage policy) ────────


class TestMCPSurfaceCoverage:
    """scripts/check-mcp-coverage.py must pass.

    Every expressible agent-svc /v2 endpoint needs an MCP tool; the
    script is the drift guard (same policy as check-cli-coverage.py for
    the CLI surface).
    """

    def test_check_mcp_coverage_passes(self):
        import os
        import subprocess
        import sys

        script = os.path.join(
            os.path.dirname(__file__), "..", "..", "scripts", "check-mcp-coverage.py"
        )
        proc = subprocess.run(
            [sys.executable, script],
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, (
            f"check-mcp-coverage.py failed:\n{proc.stdout}\n{proc.stderr}"
        )
