"""MCP server exposing GroktoCrawl tools via Model Context Protocol.

Uses FastMCP from the official mcp SDK (v1.x) with Streamable HTTP
transport.  Defines 20 tools matching the GroktoCrawl agent-svc
API surface, with proper readOnlyHint/destructiveHint annotations.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

from groktocrawl_client import GroktocrawlClient
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

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

# ── FastMCP server ─────────────────────────────────────────────────

mcp = FastMCP("GroktoCrawl")

# ── Annotation helpers ─────────────────────────────────────────────

_RO = ToolAnnotations(readOnlyHint=True, destructiveHint=False)
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


def _ensure_success(data: dict[str, Any]) -> None:
    """Raise :class:`ToolError` if *data* contains an upstream error.

    FastMCP converts ``ToolError`` to a response with ``isError: true``
    so that MCP clients can programmatically detect 4xx/5xx failures
    from the upstream agent-svc.

    Important: agent-svc includes ``"error": null`` in successful
    responses, so we must only treat truthy error values (non-None,
    non-empty) as actual errors.  Additionally, an explicit
    ``"success": true`` flag overrides any error key.
    """
    if isinstance(data, dict):
        error_val = data.get("error")
        success_val = data.get("success")
        # Only treat as error when there's a truthy error value AND
        # success is not explicitly True.
        if error_val and success_val is not True:
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
        url=url, formats=formats, only_main_content=only_main_content
    )
    _ensure_success(result)
    return _resp(result)


@mcp.tool(annotations=_RO)
async def search(
    query: str,
    limit: int = 5,
    search_type: str | None = None,
) -> str:
    """Search the web and return results with URLs titles and snippets.

    Calls POST /v2/search on the GroktoCrawl API.  Supports fast mode
    (raw results, <1s) and rich mode (scraped + LLM synthesis, 1-3s).

    Args:
        query: The search query string (max 10k chars).
        limit: Maximum number of results to return (1–100, default 5).
        search_type: Search mode — ``fast`` for raw results (<1s) or
            ``rich`` for scraped + synthesized results (1-3s).
            Defaults to fast when omitted.
    """
    result = await _client.search(query=query, limit=limit, search_type=search_type)
    _ensure_success(result)
    return _resp(result)


# ── Tools 3–6: crawl, get_crawl_status, cancel_crawl, get_crawl_errors ──


@mcp.tool(annotations=_DESTRUCTIVE)
async def crawl(
    url: str,
    max_pages: int | None = None,
    max_depth: int | None = None,
) -> str:
    """Start a recursive crawl of a website.  Returns a job ID immediately.

    Calls POST /v2/crawl on the GroktoCrawl API.  The crawl runs
    asynchronously — use get_crawl_status to poll for results and
    cancel_crawl to stop an in-progress crawl.

    Args:
        url: The starting URL for the crawl (http:// or https://).
        max_pages: Maximum number of pages to scrape.  Omit for
            unlimited (capped by server default).
        max_depth: Maximum link-follow depth from the start URL.
            Default 2 when omitted.
    """
    result = await _client.create_crawl(
        url=url, max_pages=max_pages, max_depth=max_depth
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


# ── Tool 7: map ────────────────────────────────────────────────────


@mcp.tool(annotations=_RO)
async def map(url: str, limit: int = 100) -> str:
    """Discover all URLs linked from a given page (site mapping).

    Calls POST /v2/map on the GroktoCrawl API.  Returns a list of
    URLs found on the page, classified as internal, subdomain, or
    external links.

    Args:
        url: The page URL to map.  Must start with http:// or https://.
        limit: Maximum number of links to return (default 100).
    """
    result = await _client.map(url=url, limit=limit)
    _ensure_success(result)
    return _resp(result)


# ── Tools 8–9: agent, get_agent_status ─────────────────────────────


@mcp.tool(annotations=_RO)
async def agent(
    prompt: str,
    model: str | None = None,
) -> str:
    """Run autonomous research: search → scrape → LLM synthesis with sources.

    Calls POST /v2/agent on the GroktoCrawl API, creates a research job,
    and polls until completion.  Returns the synthesized answer with
    cited sources.  For long-running research, the job ID is returned
    and get_agent_status can be used to poll independently.

    Args:
        prompt: What the agent should research (max 100k chars).
        model: Optional per-request LLM model override (e.g. ``gpt-4o``).
            When omitted or ``default``, the server-configured model is used.
    """
    result = await _client.agent(prompt=prompt, model=model)
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


# ── Tool 10: answer ────────────────────────────────────────────────


@mcp.tool(annotations=_RO)
async def answer(query: str, num_sources: int = 5) -> str:
    """Grounded Q&A: search → scrape → LLM answer with inline citations.

    Calls POST /v2/answer on the GroktoCrawl API.  This is a synchronous
    single-turn endpoint designed for 1-3s latency.  Returns a markdown
    answer with [N] citation markers and a list of source URLs.

    Args:
        query: Natural language question (max 10k chars).
        num_sources: Number of sources to scrape and cite (1–20,
            default 5).
    """
    result = await _client.answer(question=query, num_sources=num_sources)
    _ensure_success(result)
    return _resp(result)


# ── Tools 11–12: extract, get_extract_status ───────────────────────


@mcp.tool(annotations=_RO)
async def extract(
    urls: list[str],
    prompt: str | None = None,
    schema: dict[str, Any] | None = None,
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
    """
    result = await _client.create_extract(urls=urls, prompt=prompt, schema=schema)
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
async def enrich(url: str) -> str:
    """Enrich a URL or entity with web-sourced structured data and source URLs.

    Calls POST /v2/enrich on the GroktoCrawl API.  Searches the web for
    additional context about the entity and returns enriched items with
    source attribution for each field.

    Args:
        url: The URL or entity name to enrich with web context.
    """
    result = await _client.enrich(url=url)
    _ensure_success(result)
    return _resp(result)


# ── Tool 14: find_similar ──────────────────────────────────────────


@mcp.tool(annotations=_RO)
async def find_similar(url: str) -> str:
    """Find pages semantically similar to a given URL using vector embeddings.

    Calls POST /v2/find-similar on the GroktoCrawl API.  Returns a list
    of similar URLs with relevance metadata.

    Args:
        url: The reference URL to find similar pages for.  Must start
            with http:// or https://.
    """
    result = await _client.find_similar(url=url)
    _ensure_success(result)
    return _resp(result)


# ── Tool 15: batch_scrape ──────────────────────────────────────────


@mcp.tool(annotations=_DESTRUCTIVE)
async def batch_scrape(urls: list[str]) -> str:
    """Scrape multiple URLs in a single batch job.  Returns a job ID.

    Calls POST /v2/batch/scrape on the GroktoCrawl API.  The batch runs
    asynchronously — use get_batch_scrape_status to poll for results.

    Args:
        urls: List of URLs to scrape.  All URLs are processed
            concurrently by the scraper service.
    """
    result = await _client.create_batch_scrape(urls=urls)
    _ensure_success(result)
    return _resp(result)


# ── Tool 16: generate_llmstxt ──────────────────────────────────────


@mcp.tool(annotations=_DESTRUCTIVE)
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

        if not auth_str.startswith("Bearer ") or auth_str[7:] != self._api_key:
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


_AGENT_SVC_HEALTHY: bool | None = None


async def _check_agent_svc() -> bool:
    """Check whether agent-svc is reachable and healthy.

    Returns True if agent-svc responds with HTTP 200, False otherwise.
    Uses a short timeout so the health check is non-blocking.
    """
    global _AGENT_SVC_HEALTHY
    import httpx

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(3.0)) as client:
            resp = await client.get(f"{API_URL}/health")
            _AGENT_SVC_HEALTHY = resp.status_code == 200
            return _AGENT_SVC_HEALTHY
    except Exception:
        _AGENT_SVC_HEALTHY = False
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
                            body = json.loads(body_chunks[0])
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

    # Signal handlers for clean shutdown with drain
    shutdown_event = asyncio.Event()

    def _handle_shutdown() -> None:
        logger.info("Received shutdown signal, draining in-flight requests...")
        shutdown_event.set()

    loop = asyncio.new_event_loop()
    try:
        loop.add_signal_handler(signal.SIGTERM, _handle_shutdown)
        loop.add_signal_handler(signal.SIGINT, _handle_shutdown)
    except NotImplementedError:
        # Signal handlers not available on this platform (e.g. Windows)
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                signal.signal(sig, lambda *_: _handle_shutdown())
            except (ValueError, OSError):
                pass

    loop.run_until_complete(server.serve())


if __name__ == "__main__":
    main()
