"""MCP server exposing GroktoCrawl tools via Model Context Protocol.

Uses FastMCP from the official mcp SDK (v1.x) with Streamable HTTP
transport.  Defines 52 tools matching the GroktoCrawl agent-svc
API surface, with proper readOnlyHint/destructiveHint annotations.

Tool surface policy (see scripts/check-mcp-coverage.py): every agent-svc
``/v2`` endpoint that is expressible as a tool has one.  SSE-streaming,
two-phase-upload, and pre-admission-internal endpoints are exempted
explicitly.
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import time
from typing import Any, Literal

from browser_handler import BrowserHandler
from groktocrawl_client import GroktocrawlClient
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from session_store import SessionStore

logger = logging.getLogger("grokto_crawl.mcp")

# ── Configuration ──────────────────────────────────────────────────

API_URL: str = os.environ.get(
    "GROKTOCRAWL_URL",
    os.environ.get("GROKTOCRAWL_API_URL", "http://agent-svc:8000"),
)
API_KEY: str | None = os.environ.get("GROKTOCRAWL_API_KEY") or None
PORT: int = int(os.environ.get("MCP_PORT", "8002"))
DEFAULT_TIMEOUT: float = float(os.environ.get("HTTP_TIMEOUT", "60"))
_SERVER_START_TIME: float = time.time()

# ── Shared state ───────────────────────────────────────────────────

_client = GroktocrawlClient(
    base_url=API_URL,
    api_key=API_KEY,
    default_timeout=DEFAULT_TIMEOUT,
)

# In-process TTL session store + browser-session router. Browser sessions
# are tracked locally (with SESSION_TTL/SESSION_SWEEP_INTERVAL semantics)
# so tools can validate a session exists before acting on it.
_session_store = SessionStore()
_browser_handler = BrowserHandler(_client, _session_store)

# ── FastMCP server ─────────────────────────────────────────────────

# DNS-rebinding protection: the mcp SDK auto-enables loopback-only Host
# validation when FastMCP is constructed without explicit transport_security
# (its default host is 127.0.0.1). That rejects every non-loopback Host header
# with 421 — the server binds 0.0.0.0, so real clients (Hermes, Claude Code,
# remote MCP consumers) can never connect. Pass explicit settings instead:
#
# - MCP_ALLOWED_HOSTS set   -> protection ON, allowlist = parsed hosts
# - MCP_ALLOWED_HOSTS unset -> protection ON, fail-closed loopback-only
#   allowlist (the SDK's own default: 127.0.0.1, localhost, ::1). The service
#   is published on 0.0.0.0, so deployments that need LAN/Tailscale clients
#   must extend the allowlist via MCP_ALLOWED_HOSTS.
# - MCP_ALLOWED_ORIGINS set -> Origin allowlist = parsed origins (overrides
#   the host-derived default below)
#
# Values are comma-separated Host patterns, e.g.
# "hal2000:*,localhost:*,127.0.0.1:*,[::1]:*". Wildcards apply to the port
# only ("host:*"); a bare "*" matches nothing in this SDK version.


def _build_transport_security() -> TransportSecuritySettings:
    """Build DNS-rebinding protection settings from MCP_ALLOWED_HOSTS.

    Fail-closed: protection is always enabled. An unset MCP_ALLOWED_HOSTS
    falls back to the SDK's loopback-only allowlist; operators extend it
    (never disable it) via MCP_ALLOWED_HOSTS.
    """
    raw = os.environ.get("MCP_ALLOWED_HOSTS", "").strip()
    allowed = [h.strip() for h in raw.split(",") if h.strip()]
    if not allowed:
        allowed = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
        logger.info(
            "MCP_ALLOWED_HOSTS unset — protection ON with loopback-only allowlist"
        )
    else:
        logger.info("DNS-rebinding protection enabled for hosts: %s", allowed)
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=allowed,
        allowed_origins=_build_allowed_origins(allowed),
    )


def _build_allowed_origins(hosts: list[str]) -> list[str]:
    """Derive the Origin allowlist, honoring MCP_ALLOWED_ORIGINS when set.

    Origins default to the host allowlist under both http and https schemes
    so legitimate browser-served client pages are not rejected with 403.
    Operators can scope origins independently of the server's Host allowlist
    via MCP_ALLOWED_ORIGINS (comma-separated, e.g.
    "http://hal2000:*,https://hal2000:*").
    """
    raw = os.environ.get("MCP_ALLOWED_ORIGINS", "").strip()
    if raw:
        origins = [o.strip() for o in raw.split(",") if o.strip()]
        logger.info("MCP_ALLOWED_ORIGINS set — origin allowlist: %s", origins)
        return origins
    origins: list[str] = []
    for h in hosts:
        origins.append(f"http://{h}")
        origins.append(f"https://{h}")
    return origins


mcp = FastMCP("GroktoCrawl", transport_security=_build_transport_security())

# ── Annotation helpers ─────────────────────────────────────────────

_RO = ToolAnnotations(readOnlyHint=True, destructiveHint=False)
# Neutral: modifies server/third-party state without being destructive
# (e.g. updating a monitor, running a check, acting on a browser session).
_NEUTRAL = ToolAnnotations(readOnlyHint=False, destructiveHint=False)
_DESTRUCTIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True)


def _json_text(data: dict[str, Any]) -> str:
    """Serialize a dict to indented JSON for use as MCP text content."""
    return json.dumps(data, indent=2, default=str, ensure_ascii=False)


def _resp(data: dict[str, Any]) -> str:
    """Convert a client result dict to a JSON string response.

    Always returns valid JSON.  When the result contains a truthy
    ``error`` value (non-None, non-empty) and ``success`` is not
    explicitly True, wraps it in a ``{"success": false, ...}``
    envelope so that MCP clients can parse it reliably.
    """
    if isinstance(data, dict):
        error_val = data.get("error")
        success_val = data.get("success")
        if error_val and success_val is not True:
            error_obj: dict[str, Any] = {
                "success": False,
                "error": str(error_val),
            }
            if "status_code" in data:
                error_obj["status_code"] = data["status_code"]
            return _json_text(error_obj)
    return _json_text(data)


def _browser_action_kwargs(**params: Any) -> dict[str, Any]:
    """Drop unset browser-action params so nulls are never sent upstream.

    agent-svc's ``BrowserExecuteRequest`` has non-optional scalar fields
    (e.g. ``timeout``), so passing ``None`` would 422.  Only explicitly
    provided values are forwarded.
    """
    return {key: value for key, value in params.items() if value is not None}


def _ensure_success(data: dict[str, Any]) -> None:
    """Raise :class:`ToolError` if *data* contains an upstream error.

    FastMCP converts ``ToolError`` to a response with ``isError: true``
    so that MCP clients can programmatically detect 4xx/5xx failures
    and failed jobs from the upstream agent-svc.

    Two conditions raise:

    * A truthy ``error`` value (non-None, non-empty) when ``success`` is
      not explicitly True — covers HTTP-level errors.
    * ``status == "failed"`` with a truthy ``error`` even when
      ``success`` is True — covers failed background jobs returned by
      the status endpoints (``get_agent_status``, ``get_crawl_status``,
      etc.), so clients can detect job failure programmatically instead
      of parsing ``{"success": true, "status": "failed"}`` as a success.

    Important: agent-svc includes ``"error": null`` in successful
    responses, so null/empty error values are never treated as errors,
    and ``cancelled`` / ``retry_scheduled`` statuses are returned as-is.
    """
    if isinstance(data, dict):
        error_val = data.get("error")
        success_val = data.get("success")
        status_val = data.get("status")
        # Only treat as error when there's a truthy error value AND
        # success is not explicitly True.
        if error_val and success_val is not True:
            raise ToolError(_resp(data))
        # Failed background jobs: success is True but the job itself failed.
        if status_val == "failed" and error_val:
            raise ToolError(_resp(data))


# ── Tools 1–2: scrape, search (read-only) ─────────────────────────


@mcp.tool(annotations=_RO)
async def scrape(
    url: str,
    formats: list[str] | None = None,
    only_main_content: bool = True,
) -> str:
    """Scrape a single URL and return its content as markdown or other formats.

    Calls POST /v2/scrape on the GroktoCrawl API.  The primary output is
    clean markdown suitable for LLM consumption.  Optionally request
    additional formats (html, screenshot, links, rawHtml, images).

    Args:
        url: The URL to scrape.  Must start with http:// or https://.
        formats: Additional output formats to include.  Supported:
            markdown, html, links, screenshot, rawHtml,
            screenshot@fullPage, images.
        only_main_content: When True (default), extract only the main
            article content.  Set False to get the full page.
    """
    result = await _client.scrape(
        url=url,
        formats=formats,
        only_main_content=only_main_content,
    )
    _ensure_success(result)
    return _resp(result)


@mcp.tool(annotations=_RO)
async def search(
    query: str,
    limit: int = 5,
    sources: list[str] | None = None,
    categories: list[str] | None = None,
    search_type: str | None = None,
    retrieval_mode: str | None = None,
    output_schema: dict[str, Any] | None = None,
    system_prompt: str | None = None,
) -> str:
    """Search the web and return results with URLs titles and snippets.

    Calls POST /v2/search on the GroktoCrawl API.  Supports fast mode
    (raw results, <1s) and rich mode (scraped + LLM synthesis, 1-3s).

    Args:
        query: The search query string.
        limit: Maximum number of results to return (1–100, default 5).
        sources: Optional source-type filter (e.g. web, news, images).
        categories: Optional content-category filter (e.g. research,
            github, pdf, news, science, it).
        search_type: Search mode — ``fast`` for raw results (<1s) or
            ``rich`` for scraped + synthesized results (1-3s).
            Defaults to fast when omitted.
        retrieval_mode: Retrieval strategy — keyword (default),
            semantic, hybrid, vector, or hybrid_vector.
        output_schema: Optional JSON Schema for structured extraction
            from the search results (single-call search→scrape→extract).
        system_prompt: Optional guidance for synthesis behavior.
    """
    result = await _client.search(
        query=query,
        limit=limit,
        sources=sources,
        categories=categories,
        search_type=search_type,
        retrieval_mode=retrieval_mode,
        output_schema=output_schema,
        system_prompt=system_prompt,
    )
    _ensure_success(result)
    return _resp(result)


# ── Tools 3–6: crawl, get_crawl_status, cancel_crawl, get_crawl_errors ──


@mcp.tool(annotations=_RO)
async def crawl(
    url: str,
    max_pages: int | None = None,
    max_depth: int | None = None,
    limit: int | None = None,
    include_paths: list[str] | None = None,
    exclude_paths: list[str] | None = None,
    sitemap: str | None = None,
    ignore_robots_txt: bool = False,
    max_concurrency: int | None = None,
    delay: float | None = None,
    allow_subdomains: bool = False,
    allow_external_links: bool = False,
    prompt: str | None = None,
) -> str:
    """Start a recursive crawl of a website.  Returns a job ID immediately.

    Calls POST /v2/crawl on the GroktoCrawl API.  The crawl runs
    asynchronously — use get_crawl_status to poll for results and
    cancel_crawl to stop an in-progress crawl.

    Args:
        url: The starting URL for the crawl (http:// or https://).
        max_pages: Maximum number of pages to scrape.  Omit for
            unlimited (agent-svc default is 0 = unlimited; set a
            concrete value to bound resource usage).
        max_depth: Maximum link-follow depth from the start URL.
            Default 2 when omitted.
        limit: Optional cap on the total number of pages (alias for
            a bounded crawl budget).
        include_paths: Only crawl URLs whose path matches one of these
            prefixes/regexes.
        exclude_paths: Skip URLs whose path matches one of these
            prefixes/regexes.
        sitemap: Sitemap mode — ``include`` (default), ``skip``, or
            ``only``.
        ignore_robots_txt: When True, bypass robots.txt enforcement.
        max_concurrency: Maximum concurrent page scrapes (1-50).
        delay: Delay in seconds between scrapes (forces concurrency 1).
        allow_subdomains: Crawl links on subdomains of the start URL.
        allow_external_links: Crawl links on other domains.
        prompt: Natural-language description of what to crawl; derives
            include/exclude paths and depth via LLM.  Explicit params
            override LLM-derived ones.
    """
    result = await _client.create_crawl(
        url=url,
        max_pages=max_pages,
        max_depth=max_depth,
        limit=limit,
        include_paths=include_paths,
        exclude_paths=exclude_paths,
        sitemap=sitemap,
        ignore_robots_txt=ignore_robots_txt,
        max_concurrency=max_concurrency,
        delay=delay,
        allow_subdomains=allow_subdomains,
        allow_external_links=allow_external_links,
        prompt=prompt,
    )
    _ensure_success(result)
    return _resp(result)


@mcp.tool(annotations=_RO)
async def get_crawl_status(job_id: str) -> str:
    """Poll the status of a crawl job and return page data when complete.

    Calls GET /v2/crawl/{job_id} on the GroktoCrawl API.  Returns the
    current status (processing/completed/failed), page counts, and
    (when finished) the scraped page content with metadata.

    Args:
        job_id: The crawl job ID returned by the crawl tool.
    """
    result = await _client.get_crawl_status(job_id)
    _ensure_success(result)
    return _resp(result)


@mcp.tool(annotations=_DESTRUCTIVE)
async def cancel_crawl(job_id: str) -> str:
    """Cancel an in-progress crawl job.  The crawl transitions to cancelled.

    Calls DELETE /v2/crawl/{job_id} on the GroktoCrawl API.  Already
    scraped pages are preserved in subsequent status polls.

    Args:
        job_id: The crawl job ID to cancel.
    """
    result = await _client.cancel_crawl(job_id)
    _ensure_success(result)
    return _resp(result)


@mcp.tool(annotations=_RO)
async def get_crawl_errors(job_id: str) -> str:
    """Retrieve per-URL errors and robots-blocked URLs for a crawl job.

    Calls GET /v2/crawl/{job_id}/errors on the GroktoCrawl API.
    Returns a structured list of errors including the failing URL,
    error type, and timestamp.

    Args:
        job_id: The crawl job ID returned by the crawl tool.
    """
    result = await _client.get_crawl_errors(job_id)
    _ensure_success(result)
    return _resp(result)


@mcp.tool(annotations=_RO)
async def get_active_crawls() -> str:
    """List active/processing crawl jobs.

    Calls GET /v2/crawl/active on the GroktoCrawl API.  Returns crawl
    jobs currently processing with crawl-specific fields (url,
    max_pages, max_depth, completed, total).

    Args:
        (none)
    """
    result = await _client.get_active_crawls()
    _ensure_success(result)
    return _resp(result)


# ── Tool 7: map ────────────────────────────────────────────────────


@mcp.tool(annotations=_RO)
async def map(
    url: str,
    limit: int = 100,
    search: str | None = None,
    allow_subdomains: bool = False,
    allow_external_links: bool = False,
) -> str:
    """Discover all URLs linked from a given page (site mapping).

    Calls POST /v2/map on the GroktoCrawl API.  Returns a list of
    URLs found on the page, classified as internal, subdomain, or
    external links.

    Args:
        url: The page URL to map.  Must start with http:// or https://.
        limit: Maximum number of links to return (default 100).
        search: Optional case-insensitive substring filter on links.
        allow_subdomains: Include links on subdomains of the page origin.
        allow_external_links: Include links to other domains.
    """
    result = await _client.map(
        url=url,
        limit=limit,
        search=search,
        allow_subdomains=allow_subdomains,
        allow_external_links=allow_external_links,
    )
    _ensure_success(result)
    return _resp(result)


# ── Tools 8–9: agent, get_agent_status ─────────────────────────────


@mcp.tool(annotations=_RO)
async def agent(
    prompt: str,
    model: str | None = None,
    urls: list[str] | None = None,
    output_schema: dict[str, Any] | None = None,
    citation_style: str | None = None,
    max_credits: int | None = None,
    include_images: bool = False,
    force_fresh: bool = False,
    search_type: str | None = None,
) -> str:
    """Run autonomous research: search → scrape → LLM synthesis with sources.

    Calls POST /v2/agent on the GroktoCrawl API and returns the job ID
    immediately.  The research runs asynchronously — poll with
    get_agent_status for the synthesized answer with cited sources, and
    cancel_agent to stop an in-progress job.

    Args:
        prompt: What the agent should research (max 100k chars).
        model: Optional per-request LLM model override (e.g. ``gpt-4o``).
            When omitted or ``default``, the server-configured model is used.
        urls: Optional seed URLs to constrain research.
        output_schema: Optional JSON Schema for structured output.
        citation_style: Citation formatting — ``inline`` (bare [N]
            markers) or ``compact`` ([N](url) embedded links).
        max_credits: Optional cap on credits consumed by the job.
        include_images: When True, collect images from scraped sources.
        force_fresh: When True, bypass the research-memory cache and run
            a fresh research pipeline.
        search_type: Research depth — ``deep`` (multi-query, default) or
            ``focused`` (single-query, single-pass).
    """
    result = await _client.create_agent(
        prompt=prompt,
        model=model,
        urls=urls,
        output_schema=output_schema,
        citation_style=citation_style,
        max_credits=max_credits,
        include_images=include_images,
        force_fresh=force_fresh,
        search_type=search_type,
    )
    _ensure_success(result)
    return _resp(result)


@mcp.tool(annotations=_RO)
async def get_agent_status(job_id: str) -> str:
    """Poll the status of an agent research job and return results when done.

    Calls GET /v2/agent/{job_id} on the GroktoCrawl API.  Returns the
    current status (processing/completed/failed) and, when completed,
    the research answer with source details and credits used.

    Args:
        job_id: The agent job ID returned by the agent tool.
    """
    result = await _client.get_agent_status(job_id)
    _ensure_success(result)
    return _resp(result)


@mcp.tool(annotations=_DESTRUCTIVE)
async def cancel_agent(job_id: str) -> str:
    """Cancel an in-progress agent research job.

    Calls DELETE /v2/agent/{job_id} on the GroktoCrawl API.  The job
    transitions to cancelled.

    Args:
        job_id: The agent job ID returned by the agent tool.
    """
    result = await _client.cancel_agent(job_id)
    _ensure_success(result)
    return _resp(result)


# ── Tools 12–17: agent-native research sessions ──────────────────


@mcp.tool(annotations=_RO)
async def create_research_session(ttl: int | None = None) -> str:
    """Create a durable server-side research session.

    The returned session ID can be passed to research_session_step,
    get_research_session, export_research_session, resolve_research_session,
    and delete_research_session. Session state survives across MCP calls
    until its TTL expires or it is explicitly deleted.

    Args:
        ttl: Optional session lifetime in seconds. The API default is used
            when omitted.
    """
    result = await _client.create_research_session(ttl=ttl)
    _ensure_success(result)
    return _resp(result)


@mcp.tool(annotations=_NEUTRAL)
async def research_session_step(
    session_id: str,
    action: Literal["search", "scrape", "query", "deepen"],
    query: str | None = None,
    limit: int | None = None,
    urls: list[str] | None = None,
    question: str | None = None,
    ref_id: str | None = None,
    sub_topic: str | None = None,
    max_sources: int | None = None,
    parallel: bool = False,
    idempotency_key: str | None = None,
) -> str:
    """Run one typed search, scrape, query, or deepen step in a session.

    Supply only the fields for the selected action: search uses ``query``
    and optional ``limit``; scrape uses ``urls``; query uses ``question``;
    deepen uses ``ref_id``, ``sub_topic``, and optional ``max_sources``.
    The API rejects missing sessions, invalid transitions, and conflicting
    concurrent or idempotent steps with actionable errors.
    """
    params: dict[str, Any]
    if action == "search":
        if not query or not query.strip():
            raise ToolError("search action requires a non-empty query")
        params = {"query": query}
        if limit is not None:
            params["limit"] = limit
    elif action == "scrape":
        if not urls:
            raise ToolError("scrape action requires a non-empty urls list")
        params = {"urls": urls}
    elif action == "query":
        if not question or not question.strip():
            raise ToolError("query action requires a non-empty question")
        params = {"question": question}
    else:
        if not ref_id or not ref_id.strip():
            raise ToolError("deepen action requires a non-empty ref_id")
        if not sub_topic or not sub_topic.strip():
            raise ToolError("deepen action requires a non-empty sub_topic")
        params = {"ref_id": ref_id, "sub_topic": sub_topic}
        if max_sources is not None:
            params["max_sources"] = max_sources

    result = await _client.research_session_step(
        session_id=session_id,
        action=action,
        params=params,
        parallel=parallel,
        idempotency_key=idempotency_key,
    )
    _ensure_success(result)
    return _resp(result)


@mcp.tool(annotations=_RO)
async def get_research_session(session_id: str) -> str:
    """Get a research session's status, step history, and artifact counts."""
    result = await _client.get_research_session(session_id)
    _ensure_success(result)
    return _resp(result)


@mcp.tool(annotations=_RO)
async def export_research_session(session_id: str) -> str:
    """Export a complete, navigable artifact tree from a research session."""
    result = await _client.export_research_session(session_id)
    _ensure_success(result)
    return _resp(result)


@mcp.tool(annotations=_RO)
async def resolve_research_session(session_id: str, ref_ids: list[str]) -> str:
    """Resolve session reference IDs to full source content and metadata."""
    if not ref_ids:
        raise ToolError("resolve_research_session requires at least one ref_id")
    result = await _client.resolve_research_session(session_id, ref_ids)
    _ensure_success(result)
    return _resp(result)


@mcp.tool(annotations=_DESTRUCTIVE)
async def delete_research_session(session_id: str) -> str:
    """Delete a research session and all accumulated server-side state."""
    result = await _client.delete_research_session(session_id)
    _ensure_success(result)
    return _resp(result)


# ── Tools 18–20: plan-consent research workflow ──────────────────


@mcp.tool(annotations=_RO)
async def create_research_plan(
    prompt: str,
    model: str | None = None,
    urls: list[str] | None = None,
) -> str:
    """Generate a research plan for review without starting execution.

    The response includes a one-shot ``plan_id`` plus proposed phases,
    expected searches, estimated depth, and analysis dimensions. Review it
    with get_research_plan before calling execute_research_plan.

    Args:
        prompt: Research question to decompose into an execution plan.
        model: Optional LLM model override.
        urls: Optional seed URLs to scope the plan.
    """
    result = await _client.create_research_plan(
        prompt=prompt,
        model=model,
        urls=urls,
    )
    _ensure_success(result)
    return _resp(result)


@mcp.tool(annotations=_RO)
async def get_research_plan(plan_id: str) -> str:
    """Retrieve a proposed research plan for human or agent review."""
    result = await _client.get_research_plan(plan_id)
    _ensure_success(result)
    return _resp(result)


@mcp.tool(annotations=_DESTRUCTIVE)
async def execute_research_plan(
    plan_id: str,
    approve: bool,
    narrow: str | None = None,
    add_dimensions: list[str] | None = None,
    remove_dimensions: list[str] | None = None,
    query_overrides: dict[str, str] | None = None,
) -> str:
    """Execute a reviewed research plan after explicit approval.

    Set ``approve`` to True only after inspecting get_research_plan. The
    plan is one-shot: a successful execution consumes it and returns an
    asynchronous research job ID. Optional typed fields narrow scope, add
    or remove analysis dimensions, or replace phase queries. Rejected,
    expired, already-executed, and invalid plan IDs return actionable errors.

    Args:
        plan_id: Plan ID returned by create_research_plan.
        approve: Explicit approval gate; must be True to execute.
        narrow: Optional focus text that narrows the research scope.
        add_dimensions: Optional analysis dimensions to add.
        remove_dimensions: Optional analysis dimensions to remove.
        query_overrides: Optional mapping of zero-based phase indexes to new
            search queries.
    """
    if not approve:
        raise ToolError(
            "execute_research_plan requires approve=True after reviewing the plan"
        )

    if remove_dimensions and query_overrides:
        raise ToolError(
            "remove_dimensions cannot be combined with query_overrides by the plan API"
        )

    modifications: list[dict[str, Any]] | dict[str, Any] = []
    if remove_dimensions:
        # The API's legacy dict form is the typed representation that supports
        # remove_dimension alongside narrow/add_dimension.
        modifications = {
            "narrow": narrow,
            "add_dimension": add_dimensions,
            "remove_dimension": remove_dimensions,
        }
    else:
        if narrow:
            modifications.append({"type": "narrow", "params": {"focus": narrow}})
        for dimension in add_dimensions or []:
            modifications.append(
                {"type": "add_dimension", "params": {"dimension": dimension}}
            )
    for phase_index, new_query in (query_overrides or {}).items():
        if not new_query.strip():
            raise ToolError("query_overrides values must be non-empty queries")
        try:
            phase_number = int(phase_index)
        except ValueError as exc:
            raise ToolError("query_overrides keys must be phase indexes") from exc
        modifications.append(
            {
                "type": "modify_query",
                "params": {"phase_index": phase_number, "new_query": new_query},
            }
        )

    result = await _client.execute_research_plan(
        plan_id=plan_id,
        modifications=modifications or None,
    )
    _ensure_success(result)
    return _resp(result)


# ── Tools 21–28: research-memory lifecycle ───────────────────────


@mcp.tool(annotations=_RO)
async def query_research_memory(
    question: str,
    max_age_hours: int | None = None,
) -> str:
    """Find a compatible cached research artifact by question.

    Calls POST /v2/research-memory/query. The response preserves hit or
    miss status, freshness, similarity, compatibility, artifact ID, and
    source metadata so clients can decide whether to reuse the result.

    Args:
        question: Research question to match against stored artifacts.
        max_age_hours: Optional maximum artifact age, from 1 to 720 hours.
    """
    result = await _client.query_research_memory(
        question=question,
        max_age_hours=max_age_hours,
    )
    _ensure_success(result)
    return _resp(result)


@mcp.tool(annotations=_NEUTRAL)
async def store_research_memory(
    question: str,
    answer: str,
    sources: list[dict[str, Any]],
    metadata: dict[str, Any] | None = None,
) -> str:
    """Store a completed research artifact for reuse across sessions.

    Calls POST /v2/research-memory/store. Source objects are retained with
    the artifact; include URL, title, relevance, or other source metadata.

    Args:
        question: Original research question used for semantic indexing.
        answer: Completed research answer in markdown or plain text.
        sources: Source metadata objects, normally including ``url`` and
            ``title``.
        metadata: Optional model or workflow metadata to store with it.
    """
    result = await _client.store_research_memory(
        question=question,
        answer=answer,
        sources=sources,
        metadata=metadata,
    )
    _ensure_success(result)
    return _resp(result)


@mcp.tool(annotations=_DESTRUCTIVE)
async def delete_research_memory_artifact(artifact_id: str) -> str:
    """Delete a research-memory artifact by its stored artifact ID.

    Calls DELETE /v2/research-memory/{artifact_id}. A successful response
    reports whether the artifact was removed from the backing stores.

    Args:
        artifact_id: ID returned by store_research_memory.
    """
    result = await _client.delete_research_memory_artifact(artifact_id)
    _ensure_success(result)
    return _resp(result)


@mcp.tool(annotations=_RO)
async def get_research_memory(memory_id: str) -> str:
    """Retrieve one complete research-memory artifact by memory ID.

    Calls GET /v2/memory/{memory_id}. The response includes the stored
    question, answer, sources, model, timestamps, and scope metadata.

    Args:
        memory_id: Memory ID returned by a query or batch-query result.
    """
    result = await _client.get_research_memory(memory_id)
    _ensure_success(result)
    return _resp(result)


@mcp.tool(annotations=_DESTRUCTIVE)
async def delete_research_memory(memory_id: str) -> str:
    """Delete one research-memory artifact by memory ID.

    Calls DELETE /v2/memory/{memory_id} and returns a verifiable deletion
    result. Missing IDs are surfaced as an MCP tool error.

    Args:
        memory_id: Memory ID to delete.
    """
    result = await _client.delete_research_memory(memory_id)
    _ensure_success(result)
    return _resp(result)


@mcp.tool(annotations=_DESTRUCTIVE)
async def sweep_research_memory() -> str:
    """Sweep expired research-memory index entries.

    Calls POST /v2/memory/sweep. This admin-style maintenance operation
    removes orphaned vector entries and returns the swept count.
    """
    result = await _client.sweep_research_memory()
    _ensure_success(result)
    return _resp(result)


@mcp.tool(annotations=_RO)
async def batch_query_research_memory(queries: list[str]) -> str:
    """Look up multiple research questions in one memory request.

    Calls POST /v2/memory/batch/query and preserves input ordering. Each
    result includes hit or miss, freshness, similarity, memory ID, answer,
    and source metadata when available.

    Args:
        queries: Research questions to search, up to the API batch limit.
    """
    result = await _client.batch_query_research_memory(queries)
    _ensure_success(result)
    return _resp(result)


@mcp.tool(annotations=_NEUTRAL)
async def batch_store_research_memory(
    entries: list[dict[str, Any]],
) -> str:
    """Store multiple research artifacts with per-entry outcomes.

    Calls POST /v2/memory/batch/store. Each entry must contain ``query``,
    ``artifact``, and ``sources`` and may include ``model``. Partial success
    is preserved in the returned stored and failed counts.

    Args:
        entries: Artifact objects with query, answer text, source metadata,
            and optional model name.
    """
    result = await _client.batch_store_research_memory(entries)
    _ensure_success(result)
    return _resp(result)


# ── Tool 29: answer ────────────────────────────────────────────────


@mcp.tool(annotations=_RO)
async def answer(
    query: str,
    num_sources: int = 5,
    model: str | None = None,
    output_schema: dict[str, Any] | None = None,
    citation_style: str | None = None,
    search_type: str | None = None,
    retrieval_mode: str | None = None,
) -> str:
    """Grounded Q&A: search → scrape → LLM answer with inline citations.

    Calls POST /v2/answer on the GroktoCrawl API.  This is a synchronous
    single-turn endpoint designed for 1-3s latency.  Returns a markdown
    answer with [N] citation markers and a list of source URLs.

    Args:
        query: Natural language question.
        num_sources: Number of sources to scrape and cite (1–20,
            default 5).
        model: Optional per-request LLM model override (e.g. ``gpt-4o``).
        output_schema: Optional JSON Schema for structured output from
            the answer.
        citation_style: Citation formatting — ``inline`` (default) or
            ``compact``.
        search_type: Hint for search depth (default ``auto``).
        retrieval_mode: Retrieval strategy — keyword (default),
            semantic, hybrid, vector, or hybrid_vector.
    """
    result = await _client.answer(
        question=query,
        num_sources=num_sources,
        model=model,
        output_schema=output_schema,
        citation_style=citation_style,
        search_type=search_type,
        retrieval_mode=retrieval_mode,
    )
    _ensure_success(result)
    return _resp(result)


# ── Tools 11–12: extract, get_extract_status ───────────────────────


@mcp.tool(annotations=_RO)
async def extract(
    urls: list[str],
    prompt: str | None = None,
    schema: dict[str, Any] | None = None,
    model: str | None = None,
) -> str:
    """Extract structured data from one or more URLs.  Returns a job ID.

    Calls POST /v2/extract on the GroktoCrawl API.  The extraction runs
    asynchronously — use get_extract_status to poll for results.

    Args:
        urls: List of URLs to extract data from.
        prompt: Natural language description of what to extract
            (e.g. "Extract all product names and prices").
        schema: Optional JSON Schema for structured output.  When
            provided, the LLM returns data matching the schema.
        model: Optional per-request LLM model override (e.g. ``gpt-4o``).
    """
    result = await _client.create_extract(
        urls=urls, prompt=prompt, schema=schema, model=model
    )
    _ensure_success(result)
    return _resp(result)


@mcp.tool(annotations=_RO)
async def get_extract_status(job_id: str) -> str:
    """Poll the status of an extract job and return structured data when done.

    Calls GET /v2/extract/{job_id} on the GroktoCrawl API.  Returns the
    current status and, when completed, the extracted structured data
    matching the requested schema or prompt.

    Args:
        job_id: The extract job ID returned by the extract tool.
    """
    result = await _client.get_extract_status(job_id)
    _ensure_success(result)
    return _resp(result)


# ── Tool 13: enrich ────────────────────────────────────────────────


@mcp.tool(annotations=_RO)
async def enrich(
    url: str | None = None,
    fields: dict[str, dict[str, Any]] | None = None,
    source_hint: str | None = None,
    effort: str = "low",
) -> str:
    """Enrich a URL or entity with web-sourced structured data and source URLs.

    Calls POST /v2/enrich on the GroktoCrawl API.  Searches the web for
    additional context about the entity and returns enriched items with
    source attribution for each field.

    Args:
        url: The URL or entity name to enrich with web context.  When
            omitted, ``fields`` must be provided (a single-item entity
            with a generic summary field is requested by default).
        fields: Optional map of field names to ``{"description": ...}``
            specs describing what to extract, e.g.
            ``{"founded": {"description": "Year the company was founded"}}``.
        source_hint: Optional entity-type hint (company, person, url,
            product) to steer search.
        effort: Extraction effort — ``low`` (default), ``medium``, or
            ``high``.
    """
    result = await _client.enrich(
        url=url,
        fields=fields,
        source_hint=source_hint,
        effort=effort,
    )
    _ensure_success(result)
    return _resp(result)


# ── Tool 14: find_similar ──────────────────────────────────────────


@mcp.tool(annotations=_RO)
async def find_similar(
    url: str,
    limit: int | None = None,
    search_mode: str | None = None,
) -> str:
    """Find pages semantically similar to a given URL using vector embeddings.

    Calls POST /v2/find-similar on the GroktoCrawl API.  Returns a list
    of similar URLs with relevance metadata.

    Args:
        url: The reference URL to find similar pages for.  Must start
            with http:// or https://.
        limit: Maximum number of similar URLs to return.
        search_mode: Similarity source — ``qdrant`` (default, local
            index) or ``web`` (web-assisted reranking).
    """
    result = await _client.find_similar(url=url, limit=limit, search_mode=search_mode)
    _ensure_success(result)
    return _resp(result)


# ── Tool 15: batch_scrape ──────────────────────────────────────────


@mcp.tool(annotations=_RO)
async def batch_scrape(
    urls: list[str],
    max_concurrency: int | None = None,
) -> str:
    """Scrape multiple URLs in a single batch job.  Returns a job ID.

    Calls POST /v2/batch/scrape on the GroktoCrawl API.  The batch runs
    asynchronously — use get_batch_scrape_status to poll for results.

    Args:
        urls: List of URLs to scrape.  All URLs are processed
            concurrently by the scraper service.
        max_concurrency: Optional cap on concurrent scrapes.
    """
    result = await _client.create_batch_scrape(
        urls=urls, max_concurrency=max_concurrency
    )
    _ensure_success(result)
    return _resp(result)


# ── Tool 16: generate_llmstxt ──────────────────────────────────────


@mcp.tool(annotations=_RO)
async def generate_llmstxt(url: str, max_pages: int | None = None) -> str:
    """Generate an llms.txt file for a website.  Returns a job ID.

    Calls POST /v2/generate-llmstxt on the GroktoCrawl API.  The
    generation runs asynchronously — use get_llmstxt_status to poll
    for the completed llms.txt content.

    Args:
        url: The website URL to generate llms.txt for.  Must start
            with http:// or https://.
        max_pages: Maximum pages to include in the llms.txt file.
            Omit for server default.
    """
    result = await _client.create_llmstxt(url=url, max_pages=max_pages)
    _ensure_success(result)
    return _resp(result)


# ── Tool 17: get_activity ──────────────────────────────────────────


@mcp.tool(annotations=_RO)
async def get_activity() -> str:
    """Retrieve recent API activity including job IDs types statuses and timestamps.

    Calls GET /v2/activity on the GroktoCrawl API.  Returns a list of
    recent jobs across all types (scrape crawl agent extract etc.) with
    their current status and creation timestamps.  Useful for monitoring
    and debugging.
    """
    result = await _client.get_activity()
    _ensure_success(result)
    return _resp(result)


# ── Tool 18: get_batch_scrape_status ────────────────────────────────


@mcp.tool(annotations=_RO)
async def get_batch_scrape_status(job_id: str) -> str:
    """Poll the status of a batch scrape job and return results when done.

    Calls GET /v2/batch/scrape/{job_id} on the GroktoCrawl API.
    Returns the current status (processing/completed/failed) and,
    when finished, the scraped content for each URL in the batch.

    Args:
        job_id: The batch scrape job ID returned by the batch_scrape tool.
    """
    result = await _client.get_batch_scrape_status(job_id)
    _ensure_success(result)
    return _resp(result)


@mcp.tool(annotations=_DESTRUCTIVE)
async def cancel_batch_scrape(job_id: str) -> str:
    """Cancel an in-progress batch scrape job.

    Calls DELETE /v2/batch/scrape/{job_id} on the GroktoCrawl API.  The
    job transitions to cancelled.

    Args:
        job_id: The batch scrape job ID returned by the batch_scrape tool.
    """
    result = await _client.cancel_batch_scrape(job_id)
    _ensure_success(result)
    return _resp(result)


@mcp.tool(annotations=_RO)
async def get_batch_scrape_errors(job_id: str) -> str:
    """Retrieve per-URL errors for a batch scrape job.

    Calls GET /v2/batch/scrape/{job_id}/errors on the GroktoCrawl API.
    Returns a structured list of errors including the failing URL and
    error type.

    Args:
        job_id: The batch scrape job ID returned by the batch_scrape tool.
    """
    result = await _client.get_batch_scrape_errors(job_id)
    _ensure_success(result)
    return _resp(result)


# ── Tool 19: get_llmstxt_status ─────────────────────────────────────


@mcp.tool(annotations=_RO)
async def get_llmstxt_status(job_id: str) -> str:
    """Poll the status of an llms.txt generation job and return the file when done.

    Calls GET /v2/generate-llmstxt/{job_id} on the GroktoCrawl API.
    Returns the current status and, when completed, the generated
    llms.txt content.

    Args:
        job_id: The llms.txt generation job ID returned by the
            generate_llmstxt tool.
    """
    result = await _client.get_llmstxt_status(job_id)
    _ensure_success(result)
    return _resp(result)


# ── Tool 21: parse ────────────────────────────────────────────────


@mcp.tool(annotations=_RO)
async def parse(file_url: str) -> str:
    """Parse a document (PDF, DOCX, PPTX, etc.) served at a URL to markdown.

    Calls POST /v2/parse on the GroktoCrawl API.  Downloads the file
    (with a separate unauthenticated client, so the GroktoCrawl API key
    is never sent to third-party hosts) and uploads it to the parse
    endpoint.

    Args:
        file_url: Direct URL of the document to parse.
    """
    result = await _client.parse(file_url)
    _ensure_success(result)
    return _resp(result)


# ── Tool 20: resolve_citations ──────────────────────────────────────


@mcp.tool(annotations=_RO)
async def resolve_citations(
    text: str,
    sources: list[dict[str, Any]],
    style: str = "inline",
) -> str:
    """Resolve compact citation IDs in markdown text to full source cards.

    Calls POST /v2/citations/resolve on the GroktoCrawl API.  In
    ``compact`` style, replaces ``[N]`` markers with ``[N](url)``
    self-contained links.  In ``inline`` style, returns the text
    unchanged with a separate citations map.

    Args:
        text: The markdown text containing ``[N]`` citation markers.
        sources: List of source objects, each with ``url`` and
            ``title`` keys.  Sources are 1-indexed — the first source
            corresponds to ``[1]``.
        style: Citation style: ``compact`` transforms ``[N]`` to
            ``[N](url)``; ``inline`` leaves ``[N]`` as-is.  Defaults
            to ``inline``.
    """
    result = await _client.resolve_citations(text=text, sources=sources, style=style)
    _ensure_success(result)
    return _resp(result)


# ── Tools 22-25: browser sessions ─────────────────────────────────


@mcp.tool(annotations=_RO)
async def create_browser_session(ttl: int = 300) -> str:
    """Create a short-lived headless browser session.  Returns a session ID.

    Calls POST /v2/browser on the GroktoCrawl API.  Use
    browser_execute to act on the session and destroy_browser_session
    to close it.

    Args:
        ttl: Session TTL in seconds (30–3600, default 300).
    """
    result = await _browser_handler.create_session(ttl=ttl)
    _ensure_success(result)
    return _resp(result)


@mcp.tool(annotations=_NEUTRAL)
async def browser_execute(
    session_id: str,
    action: str,
    url: str | None = None,
    selector: str | None = None,
    text: str | None = None,
    script: str | None = None,
    timeout: int | None = None,
) -> str:
    """Execute an action in a headless browser session.

    Calls POST /v2/browser/{session_id}/execute on the GroktoCrawl API.

    Args:
        session_id: The browser session ID from create_browser_session.
        action: One of: navigate, click, type, screenshot, scroll, wait,
            getContent, executeScript, select, write.
        url: Target URL (navigate action).
        selector: CSS selector (click/type/select actions).
        text: Text to type (type/write actions).
        script: JavaScript source (executeScript action).
        timeout: Action timeout in milliseconds (default 10000).
    """
    result = await _browser_handler.execute_action(
        session_id=session_id,
        action=action,
        **_browser_action_kwargs(
            url=url,
            selector=selector,
            text=text,
            script=script,
            timeout=timeout,
        ),
    )
    _ensure_success(result)
    return _resp(result)


@mcp.tool(annotations=_RO)
async def list_browser_sessions() -> str:
    """List active headless browser sessions.

    Calls GET /v2/browser on the GroktoCrawl API.

    Args:
        (none)
    """
    result = await _client.browser_list()
    _ensure_success(result)
    return _resp(result)


@mcp.tool(annotations=_DESTRUCTIVE)
async def destroy_browser_session(session_id: str) -> str:
    """Destroy a headless browser session.

    Calls DELETE /v2/browser/{session_id} on the GroktoCrawl API.

    Args:
        session_id: The browser session ID to destroy.
    """
    result = await _browser_handler.destroy_session(session_id)
    _ensure_success(result)
    return _resp(result)


# ── Tools 26-31: change monitors ──────────────────────────────────


@mcp.tool(annotations=_RO)
async def create_monitor(
    url: str | None = None,
    schedule: str = "0 */6 * * *",
    webhook: str | None = None,
    monitor_type: str = "scrape",
    query: str | None = None,
    sources: list[str] | None = None,
    categories: list[str] | None = None,
    num_results: int | None = None,
) -> str:
    """Create a scheduled change monitor.  Returns a monitor ID.

    Calls POST /v2/monitor on the GroktoCrawl API.  Scrape-type monitors
    watch a URL for content changes; search-type monitors watch search
    results for new items (requires ``query``).

    Args:
        url: URL to monitor (required for monitor_type ``scrape``).
        schedule: Cron expression for check frequency (default
            ``0 */6 * * *`` = every 6 hours).
        webhook: URL called when a change is detected.
        monitor_type: ``scrape`` (default) or ``search``.
        query: Search query (required for monitor_type ``search``).
        sources: Source types for search monitors (web, news, images,
            video, social).
        categories: Content categories for search monitors (research,
            github, pdf, news, science, it).
        num_results: Max results per check for search monitors.
    """
    if monitor_type not in ("scrape", "search"):
        raise ToolError(
            f"create_monitor: monitor_type must be 'scrape' or 'search', "
            f"got {monitor_type!r}"
        )
    if monitor_type == "search" and not query:
        raise ToolError("create_monitor: query is required for monitor_type='search'")
    if monitor_type == "scrape" and not url:
        raise ToolError("create_monitor: url is required for monitor_type='scrape'")
    search_config: dict[str, Any] | None = None
    if monitor_type == "search":
        search_config = {"query": query}
        if sources:
            search_config["sources"] = sources
        if categories:
            search_config["categories"] = categories
        if num_results is not None:
            search_config["numResults"] = num_results
    result = await _client.monitor_create(
        url=url,
        schedule=schedule,
        webhook=webhook,
        monitor_type=monitor_type,
        search_config=search_config,
    )
    _ensure_success(result)
    return _resp(result)


@mcp.tool(annotations=_RO)
async def list_monitors() -> str:
    """List all change monitors.

    Calls GET /v2/monitor on the GroktoCrawl API.

    Args:
        (none)
    """
    result = await _client.monitor_list()
    _ensure_success(result)
    return _resp(result)


@mcp.tool(annotations=_RO)
async def get_monitor(monitor_id: str) -> str:
    """Get a change monitor's details and last check result.

    Calls GET /v2/monitor/{monitor_id} on the GroktoCrawl API.

    Args:
        monitor_id: The monitor ID returned by create_monitor.
    """
    result = await _client.monitor_get(monitor_id)
    _ensure_success(result)
    return _resp(result)


@mcp.tool(annotations=_NEUTRAL)
async def update_monitor(
    monitor_id: str,
    url: str | None = None,
    schedule: str | None = None,
    webhook: str | None = None,
    query: str | None = None,
    sources: list[str] | None = None,
    categories: list[str] | None = None,
    num_results: int | None = None,
) -> str:
    """Update a change monitor's configuration.

    Calls PATCH /v2/monitor/{monitor_id} on the GroktoCrawl API.  Only
    provided fields are changed.

    Args:
        monitor_id: The monitor ID to update.
        url: New URL to monitor (scrape type).
        schedule: New cron expression.
        webhook: New webhook URL.
        query: New search query (search type).
        sources: New source types (search type).
        categories: New content categories (search type).
        num_results: New max results per check (search type).
    """
    search_config: dict[str, Any] | None = None
    if query is not None or sources or categories or num_results is not None:
        if query is None:
            # agent-svc replaces the whole search_config on PATCH, so a
            # partial search-field update must preserve the existing
            # query instead of silently resetting it to "".
            current = await _client.monitor_get(monitor_id)
            if current.get("error"):
                return current  # _ensure_success below surfaces the error
            existing_query = (current.get("search_config") or {}).get("query")
            if not existing_query:
                raise ToolError(
                    "update_monitor: cannot update search fields — monitor "
                    f"{monitor_id!r} has no search query; pass query explicitly"
                )
            query = existing_query
        search_config = {"query": query}
        if sources:
            search_config["sources"] = sources
        if categories:
            search_config["categories"] = categories
        if num_results is not None:
            search_config["numResults"] = num_results
    result = await _client.monitor_update(
        monitor_id=monitor_id,
        url=url,
        schedule=schedule,
        webhook=webhook,
        search_config=search_config,
    )
    _ensure_success(result)
    return _resp(result)


@mcp.tool(annotations=_NEUTRAL)
async def run_monitor(monitor_id: str) -> str:
    """Manually trigger a monitor check immediately.

    Calls POST /v2/monitor/{monitor_id}/run on the GroktoCrawl API.
    Runs the check regardless of the cron schedule and returns the
    updated monitor status including any diff or new results.

    Args:
        monitor_id: The monitor ID to run.
    """
    result = await _client.monitor_run(monitor_id)
    _ensure_success(result)
    return _resp(result)


@mcp.tool(annotations=_DESTRUCTIVE)
async def delete_monitor(monitor_id: str) -> str:
    """Delete a change monitor.

    Calls DELETE /v2/monitor/{monitor_id} on the GroktoCrawl API.

    Args:
        monitor_id: The monitor ID to delete.
    """
    result = await _client.monitor_delete(monitor_id)
    _ensure_success(result)
    return _resp(result)


# ── Auth middleware ────────────────────────────────────────────────


class _AuthMiddleware:
    """ASGI middleware that enforces Bearer token auth when
    GROKTOCRAWL_API_KEY is set in the environment.
    """

    def __init__(self, app: Any, api_key: str) -> None:
        self._app = app
        self._api_key = api_key

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        # Only protect /mcp path; allow health and others through
        path: str = scope.get("path", "")
        if not path.startswith("/mcp"):
            await self._app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        auth_bytes = headers.get(b"authorization", b"")
        auth_str = auth_bytes.decode() if auth_bytes else ""

        valid = auth_str.startswith("Bearer ") and hmac.compare_digest(
            auth_str[7:], self._api_key
        )
        if not valid:
            body = json.dumps({"error": "Unauthorized"}).encode()
            await send(
                {
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode()),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return

        await self._app(scope, receive, send)


# ── Health endpoint ────────────────────────────────────────────────


async def _check_agent_svc() -> bool:
    """Check whether agent-svc is reachable and healthy.

    Returns True if agent-svc responds with HTTP 200, False otherwise.
    Uses a short timeout so the health check is non-blocking.
    """
    import httpx

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(3.0)) as client:
            resp = await client.get(f"{API_URL}/health")
            return resp.status_code == 200
    except Exception:
        return False


async def _health_endpoint(scope: dict, receive: Any, send: Any) -> None:
    """ASGI health-check handler returning server status and agent-svc connectivity."""
    agent_svc_status = await _check_agent_svc()
    uptime = time.time() - _SERVER_START_TIME
    body = json.dumps(
        {
            "status": "ok",
            "agent_svc": "connected" if agent_svc_status else "disconnected",
            "uptime_seconds": round(uptime, 1),
        }
    ).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


# ── Logging middleware ─────────────────────────────────────────────


class _LoggingMiddleware:
    """ASGI middleware that logs MCP requests at INFO level.

    Logs method, tool name (when available), and a session prefix.
    Never logs API keys or full content bodies.
    """

    def __init__(self, app: Any) -> None:
        self._app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        path: str = scope.get("path", "")

        # Collect request body for MCP method extraction
        body_chunks: list[bytes] = []

        async def _recv() -> dict:
            msg = await receive()
            if msg.get("type") == "http.request" and msg.get("body"):
                body_chunks.append(msg["body"])
            return msg

        async def _send(msg: dict) -> None:
            # Log on response start
            if msg.get("type") == "http.response.start":
                status: int = msg.get("status", 0)
                session_prefix = ""
                headers = dict(scope.get("headers", []))
                sid = headers.get(b"mcp-session-id", b"")
                if sid:
                    session_prefix = sid.decode()[:8] + "... "
                if path.startswith("/mcp"):
                    tool_name = ""
                    if body_chunks:
                        try:
                            body = json.loads(b"".join(body_chunks))
                            mcp_method = body.get("method", "")
                            if mcp_method == "tools/call":
                                tool_name = " tool=" + body.get("params", {}).get(
                                    "name", "?"
                                )
                            mcp_info = f"{mcp_method}{tool_name}"
                        except (json.JSONDecodeError, KeyError, TypeError):
                            mcp_info = "?"
                    else:
                        mcp_info = "?"
                    logger.info(
                        "MCP request: method=%s%s session=%sstatus=%s",
                        mcp_info,
                        "",
                        session_prefix,
                        status,
                    )
            await send(msg)

        if path == "/health":
            await _health_endpoint(scope, receive, send)
        else:
            await self._app(scope, _recv, _send)


# ── Entrypoint ─────────────────────────────────────────────────────


def main() -> None:
    """Start the MCP server with Streamable HTTP transport on port 8002."""
    import asyncio
    import signal

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logger.info(
        "Starting GroktoCrawl MCP server on port %s (agent-svc: %s)",
        PORT,
        API_URL,
    )

    app = mcp.streamable_http_app()

    # Logging middleware (innermost, so it sees all MCP requests)
    app.add_middleware(_LoggingMiddleware)

    # Auth middleware (outermost, so it blocks before logging/processing)
    if API_KEY:
        logger.info("API key auth enabled for /mcp path")
        app.add_middleware(_AuthMiddleware, api_key=API_KEY)
    else:
        logger.info("No API key set — auth disabled")

    import uvicorn

    config = uvicorn.Config(
        app,
        host="0.0.0.0",
        port=PORT,
        timeout_graceful_shutdown=30,
    )
    server = uvicorn.Server(config)

    # Signal handlers log shutdown so operators can see the drain window.
    # uvicorn's own signal handling (installed inside serve()) drives the
    # actual graceful shutdown with timeout_graceful_shutdown.
    def _handle_shutdown() -> None:
        logger.info("Received shutdown signal, draining in-flight requests...")

    loop = asyncio.new_event_loop()
    try:
        loop.add_signal_handler(signal.SIGTERM, _handle_shutdown)
        loop.add_signal_handler(signal.SIGINT, _handle_shutdown)
    except NotImplementedError:
        # Signal handlers not available on this platform (e.g. Windows)
        import contextlib

        for sig in (signal.SIGTERM, signal.SIGINT):
            with contextlib.suppress(ValueError, OSError):
                signal.signal(sig, lambda *_: _handle_shutdown())

    loop.run_until_complete(server.serve())


if __name__ == "__main__":
    main()
