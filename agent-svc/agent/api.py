"""Route handlers implementing the GroktoCrawl API surface.

Targets Firecrawl v2 API compatibility where possible.
"""

import logging
from datetime import UTC
from typing import Any

import httpx
from fastapi import APIRouter, Request, Response

from .exceptions import (
    BrowserError,
    InvalidRequestError,
    NotFoundError,
    ScrapeError,
    UpstreamError,
)
from .models import (
    ActivityItem,
    ActivityResponse,
    AgentCancelResponse,
    AgentCreateResponse,
    AgentRequest,
    AgentStatusResponse,
    AnswerRequest,
    AnswerResponse,
    BatchScrapeErrorsResponse,
    BatchScrapeRequest,
    BatchScrapeStatusResponse,
    BrowserCreateRequest,
    BrowserCreateResponse,
    BrowserDeleteResponse,
    BrowserExecuteRequest,
    BrowserExecuteResponse,
    BrowserListResponse,
    Citation,
    CrawlActiveItem,
    CrawlActiveResponse,
    CrawlCreateResponse,
    CrawlErrorItem,
    CrawlErrorsResponse,
    CrawlRequest,
    CrawlStatusResponse,
    EnrichRequest,
    EnrichResponse,
    ExtractCreateResponse,
    ExtractRequest,
    ExtractStatusResponse,
    FindSimilarRequest,
    FindSimilarResponse,
    LLMsTextCreateResponse,
    LLMsTextRequest,
    LLMsTextStatusResponse,
    MapRequest,
    MapResponse,
    MonitorCreateRequest,
    MonitorDeleteResponse,
    MonitorListResponse,
    MonitorResponse,
    MonitorUpdateRequest,
    MonitorCheckItem,
    MonitorCheckListResponse,
    ParamsPreviewRequest,
    ParamsPreviewResponse,
    ParseResponse,
    ParseUploadUrlResponse,
    ScrapeData,
    ScrapeRequest,
    ScrapeResponse,
    SearchRequest,
    SearchResponse,
    SearchResult,
    Source,
)
from .monitor import delete_monitor, get_all_monitors, get_monitor, get_monitor_check, get_monitor_checks, run_monitor, save_monitor
from .store import JobStore

logger = logging.getLogger(__name__)

router = APIRouter()


def _get_client_ip(request: Request) -> str:
    """Extract the client IP address from the request.

    Respects the ``X-Forwarded-For`` header for reverse-proxy deployments.
    Falls back to ``request.client.host`` when the header is absent.
    """
    xff = request.headers.get("X-Forwarded-For")
    if xff:
        return xff.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


@router.get("/v2/activity", response_model=ActivityResponse)
async def list_activity(request: Request) -> ActivityResponse:
    """List all active/processing jobs across all job types.

    Returns jobs with status ``processing``, ordered by creation time.
    Completed and failed jobs are excluded (they TTL out after 24h).
    """
    store: JobStore = request.app.state.job_store
    jobs = store.list_active_jobs(limit=50)
    items: list[ActivityItem] = []
    for job in jobs:
        payload = job.get("payload") or {}
        url = payload.get("url") if isinstance(payload, dict) else None
        items.append(
            ActivityItem(
                id=job["id"],
                kind=job.get("kind", "unknown"),
                status=job.get("status", "processing"),
                url=url,
                created_at=job.get("created_at", ""),
                completed_at=job.get("completed_at"),
            )
        )
    return ActivityResponse(data=items)


@router.post("/v2/scrape", response_model=ScrapeResponse)
async def scrape(request: Request, body: ScrapeRequest) -> ScrapeResponse:
    scraper = request.app.state.scraper_client
    result = await scraper.scrape(body.url)
    if result.get("success"):
        scraper_data = result["data"]
        # Fire-and-forget index the page
        markdown = scraper_data.get("markdown", "")
        if markdown:
            title = scraper_data.get("metadata", {}).get("title", "")
            request.app.state.task_tracker.create_background_task(
                _index_scrape(body.url, title, markdown, request)
            )
        return ScrapeResponse(
            success=True,
            data=ScrapeData(
                markdown=scraper_data.get("markdown", ""),
                metadata=scraper_data.get("metadata")
                or {"source": scraper_data.get("source", "unknown")},
            ),
        )
    raise ScrapeError(detail=result.get("error", "Scrape failed"))


@router.post("/v2/agent", response_model=AgentCreateResponse)
async def create_agent(request: Request, body: AgentRequest, response: Response) -> Any:
    # ── Per-client rate limit check ────────────────────────────
    client_ip = _get_client_ip(request)
    rate_limiter = request.app.state.rate_limiter
    allowed, rate_remaining = await rate_limiter.check(f"{client_ip}:search")
    if not allowed:
        from .exceptions import RateLimitedError
        from .metrics import METRICS

        METRICS.counter("search_calls_total", "Total search calls", ["status"]).inc(
            {"status": "rate_limited"}
        )
        raise RateLimitedError(
            detail=f"Per-client rate limit exceeded ({rate_limiter.limit}/{rate_limiter.window}s)"
        )

    max_searches = request.app.state.max_searches_per_request

    # ── Metrics ──────────────────────────────────────────────────
    from .metrics import METRICS

    METRICS.counter("search_calls_total", "Total search calls", ["status"]).inc(
        {"status": "allowed"}
    )

    # Streaming path — run inline, return SSE
    if body.stream:
        # Pre-flight LLM health check — fail fast before opening the stream
        from .llm import LLMClient

        health_logger = logging.getLogger(__name__)
        effective_model = (
            body.model
            if body.model and body.model != "default"
            else request.app.state.llm_model
        )
        llm_check = LLMClient(
            base_url=request.app.state.llm_base_url,
            api_key=request.app.state.llm_api_key,
            model=effective_model,
        )
        if not await llm_check.check_health():
            health_logger.error("LLM backend unreachable. Agent disabled.")
            await llm_check.close()
            from fastapi import HTTPException

            raise HTTPException(
                status_code=503,
                detail="LLM backend is not available. Cannot process agent request.",
            )
        await llm_check.close()
        from fastapi.responses import StreamingResponse

        async def event_stream() -> Any:
            from .research import run_research_stream

            async for event in run_research_stream(
                prompt=body.prompt,
                urls=body.urls,
                schema=body.schema_,
                searxng_url=request.app.state.searxng_url,
                scraper_url=request.app.state.scraper_url,
                llm_base_url=request.app.state.llm_base_url,
                llm_api_key=request.app.state.llm_api_key,
                llm_model=request.app.state.llm_model,
                requested_model=body.model if body.model != "default" else None,
                max_searches_per_request=max_searches,
            ):
                import json

                if event["type"] == "sources_pending":
                    yield f"data: {json.dumps({'type': 'sources_pending', 'sources': event['sources']})}\n\n"
                elif event["type"] == "source_scraped":
                    yield f"data: {json.dumps({'type': 'source_scraped', 'url': event['url'], 'source': event.get('source', ''), 'chars': event.get('chars', 0)})}\n\n"
                elif event["type"] == "sources":
                    yield f"data: {json.dumps({'type': 'sources', 'sources': event['sources']})}\n\n"
                elif event["type"] == "token":
                    yield f"data: {json.dumps({'type': 'token', 'content': event['content']})}\n\n"
                elif event["type"] == "done":
                    yield f"data: {json.dumps({'type': 'done', 'result': event['result'], 'sources': event['sources'], 'latency_ms': event['latency_ms']})}\n\n"
                elif event["type"] == "error":
                    yield f"data: {json.dumps({'type': 'error', 'content': event['content']})}\n\n"
                elif event["type"] == "status":
                    yield f"data: {json.dumps({'type': 'status', 'state': event['state']})}\n\n"
                elif event["type"] == "research_plan":
                    yield f"data: {json.dumps({'type': 'research_plan', 'strategy': event['strategy'], 'queries': event['queries'], 'reasoning': event['reasoning']})}\n\n"
                elif event["type"] == "research_pass":
                    yield f"data: {json.dumps({'type': 'research_pass', 'pass': event['pass'], 'total_passes': event['total_passes']})}\n\n"
            yield "data: [DONE]\n\n"

        headers = {
            "X-Search-Budget": f"{max_searches}/{max_searches}",
            "X-Search-Rate-Remaining": f"{rate_remaining}/{rate_limiter.limit}",
        }
        return StreamingResponse(  # type: ignore[return-value]
            event_stream(), media_type="text/event-stream", headers=headers
        )

    # Sync path — create job, process in background
    store: JobStore = request.app.state.job_store
    job_id = store.create_job(
        kind="agent", payload=body.model_dump(exclude_none=True, by_alias=True)
    )

    # Process inline (synchronous) for MVP — no RQ worker needed.
    # A separate worker container can be added later for proper async.

    from .worker import _process_agent_async

    request.app.state.task_tracker.create_background_task(
        _process_agent_async(
            job_id=job_id,
            prompt=body.prompt,
            urls=body.urls,
            schema_=body.schema_,
            llm_base_url=request.app.state.llm_base_url,
            llm_api_key=request.app.state.llm_api_key,
            llm_model=request.app.state.llm_model,
            searxng_url=request.app.state.searxng_url,
            scraper_url=request.app.state.scraper_url,
            webhook_config=body.webhook,
            requested_model=body.model,
        )
    )

    response.headers["X-Search-Budget"] = f"{max_searches}/{max_searches}"
    response.headers["X-Search-Rate-Remaining"] = (
        f"{rate_remaining}/{rate_limiter.limit}"
    )
    return AgentCreateResponse(id=job_id)


@router.get("/v2/agent/{job_id}", response_model=AgentStatusResponse)
async def get_agent_status(request: Request, job_id: str) -> AgentStatusResponse:
    store: JobStore = request.app.state.job_store
    job = store.get_job(job_id)
    if job is None:
        raise NotFoundError(detail="Job not found", details={"job_id": job_id})
    return AgentStatusResponse(
        success=True,
        status=job.get("status", "processing"),
        data=job.get("data"),
        error=job.get("error"),
        expires_at=job.get("completed_at") or job.get("created_at"),
    )


@router.delete("/v2/agent/{job_id}", response_model=AgentCancelResponse)
async def cancel_agent(request: Request, job_id: str) -> AgentCancelResponse:
    store: JobStore = request.app.state.job_store
    if not store.cancel_job(job_id):
        raise NotFoundError(
            detail="Job not found or already completed", details={"job_id": job_id}
        )
    return AgentCancelResponse(success=True)


@router.post("/v2/crawl", response_model=CrawlCreateResponse)
async def create_crawl(
    request: Request, body: CrawlRequest, response: Response
) -> CrawlCreateResponse:
    # ── Per-client rate limit check (VAL-CONC-047) ────────────
    client_ip = _get_client_ip(request)
    rate_limiter = request.app.state.rate_limiter
    allowed, rate_remaining = await rate_limiter.check(f"{client_ip}:crawl")
    if not allowed:
        from .exceptions import RateLimitedError
        from .metrics import METRICS

        METRICS.counter("search_calls_total", "Total search calls", ["status"]).inc(
            {"status": "rate_limited"}
        )
        raise RateLimitedError(
            detail=f"Per-client rate limit exceeded ({rate_limiter.limit}/{rate_limiter.window}s)"
        )

    response.headers["X-Crawl-Rate-Remaining"] = (
        f"{rate_remaining}/{rate_limiter.limit}"
    )

    # ── NL→params: derive from prompt, merge with explicit params ──
    include_paths = body.include_paths
    exclude_paths = body.exclude_paths
    max_depth = body.max_depth

    if body.prompt:
        from .nl_params import derive_crawl_params, merge_params

        # Detect which fields were explicitly set by the user
        # (exclude_unset=True only includes fields present in the request body)
        explicitly_set = body.model_dump(exclude_unset=True)

        # Explicit params that the user set (non-None values that were in the request)
        explicit: dict[str, object] = {}
        if "include_paths" in explicitly_set and body.include_paths is not None:
            explicit["include_paths"] = body.include_paths
        if "exclude_paths" in explicitly_set and body.exclude_paths is not None:
            explicit["exclude_paths"] = body.exclude_paths
        if "max_depth" in explicitly_set:
            explicit["max_depth"] = body.max_depth

        llm_result = await derive_crawl_params(
            prompt=body.prompt,
            llm_base_url=request.app.state.llm_base_url,
            llm_api_key=request.app.state.llm_api_key,
            llm_model=request.app.state.llm_model,
        )

        llm_error = llm_result.get("error")
        if llm_error:
            logger.warning("NL→params for crawl %s: %s", "pending", llm_error)

        # Merge: explicit beats LLM
        merged = merge_params(llm_result, explicit)  # type: ignore[arg-type]

        # Apply merged values (only if not overridden by explicit user params)
        if "include_paths" in merged and "include_paths" not in explicitly_set:
            include_paths = merged["include_paths"]
        if "exclude_paths" in merged and "exclude_paths" not in explicitly_set:
            exclude_paths = merged["exclude_paths"]
        if "max_depth" in merged and "max_depth" not in explicitly_set:
            max_depth = merged["max_depth"]

    store: JobStore = request.app.state.job_store
    job_id = store.create_job(kind="crawl", payload=body.model_dump())

    # Resolve limit vs max_pages conflict (stricter wins, per VAL-CRAWL-089)
    effective_max_pages = body.max_pages
    if body.limit is not None:
        effective_max_pages = min(body.max_pages, body.limit)

    # ── Streaming path: run inline, return SSE ────────────
    if body.stream:
        from fastapi.responses import StreamingResponse

        from .crawl_stream import crawl_event_stream
        from .settings import load_settings as _load_crawl_settings

        _settings = _load_crawl_settings()

        async def event_stream() -> Any:
            async for event in crawl_event_stream(
                job_id=job_id,
                url=body.url,
                max_pages=effective_max_pages,
                max_depth=max_depth,
                scraper_url=request.app.state.scraper_url,
                store=store,
                task_tracker=request.app.state.task_tracker,
                webhook_config=body.webhook,
                ignore_query_parameters=body.ignore_query_parameters,
                include_paths=include_paths,
                exclude_paths=exclude_paths,
                regex_on_full_url=body.regex_on_full_url,
                verbose=body.verbose,
                sitemap_mode=body.sitemap,
                crawl_entire_domain=body.crawl_entire_domain,
                allow_subdomains=body.allow_subdomains,
                allow_external_links=body.allow_external_links,
                max_concurrency=body.max_concurrency,
                delay=body.delay,
                ignore_robots_txt=body.ignore_robots_txt,
                robots_user_agent=body.robots_user_agent,
                scrape_options=body.scrape_options.model_dump(
                    mode="json", by_alias=True
                )
                if body.scrape_options
                else None,
                max_duration_seconds=_settings.crawl_max_duration_seconds,
                idle_timeout_seconds=_settings.crawl_idle_timeout_seconds,
            ):
                yield event
            yield "data: [DONE]\n\n"

        sse_headers = {
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Access-Control-Allow-Origin": "*",
            "Location": f"/v2/crawl/{job_id}/stream",
            "X-Accel-Buffering": "no",
        }
        return StreamingResponse(  # type: ignore[return-value]
            event_stream(),
            media_type="text/event-stream",
            headers=sse_headers,
        )

    # ── Sync path (non-streaming): create background task ─────────
    from .worker import _process_crawl_async

    request.app.state.task_tracker.create_background_task(
        _process_crawl_async(
            job_id=job_id,
            url=body.url,
            max_pages=effective_max_pages,
            max_depth=max_depth,
            scraper_url=request.app.state.scraper_url,
            webhook_config=body.webhook,
            task_tracker=request.app.state.task_tracker,
            ignore_query_parameters=body.ignore_query_parameters,
            include_paths=include_paths,
            exclude_paths=exclude_paths,
            regex_on_full_url=body.regex_on_full_url,
            verbose=body.verbose,
            sitemap_mode=body.sitemap,
            crawl_entire_domain=body.crawl_entire_domain,
            allow_subdomains=body.allow_subdomains,
            allow_external_links=body.allow_external_links,
            max_concurrency=body.max_concurrency,
            delay=body.delay,
            ignore_robots_txt=body.ignore_robots_txt,
            robots_user_agent=body.robots_user_agent,
            scrape_options=body.scrape_options.model_dump(mode="json", by_alias=True)
            if body.scrape_options
            else None,
        )
    )
    return CrawlCreateResponse(id=job_id)


@router.get("/v2/crawl/active", response_model=CrawlActiveResponse)
async def list_active_crawls(
    request: Request,
    status: str = "processing",
) -> CrawlActiveResponse:
    """List all active crawl jobs.

    Returns only jobs with ``kind: "crawl"``. By default returns jobs
    with ``status: "processing"`` (excluding completed, failed, and
    cancelled crawls). Filterable via the ``status`` query parameter.

    Each item includes crawl-specific fields: ``url``, ``max_pages``,
    ``max_depth``, ``completed``, ``total``, ``status``, and ``created_at``.

    Returns an empty ``data`` array (HTTP 200) when no active crawls exist.
    """
    store: JobStore = request.app.state.job_store
    jobs = store.list_active_jobs(kind="crawl", status=status, limit=50)
    items: list[CrawlActiveItem] = []
    for job in jobs:
        payload = job.get("payload") or {}
        url = payload.get("url") if isinstance(payload, dict) else None
        max_pages = payload.get("max_pages") if isinstance(payload, dict) else None
        max_depth = payload.get("max_depth") if isinstance(payload, dict) else None

        # Get completed/total from data payload (set during crawl progress)
        data = job.get("data") or {}
        completed = data.get("completed", 0) if isinstance(data, dict) else 0
        total = data.get("total", 0) if isinstance(data, dict) else 0

        items.append(
            CrawlActiveItem(
                id=job["id"],
                url=url,
                status=job.get("status", "processing"),
                created_at=job.get("created_at", ""),
                completed=completed,
                total=total,
                max_pages=max_pages,
                max_depth=max_depth,
            )
        )
    return CrawlActiveResponse(data=items)


@router.get("/v2/crawl/{job_id}", response_model=CrawlStatusResponse)
async def get_crawl_status(
    request: Request,
    job_id: str,
    offset: int = 0,
) -> CrawlStatusResponse:
    """Get crawl job status and paginated results.

    Supports pagination via the ``offset`` query parameter and ``next``
    response field. When the serialized response exceeds ~10MB, a ``next``
    URL is included in the response pointing to the next chunk.

    Args:
        request: FastAPI request object.
        job_id: The crawl job UUID.
        offset: Zero-based index of the first page to return in this chunk.
            Used for paginated retrieval (default: 0). The ``next`` field
            in the response points to the next offset.

    Returns:
        ``CrawlStatusResponse`` with ``data`` (paginated), ``next`` URL,
        ``credits_used``, timestamps, and per-page metadata.
    """
    store: JobStore = request.app.state.job_store
    job = store.get_job(job_id)
    if job is None:
        raise NotFoundError(detail="Job not found", details={"job_id": job_id})
    data = job.get("data") or {}
    all_pages: list[dict] = data.get("pages", []) or []

    # Compute duration in milliseconds from created_at to completed_at
    created_at = job.get("created_at")
    completed_at = job.get("completed_at")
    duration: int | None = None
    if created_at and completed_at:
        try:
            from datetime import datetime as _dt

            created_dt = _dt.fromisoformat(created_at)
            completed_dt = _dt.fromisoformat(completed_at)
            duration = int((completed_dt - created_dt).total_seconds() * 1000)
        except (ValueError, TypeError):
            duration = None

    # Determine the credits used (1 per completed page)
    completed_count = data.get("completed", 0)
    credits_used = completed_count or len(all_pages)

    # ── Pagination: determine the chunk to return ───────────────
    # We aim for each response to be under ~10MB to match Firecrawl's
    # pagination behavior. Each page is roughly estimated at 10KB on
    # average, so we return pages in chunks of ~1000.
    # If the user provided an offset, slice from there.
    # Otherwise, start from 0 and estimate chunk size.
    _max_chunk_bytes = 10 * 1024 * 1024  # 10MB
    _estimated_page_bytes = 10 * 1024  # ~10KB per page (conservative estimate)
    _max_pages_per_chunk = max(1, _max_chunk_bytes // _estimated_page_bytes)

    chunk_pages = all_pages[offset:]
    next_url: str | None = None

    # If the remaining pages might exceed the size limit, paginate
    if len(chunk_pages) > _max_pages_per_chunk:
        chunk_pages = all_pages[offset : offset + _max_pages_per_chunk]
        next_offset = offset + _max_pages_per_chunk
        if next_offset < len(all_pages):
            # Build the next URL — use the request's base URL
            scheme = request.url.scheme
            host = request.url.netloc
            path = request.url.path
            next_url = f"{scheme}://{host}{path}?offset={next_offset}"
    elif offset > 0 and not chunk_pages:
        # Offset beyond end — return empty data
        chunk_pages = []

    return CrawlStatusResponse(
        status=job.get("status", "processing"),
        completed=completed_count,
        total=data.get("total", 0),
        credits_used=credits_used,
        data=chunk_pages or (all_pages if offset == 0 else []),
        error=job.get("error"),
        next=next_url,
        created_at=created_at,
        completed_at=completed_at,
        expires_at=job.get("expires_at"),
        duration=duration,
    )


@router.delete("/v2/crawl/{job_id}", response_model=AgentCancelResponse)
async def cancel_crawl(request: Request, job_id: str) -> AgentCancelResponse:
    store: JobStore = request.app.state.job_store
    if not store.cancel_job(job_id):
        raise NotFoundError(
            detail="Job not found or already completed", details={"job_id": job_id}
        )
    return AgentCancelResponse(success=True)


@router.get("/v2/crawl/{job_id}/stream")
async def stream_crawl(request: Request, job_id: str) -> Any:
    """Reconnect to a crawl SSE stream or replay completed results.

    For a processing crawl, streams current progress plus future events.
    For a completed crawl, replays all results as SSE events.
    Returns 404 if the job does not exist.

    SSE events include:
        - ``page``: per-page data with url, markdown, metadata
        - ``progress``: periodic progress with completed/total
        - ``done``: final result with summary stats
        - ``error``: per-page failure or overall failure
    """
    store: JobStore = request.app.state.job_store
    job = store.get_job(job_id)
    if job is None:
        raise NotFoundError(detail="Job not found", details={"job_id": job_id})

    status = job.get("status", "processing")
    data = job.get("data") or {}
    pages = data.get("pages", []) if isinstance(data, dict) else []
    errors = data.get("errors", []) if isinstance(data, dict) else []
    total = data.get("total", 0) if isinstance(data, dict) else 0
    completed = data.get("completed", 0) if isinstance(data, dict) else 0

    from fastapi.responses import StreamingResponse

    async def event_stream() -> Any:
        import json

        event_id = 0

        # Replay already-scraped pages
        for page in pages:
            event_id += 1
            page_payload = {
                "type": "page",
                "url": page.get("url", ""),
                "markdown": page.get("markdown", ""),
                "metadata": page.get("metadata", {}),
            }
            yield f"id: {event_id}\ndata: {json.dumps(page_payload)}\n\n"

        # Replay errors
        for error_entry in errors:
            event_id += 1
            error_payload = {
                "type": "error",
                "url": error_entry.get("url", ""),
                "error": error_entry.get("error", "Unknown error"),
            }
            yield f"id: {event_id}\ndata: {json.dumps(error_payload)}\n\n"

        # Send final done event for completed/failed/cancelled jobs
        if status == "completed":
            event_id += 1
            done_payload = {
                "type": "done",
                "id": job_id,
                "status": "completed",
                "pages": pages,
                "total": total,
                "completed": completed,
                "latency_ms": 0,
            }
            yield f"id: {event_id}\ndata: {json.dumps(done_payload)}\n\n"
        elif status == "failed":
            event_id += 1
            error_payload = {
                "type": "error",
                "content": job.get("error", "Crawl failed"),
            }
            yield f"id: {event_id}\ndata: {json.dumps(error_payload)}\n\n"
        else:
            # For processing/cancelled jobs, send current status
            event_id += 1
            progress_payload = {
                "type": "progress",
                "completed": completed,
                "total": total or completed,
                "status": status,
            }
            yield f"id: {event_id}\ndata: {json.dumps(progress_payload)}\n\n"
            event_id += 1
            done_payload = {
                "type": "done",
                "id": job_id,
                "status": status,
                "pages": pages,
                "total": total or completed,
                "completed": completed,
                "latency_ms": 0,
            }
            yield f"id: {event_id}\ndata: {json.dumps(done_payload)}\n\n"

        yield "data: [DONE]\n\n"

    sse_headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "Access-Control-Allow-Origin": "*",
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers=sse_headers,
    )


@router.post("/v2/crawl/params-preview", response_model=ParamsPreviewResponse)
async def params_preview(
    request: Request, body: ParamsPreviewRequest
) -> ParamsPreviewResponse:
    """Preview crawl parameters derived from a natural-language prompt.

    Accepts a ``url`` and ``prompt``, translates the prompt into crawl
    parameters using the LLM, and returns the derived parameters WITHOUT
    starting a crawl job.

    The endpoint is synchronous — no job ID is created.

    Returns:
        - ``include_paths``, ``exclude_paths``, ``max_depth``,
          ``limit``, and other derived params
        - ``error`` if the LLM is unavailable or returns invalid JSON
          (the caller can still proceed with default crawl params)
    """
    from .nl_params import derive_crawl_params

    llm_base_url = request.app.state.llm_base_url
    llm_api_key = request.app.state.llm_api_key
    llm_model = request.app.state.llm_model

    result = await derive_crawl_params(
        prompt=body.prompt,
        llm_base_url=llm_base_url,
        llm_api_key=llm_api_key,
        llm_model=llm_model,
    )

    return ParamsPreviewResponse(
        success=("error" not in result),
        include_paths=result.get("include_paths"),
        exclude_paths=result.get("exclude_paths"),
        max_depth=result.get("max_depth"),
        limit=result.get("max_pages"),
        ignore_robots_txt=result.get("ignore_robots_txt"),
        robots_user_agent=result.get("robots_user_agent"),
        deduplicate_similar_urls=result.get("deduplicate_similar_urls"),
        error=result.get("error"),
    )


@router.get("/v2/crawl/{job_id}/errors", response_model=CrawlErrorsResponse)
async def get_crawl_errors(request: Request, job_id: str) -> CrawlErrorsResponse:
    """Return per-URL errors and robots-blocked URLs for a crawl job.

    Returns a ``CrawlErrorsResponse`` with:

    - ``errors``: list of error objects. Each has ``url``, ``error``
      (human-readable message), ``error_type`` (machine-readable
      category), ``error_code``, and ``timestamp``.
    - ``robots_blocked``: subset of ``errors`` containing only URLs that
      were blocked by robots.txt or politeness rate limiting. Each entry
      has ``error_type: "robots_blocked"``.

    Scraper failures appear in ``errors`` but NOT in ``robots_blocked``.
    Politeness/robots.txt blocks appear in BOTH arrays.

    Returns 404 for unknown job IDs. Returns empty arrays for successful
    crawls with no errors. Errors persist until the job TTL expires (24h
    after creation).
    """
    store: JobStore = request.app.state.job_store
    job = store.get_job(job_id)
    if job is None:
        raise NotFoundError(detail="Job not found", details={"job_id": job_id})
    data = job.get("data") or {}
    raw_errors: list[dict] = data.get("errors", [])
    raw_robots_blocked: list[dict] = data.get("robots_blocked", [])
    return CrawlErrorsResponse(
        success=True,
        errors=[CrawlErrorItem(**e) for e in raw_errors],
        robots_blocked=[CrawlErrorItem(**e) for e in raw_robots_blocked],
    )


@router.post("/v2/batch/scrape", response_model=CrawlCreateResponse)
async def create_batch_scrape(
    request: Request, body: BatchScrapeRequest
) -> CrawlCreateResponse:
    store: JobStore = request.app.state.job_store
    job_id = store.create_job(kind="batch_scrape", payload=body.model_dump())

    from .worker import _process_batch_scrape_async

    request.app.state.task_tracker.create_background_task(
        _process_batch_scrape_async(
            job_id=job_id,
            urls=body.urls,
            scraper_url=request.app.state.scraper_url,
            webhook_config=body.webhook,
            task_tracker=request.app.state.task_tracker,
        )
    )
    return CrawlCreateResponse(id=job_id)


@router.get("/v2/batch/scrape/{job_id}", response_model=BatchScrapeStatusResponse)
async def get_batch_scrape_status(
    request: Request,
    job_id: str,
    offset: int = 0,
) -> BatchScrapeStatusResponse:
    """Get batch scrape job status and paginated results."""
    store: JobStore = request.app.state.job_store
    job = store.get_job(job_id)
    if job is None:
        raise NotFoundError(detail="Job not found", details={"job_id": job_id})
    data = job.get("data") or {}
    all_pages: list[dict] = data.get("pages", []) or []

    created_at = job.get("created_at")
    completed_at = job.get("completed_at")
    duration: int | None = None
    if created_at and completed_at:
        try:
            from datetime import datetime as _dt

            created_dt = _dt.fromisoformat(created_at)
            completed_dt = _dt.fromisoformat(completed_at)
            duration = int((completed_dt - created_dt).total_seconds() * 1000)
        except (ValueError, TypeError):
            duration = None

    completed_count = data.get("completed", 0)
    credits_used = completed_count or len(all_pages)

    # Pagination
    _max_chunk_bytes = 10 * 1024 * 1024
    _estimated_page_bytes = 10 * 1024
    _max_pages_per_chunk = max(1, _max_chunk_bytes // _estimated_page_bytes)

    chunk_pages = all_pages[offset:]
    next_url: str | None = None

    if len(chunk_pages) > _max_pages_per_chunk:
        chunk_pages = all_pages[offset : offset + _max_pages_per_chunk]
        next_offset = offset + _max_pages_per_chunk
        if next_offset < len(all_pages):
            scheme = request.url.scheme
            host = request.url.netloc
            path = request.url.path
            next_url = f"{scheme}://{host}{path}?offset={next_offset}"
    elif offset > 0 and not chunk_pages:
        chunk_pages = []

    return BatchScrapeStatusResponse(
        status=job.get("status", "processing"),
        completed=completed_count,
        total=data.get("total", 0),
        credits_used=credits_used,
        data=chunk_pages or (all_pages if offset == 0 else []),
        error=job.get("error"),
        next=next_url,
        created_at=created_at,
        completed_at=completed_at,
        expires_at=job.get("expires_at"),
        duration=duration,
    )


@router.delete("/v2/batch/scrape/{job_id}", response_model=AgentCancelResponse)
async def cancel_batch_scrape(request: Request, job_id: str) -> AgentCancelResponse:
    """Cancel an in-progress batch scrape job."""
    store: JobStore = request.app.state.job_store
    if not store.cancel_job(job_id):
        raise NotFoundError(
            detail="Job not found or already completed", details={"job_id": job_id}
        )
    return AgentCancelResponse(success=True)


@router.get(
    "/v2/batch/scrape/{job_id}/errors", response_model=BatchScrapeErrorsResponse
)
async def get_batch_scrape_errors(
    request: Request, job_id: str
) -> BatchScrapeErrorsResponse:
    """Get per-URL errors for a batch scrape job."""
    store: JobStore = request.app.state.job_store
    job = store.get_job(job_id)
    if job is None:
        raise NotFoundError(detail="Job not found", details={"job_id": job_id})
    data = job.get("data") or {}
    raw_errors: list[dict] = data.get("errors", [])
    return BatchScrapeErrorsResponse(
        success=True,
        errors=[CrawlErrorItem(**e) for e in raw_errors],
    )


@router.post("/v1/search")
async def search_v1(request: Request, body: SearchRequest) -> dict[str, Any]:
    """Firecrawl v1-compatible search endpoint.

    Returns a flat data array (v1 format) rather than the nested
    data.web / data.images / data.news structure used by v2.
    """
    from .searxng_client import SearXNGClient

    searxng = SearXNGClient(request.app.state.searxng_url)
    try:
        results, _health = await searxng.search(
            body.query,
            limit=body.limit,
            categories=body.categories,
            sources=body.sources,
        )
        return {
            "success": True,
            "data": [
                {
                    "url": r["url"],
                    "title": r["title"],
                    "description": r.get("description", ""),
                }
                for r in results
            ],
        }
    finally:
        await searxng.close()


@router.post("/v2/search", response_model=SearchResponse)
async def search(request: Request, body: SearchRequest) -> SearchResponse:
    if body.stream:
        from fastapi.responses import StreamingResponse

        async def event_stream():
            from .research import run_search_stream

            async for event in run_search_stream(
                query=body.query,
                limit=body.limit,
                search_type=body.search_type,
                retrieval_mode=body.retrieval_mode,
                categories=body.categories,
                sources=body.sources,
                output_schema=body.output_schema,
                system_prompt=body.system_prompt,
                searxng_url=request.app.state.searxng_url,
                scraper_url=request.app.state.scraper_url,
                semantic_url=request.app.state.semantic_url,
                llm_base_url=request.app.state.llm_base_url,
                llm_api_key=request.app.state.llm_api_key,
                llm_model=request.app.state.llm_model,
            ):
                import json

                yield f"data: {json.dumps(event)}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(event_stream(), media_type="text/event-stream")  # type: ignore[return-value]

    from .searxng_client import SearXNGClient

    searxng = SearXNGClient(request.app.state.searxng_url)
    try:
        # Deep mode: multi-pass search with gap analysis and follow-up queries
        if body.search_type == "deep":
            from .research import run_deep_search

            deep_result = await run_deep_search(
                query=body.query,
                limit=body.limit,
                searxng_url=request.app.state.searxng_url,
                llm_base_url=request.app.state.llm_base_url,
                llm_api_key=request.app.state.llm_api_key,
                llm_model=request.app.state.llm_model,
            )
            search_results = deep_result["results"]
            data: dict[str, list] = {"web": search_results, "images": [], "news": []}
            return SearchResponse(
                data=data, query_variations=deep_result.get("query_variations", [])
            )

        # Vector-only mode: query Qdrant, no SearXNG
        if body.retrieval_mode == "vector":
            from .semantic_client import SemanticClient

            semantic = SemanticClient(request.app.state.semantic_url)
            try:
                vector_results = await semantic.search_vector(
                    body.query, limit=body.limit
                )
                search_results = [
                    SearchResult(url=r["url"], title=r["title"], description="")
                    for r in vector_results
                ]
            finally:
                await semantic.close()

        # Hybrid vector mode: SearXNG + Qdrant in parallel, merge, dedup
        elif body.retrieval_mode == "hybrid_vector":
            from .semantic_client import SemanticClient

            semantic = SemanticClient(request.app.state.semantic_url)
            try:
                # Fetch SearXNG results first
                searxng_results, _health = await searxng.search(
                    body.query,
                    limit=body.limit,
                    categories=body.categories,
                    sources=body.sources,
                )
                # Query vector index in parallel (async would be better, but sequential for now)
                vector_results = await semantic.search_vector(
                    body.query, limit=body.limit
                )

                # Convert both to SearchResult lists
                kw_results = [
                    SearchResult(
                        url=r["url"],
                        title=r["title"],
                        description=r.get("description", ""),
                    )
                    for r in searxng_results
                ]
                vec_results = [
                    SearchResult(url=r["url"], title=r["title"], description="")
                    for r in vector_results
                ]

                # Merge and dedup by URL (keep first occurrence — SearXNG has richer metadata)
                seen: set[str] = set()
                merged: list[SearchResult] = []
                for r in kw_results + vec_results:
                    if r.url not in seen:
                        seen.add(r.url)
                        merged.append(r)

                search_results = merged[: body.limit]
            finally:
                await semantic.close()

        else:
            # Keyword, semantic, hybrid: standard SearXNG path
            results, _health = await searxng.search(
                body.query,
                limit=body.limit,
                categories=body.categories,
                sources=body.sources,
            )
            search_results = [
                SearchResult(
                    url=r["url"], title=r["title"], description=r.get("description", "")
                )
                for r in results
            ]

        # Semantic/hybrid retrieval: rerank results by embedding similarity
        if body.retrieval_mode in ("semantic", "hybrid") and results:
            from .scraper_client import ScraperClient
            from .semantic_client import SemanticClient

            semantic = SemanticClient(request.app.state.semantic_url)
            scraper = ScraperClient(request.app.state.scraper_url)
            try:
                # Scrape content for top results
                urls_to_scrape = [r["url"] for r in results[: body.limit]]
                contents = []
                for url in urls_to_scrape:
                    try:
                        scraped = await scraper.scrape(url)
                        content = (
                            scraped.get("data", {}).get("markdown", "")
                            if scraped.get("success")
                            else ""
                        )
                        contents.append(content[:2000])  # Truncate for embedding
                    except Exception:
                        contents.append("")

                # Embed query + document contents
                texts = [body.query, *contents]
                embeddings = await semantic.embed(texts)
                query_embedding = embeddings[0]
                doc_embeddings = embeddings[1:]

                if body.retrieval_mode == "hybrid":
                    # Cross-encoder reranker for merged keyword+semantic scoring
                    reranked = await semantic.rerank(
                        body.query,
                        [r.description for r in search_results[: body.limit]],
                        top_k=body.limit,
                    )
                    # Reorder by cross-encoder scores
                    new_order = [item["index"] for item in reranked]
                    search_results = [
                        search_results[i] for i in new_order if i < len(search_results)
                    ]
                else:
                    # Cosine similarity reranking (vectors are L2-normalized, so cosine = dot product)
                    similarities = [
                        sum(
                            a * b
                            for a, b in zip(query_embedding, doc_emb, strict=False)
                        )
                        for doc_emb in doc_embeddings
                    ]
                    ranked_indices = sorted(
                        range(len(similarities)),
                        key=lambda i: similarities[i],
                        reverse=True,
                    )
                    search_results = [
                        search_results[i]
                        for i in ranked_indices
                        if i < len(search_results)
                    ]

            finally:
                await semantic.close()
                await scraper.close()

        # Route results to the correct top-level key based on sources filter
        data = {"web": [], "images": [], "news": []}
        if body.sources:
            for src in body.sources:
                if src in data:
                    data[src] = search_results
        else:
            data["web"] = search_results

        # Rich mode: scrape results and synthesize enriched content
        output = None
        if body.search_type == "rich" and body.retrieval_mode in (
            "keyword",
            "semantic",
            "hybrid",
            "vector",
            "hybrid_vector",
        ):
            from .research import run_rich_search

            output = await run_rich_search(
                search_results=[
                    {"url": r.url, "title": r.title, "description": r.description}
                    for r in search_results
                ],
                query=body.query,
                limit=body.limit,
                output_schema=body.output_schema,
                system_prompt=body.system_prompt,
                scraper_url=request.app.state.scraper_url,
                llm_base_url=request.app.state.llm_base_url,
                llm_api_key=request.app.state.llm_api_key,
                llm_model=request.app.state.llm_model,
            )

        # ── Contents options: per-result highlights, summary, extras ──
        if body.contents:
            from .llm import LLMClient
            from .research import process_contents_for_results
            from .scraper_client import ScraperClient

            llm_client = LLMClient(
                request.app.state.llm_base_url,
                request.app.state.llm_api_key,
                request.app.state.llm_model,
            )
            scraper_client = ScraperClient(request.app.state.scraper_url)
            try:
                # Build result dicts from current search_results
                result_dicts = [
                    {"url": r.url, "title": r.title, "description": r.description}
                    for r in search_results
                ]
                enriched = await process_contents_for_results(
                    result_dicts,
                    body.query,
                    body.contents,
                    llm_client,
                    scraper_client,
                )
                # Update search_results with enriched data
                search_results = [
                    SearchResult(
                        url=r["url"],
                        title=r["title"],
                        description=r.get("description", ""),
                        highlights=r.get("highlights"),
                        summary=r.get("summary"),
                        extras=r.get("extras"),
                        markdown=r.get("markdown"),
                    )
                    for r in enriched
                ]
            finally:
                await llm_client.close()
                await scraper_client.close()

        return SearchResponse(data=data, output=output)
    finally:
        await searxng.close()


@router.post("/v2/find-similar", response_model=FindSimilarResponse)
async def find_similar(request: Request, body: FindSimilarRequest):
    """Find semantically similar pages for a given URL.

    Two search modes:
    - ``qdrant`` (default): Scrapes the URL, embeds its content, and
      searches the local Qdrant vector index for similar pages.
    - ``web``: Scrapes the URL, extracts keywords, searches the open web
      via SearXNG, then reranks results by cosine similarity against
      the query URL's embedding.
    """
    import time

    from .research import run_find_similar

    start = time.monotonic()
    results = await run_find_similar(
        url=body.url,
        limit=body.limit,
        search_mode=body.search_mode,
        scraper_url=request.app.state.scraper_url,
        semantic_url=request.app.state.semantic_url,
        searxng_url=request.app.state.searxng_url,
    )
    latency = (time.monotonic() - start) * 1000

    return FindSimilarResponse(
        data=results,  # type: ignore[arg-type]
        query_url=body.url,
        search_mode=body.search_mode,
        latency_ms=round(latency, 1),
    )


@router.post("/v2/answer", response_model=AnswerResponse)
async def answer(request: Request, body: AnswerRequest, response: Response) -> Any:
    """Grounded Q&A: search → scrape → LLM → citations.

    Synchronous single-turn endpoint. For streaming, set ``stream: true``
    to receive Server-Sent Events.
    """
    # ── Per-client rate limit check ────────────────────────────
    client_ip = _get_client_ip(request)
    rate_limiter = request.app.state.rate_limiter
    allowed, rate_remaining = await rate_limiter.check(f"{client_ip}:search")
    if not allowed:
        from .exceptions import RateLimitedError
        from .metrics import METRICS

        METRICS.counter("search_calls_total", "Total search calls", ["status"]).inc(
            {"status": "rate_limited"}
        )
        raise RateLimitedError(
            detail=f"Per-client rate limit exceeded ({rate_limiter.limit}/{rate_limiter.window}s)"
        )

    max_searches = request.app.state.max_searches_per_request

    # ── Metrics ──────────────────────────────────────────────────
    from .metrics import METRICS

    METRICS.counter("search_calls_total", "Total search calls", ["status"]).inc(
        {"status": "allowed"}
    )

    if body.stream:
        from fastapi.responses import StreamingResponse

        async def event_stream() -> Any:
            from .research import run_answer_stream

            async for event in run_answer_stream(
                query=body.query,
                num_sources=body.num_sources,
                search_type=body.search_type,
                retrieval_mode=body.retrieval_mode,
                searxng_url=request.app.state.searxng_url,
                scraper_url=request.app.state.scraper_url,
                semantic_url=request.app.state.semantic_url,
                llm_base_url=request.app.state.llm_base_url,
                llm_api_key=request.app.state.llm_api_key,
                llm_model=request.app.state.llm_model,
                requested_model=body.model if body.model != "default" else None,
                max_searches_per_request=max_searches,
            ):
                if event["type"] == "sources_pending":
                    import json

                    yield f"data: {json.dumps({'type': 'sources_pending', 'sources': event['sources']})}\n\n"
                elif event["type"] == "sources":
                    import json

                    yield f"data: {json.dumps({'type': 'sources', 'sources': event['sources']})}\n\n"
                elif event["type"] == "token":
                    import json

                    yield f"data: {json.dumps({'type': 'token', 'content': event['content']})}\n\n"
                elif event["type"] == "done":
                    import json

                    yield f"data: {json.dumps({'type': 'done', 'answer': event['answer'], 'citations': event['citations'], 'latency_ms': event['latency_ms']})}\n\n"
                elif event["type"] == "error":
                    import json

                    yield f"data: {json.dumps({'type': 'error', 'content': event['content']})}\n\n"
            yield "data: [DONE]\n\n"

        headers = {
            "X-Search-Budget": f"{max_searches}/{max_searches}",
            "X-Search-Rate-Remaining": f"{rate_remaining}/{rate_limiter.limit}",
        }
        return StreamingResponse(  # type: ignore[return-value]
            event_stream(), media_type="text/event-stream", headers=headers
        )

    # Sync path
    from .research import run_answer

    result = await run_answer(
        query=body.query,
        num_sources=body.num_sources,
        search_type=body.search_type,
        retrieval_mode=body.retrieval_mode,
        searxng_url=request.app.state.searxng_url,
        scraper_url=request.app.state.scraper_url,
        semantic_url=request.app.state.semantic_url,
        llm_base_url=request.app.state.llm_base_url,
        llm_api_key=request.app.state.llm_api_key,
        llm_model=request.app.state.llm_model,
        requested_model=body.model if body.model != "default" else None,
        max_searches_per_request=max_searches,
    )
    response.headers["X-Search-Budget"] = f"{max_searches}/{max_searches}"
    response.headers["X-Search-Rate-Remaining"] = (
        f"{rate_remaining}/{rate_limiter.limit}"
    )
    return AnswerResponse(
        success=True,
        answer=result["answer"],
        sources=[Source(**s) for s in result["sources"]],
        citations=[Citation(**c) for c in result["citations"]],
        search_type=result["search_type"],
        latency_ms=result["latency_ms"],
    )


@router.post("/v2/enrich", response_model=EnrichResponse)
async def enrich(request: Request, body: EnrichRequest):
    """Enrich a list of entities with web-sourced structured data.

    Each item is processed independently: search → scrape → LLM extraction.
    Returns ``{value, source}`` pairs for each requested field.
    """
    import time

    from .research import run_enrich_pipeline

    start = time.monotonic()
    result = await run_enrich_pipeline(
        items=body.items,
        fields=body.fields,
        source_hint=body.source_hint,
        effort=body.effort,
        searxng_url=request.app.state.searxng_url,
        scraper_url=request.app.state.scraper_url,
        llm_base_url=request.app.state.llm_base_url,
        llm_api_key=request.app.state.llm_api_key,
        llm_model=request.app.state.llm_model,
    )
    latency = (time.monotonic() - start) * 1000
    return EnrichResponse(
        data=result,
        latency_ms=round(latency, 1),
        items_enriched=len(body.items),
        fields_per_item=len(body.fields),
    )


@router.post("/v2/extract", response_model=ExtractCreateResponse)
async def create_extract(
    request: Request, body: ExtractRequest
) -> ExtractCreateResponse:
    """Extract structured data from provided URLs."""
    store: JobStore = request.app.state.job_store
    job_id = store.create_job(
        kind="extract", payload=body.model_dump(exclude_none=True, by_alias=True)
    )

    from .worker import _process_extract_async

    request.app.state.task_tracker.create_background_task(
        _process_extract_async(
            job_id=job_id,
            urls=body.urls,
            prompt=body.prompt,
            schema_=body.schema_,
            llm_base_url=request.app.state.llm_base_url,
            llm_api_key=request.app.state.llm_api_key,
            llm_model=request.app.state.llm_model,
            scraper_url=request.app.state.scraper_url,
            webhook_config=body.webhook,
        )
    )
    return ExtractCreateResponse(id=job_id)


@router.get("/v2/extract/{job_id}", response_model=ExtractStatusResponse)
async def get_extract_status(request: Request, job_id: str) -> ExtractStatusResponse:
    """Get extract job status and results."""
    store: JobStore = request.app.state.job_store
    job = store.get_job(job_id)
    if job is None:
        raise NotFoundError(detail="Job not found", details={"job_id": job_id})
    return ExtractStatusResponse(
        success=True,
        status=job.get("status", "processing"),
        data=job.get("data"),
        error=job.get("error"),
        expires_at=job.get("completed_at") or job.get("created_at"),
    )


# ----- Map -----


@router.post("/v2/map", response_model=MapResponse)
async def map_site(request: Request, body: MapRequest) -> MapResponse:
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
            resp = await client.get(body.url)
            if resp.status_code != 200:
                raise UpstreamError(detail=f"Site returned HTTP {resp.status_code}")

            # Use shared LinkExtractor instead of inline BeautifulSoup parsing
            from urllib.parse import urlparse

            from .link_extractor import extract_links, filter_links

            all_links = extract_links(resp.text, body.url)

            # Filter links by domain scope (default: same-origin only)
            base_domain = (urlparse(body.url).hostname or "").lower()
            filtered = filter_links(
                all_links,
                base_domain=base_domain,
                allow_subdomains=body.allow_subdomains,
                allow_external_links=body.allow_external_links,
            )

            # Apply limit (truncates AFTER filtering)
            result = filtered[: body.limit]

            # Apply search filter (case-insensitive substring match)
            if body.search:
                result = [
                    link for link in result if body.search.lower() in link.lower()
                ]

            return MapResponse(links=result)
    except Exception as e:
        logger.error("Map failed for %s: %s", body.url, e)
        raise UpstreamError(detail=str(e)) from e


# ----- Browser Sessions -----

BROWSER_SVC_URL = "http://browser-svc:8012"


async def _browser_proxy(
    path: str, method: str = "POST", json_data: dict[str, Any] | None = None
) -> Any:
    """Proxy a request to the browser service."""
    async with httpx.AsyncClient(timeout=120) as client:
        if method == "GET":
            resp = await client.get(f"{BROWSER_SVC_URL}{path}")
        elif method == "DELETE":
            resp = await client.delete(f"{BROWSER_SVC_URL}{path}")
        else:
            resp = await client.post(f"{BROWSER_SVC_URL}{path}", json=json_data or {})
        try:
            return resp.json()
        except Exception:
            return {"success": False, "error": resp.text[:200]}


@router.post("/v2/browser", response_model=BrowserCreateResponse)
async def create_browser(body: BrowserCreateRequest) -> BrowserCreateResponse:
    result = await _browser_proxy("/browsers", json_data=body.model_dump())
    if not result.get("success"):
        raise BrowserError(
            detail=result.get("detail", result.get("error", "Browser service error"))
        )
    return BrowserCreateResponse(id=result["id"])


@router.post("/v2/browser/{session_id}/execute", response_model=BrowserExecuteResponse)
async def execute_browser(
    session_id: str, body: BrowserExecuteRequest
) -> BrowserExecuteResponse:
    result = await _browser_proxy(
        f"/browsers/{session_id}/execute", json_data=body.model_dump()
    )
    if not result.get("success"):
        raise BrowserError(detail=result.get("error", "Browser execution failed"))
    return BrowserExecuteResponse(success=True, result=result.get("result"))


@router.get("/v2/browser", response_model=BrowserListResponse)
async def list_browsers() -> BrowserListResponse:
    result = await _browser_proxy("/browsers", method="GET")
    return BrowserListResponse(sessions=result.get("sessions", []))


@router.delete("/v2/browser/{session_id}", response_model=BrowserDeleteResponse)
async def destroy_browser(session_id: str) -> BrowserDeleteResponse:
    result = await _browser_proxy(f"/browsers/{session_id}", method="DELETE")
    if not result.get("success"):
        raise NotFoundError(
            detail="Session not found", details={"session_id": session_id}
        )
    return BrowserDeleteResponse(id=session_id)


# ----- Monitor -----


@router.post("/v2/monitor", response_model=MonitorResponse)
async def create_monitor(body: MonitorCreateRequest) -> MonitorResponse:
    import uuid
    from datetime import datetime

    monitor_id = str(uuid.uuid4())
    now = datetime.now(UTC).isoformat()

    if body.monitor_type == "search":
        config = {
            "monitor_type": "search",
            "search_config": body.search_config.model_dump()
            if body.search_config
            else {},
            "schedule": body.schedule,
            "webhook": body.webhook,
            "created_at": now,
        }
        save_monitor(monitor_id, config)
        return MonitorResponse(
            id=monitor_id,
            monitor_type="search",
            search_config=config["search_config"],  # type: ignore[arg-type]
            schedule=body.schedule,
            webhook=body.webhook,
            created_at=now,
        )
    else:
        # Scrape type (default)
        config = {
            "monitor_type": "scrape",
            "url": body.url,
            "schedule": body.schedule,
            "webhook": body.webhook,
            "created_at": now,
            "last_content": "",
        }
        save_monitor(monitor_id, config)
        return MonitorResponse(
            id=monitor_id,
            monitor_type="scrape",
            url=body.url,
            schedule=body.schedule,
            webhook=body.webhook,
            created_at=now,
        )


@router.get("/v2/monitor", response_model=MonitorListResponse)
async def list_monitors() -> MonitorListResponse:
    monitors = get_all_monitors()
    items = []
    for mid, cfg in monitors.items():
        mt = cfg.get("monitor_type", "scrape")
        if mt == "search":
            items.append(
                MonitorResponse(
                    id=mid,
                    monitor_type="search",
                    search_config=cfg.get("search_config"),  # type: ignore[arg-type]
                    schedule=cfg.get("schedule", ""),
                    webhook=cfg.get("webhook"),
                    last_checked=cfg.get("last_checked"),
                    last_result=cfg.get("last_result"),
                    created_at=cfg.get("created_at", ""),
                )
            )
        else:
            items.append(
                MonitorResponse(
                    id=mid,
                    monitor_type="scrape",
                    url=cfg.get("url", ""),
                    schedule=cfg.get("schedule", ""),
                    webhook=cfg.get("webhook"),
                    last_checked=cfg.get("last_checked"),
                    last_result=cfg.get("last_result"),
                    created_at=cfg.get("created_at", ""),
                )
            )
    return MonitorListResponse(monitors=items)


@router.get("/v2/monitor/{monitor_id}", response_model=MonitorResponse)
async def get_monitor_status(monitor_id: str) -> MonitorResponse:
    cfg = get_monitor(monitor_id)
    if cfg is None:
        raise NotFoundError(
            detail="Monitor not found", details={"monitor_id": monitor_id}
        )
    mt = cfg.get("monitor_type", "scrape")
    if mt == "search":
        return MonitorResponse(
            id=monitor_id,
            monitor_type="search",
            search_config=cfg.get("search_config"),  # type: ignore[arg-type]
            schedule=cfg.get("schedule", ""),
            webhook=cfg.get("webhook"),
            last_checked=cfg.get("last_checked"),
            last_result=cfg.get("last_result"),
            created_at=cfg.get("created_at", ""),
        )
    else:
        return MonitorResponse(
            id=monitor_id,
            monitor_type="scrape",
            url=cfg.get("url", ""),
            schedule=cfg.get("schedule", ""),
            webhook=cfg.get("webhook"),
            last_checked=cfg.get("last_checked"),
            last_result=cfg.get("last_result"),
            created_at=cfg.get("created_at", ""),
        )


@router.patch("/v2/monitor/{monitor_id}", response_model=MonitorResponse)
async def update_monitor(
    monitor_id: str, body: MonitorUpdateRequest
) -> MonitorResponse:
    cfg = get_monitor(monitor_id)
    if cfg is None:
        raise NotFoundError(
            detail="Monitor not found", details={"monitor_id": monitor_id}
        )
    if body.url is not None:
        cfg["url"] = body.url
    if body.schedule is not None:
        cfg["schedule"] = body.schedule
    if body.webhook is not None:
        cfg["webhook"] = body.webhook
    if body.search_config is not None:
        cfg["search_config"] = body.search_config.model_dump()
    save_monitor(monitor_id, cfg)
    mt = cfg.get("monitor_type", "scrape")
    if mt == "search":
        return MonitorResponse(
            id=monitor_id,
            monitor_type="search",
            search_config=cfg.get("search_config"),  # type: ignore[arg-type]
            schedule=cfg.get("schedule", ""),
            webhook=cfg.get("webhook"),
            last_checked=cfg.get("last_checked"),
            last_result=cfg.get("last_result"),
            created_at=cfg.get("created_at", ""),
        )
    else:
        return MonitorResponse(
            id=monitor_id,
            monitor_type="scrape",
            url=cfg.get("url", ""),
            schedule=cfg.get("schedule", ""),
            webhook=cfg.get("webhook"),
            last_checked=cfg.get("last_checked"),
            last_result=cfg.get("last_result"),
            created_at=cfg.get("created_at", ""),
        )


@router.delete("/v2/monitor/{monitor_id}", response_model=MonitorDeleteResponse)
async def delete_monitor_route(monitor_id: str) -> MonitorDeleteResponse:
    cfg = get_monitor(monitor_id)
    if cfg is None:
        raise NotFoundError(
            detail="Monitor not found", details={"monitor_id": monitor_id}
        )
    # Clean up search monitor seen set
    if cfg.get("monitor_type") == "search":
        from redis import Redis

        r = Redis.from_url("redis://valkey:6379/0", decode_responses=True)
        r.delete(f"monitor:{monitor_id}:seen")
    delete_monitor(monitor_id)
    return MonitorDeleteResponse(success=True)


@router.post("/v2/monitor/{monitor_id}/run", response_model=MonitorResponse)
async def run_monitor_check(request: Request, monitor_id: str) -> MonitorResponse:
    """Manually trigger a monitor check immediately.

    Runs the check regardless of the cron schedule and returns
    the updated monitor status including any diff or new results.
    """
    store: JobStore = request.app.state.job_store
    scraper_url = request.app.state.scraper_url

    try:
        result = await run_monitor(monitor_id, scraper_url=scraper_url)
    except ValueError:
        raise NotFoundError(
            detail="Monitor not found", details={"monitor_id": monitor_id}
        )

    cfg = get_monitor(monitor_id)
    if cfg is None:
        raise NotFoundError(
            detail="Monitor not found", details={"monitor_id": monitor_id}
        )

    search_config = cfg.get("search_config")
    if isinstance(search_config, str):
        import json

        search_config = json.loads(search_config)

    return MonitorResponse(
        id=monitor_id,
        monitor_type=cfg.get("monitor_type", "scrape"),
        url=cfg.get("url"),
        search_config=search_config,
        schedule=cfg.get("schedule", ""),
        webhook=cfg.get("webhook"),
        last_checked=cfg.get("last_checked"),
        last_result=cfg.get("last_result"),
        created_at=cfg.get("created_at", ""),
    )


@router.get(
    "/v2/monitor/{monitor_id}/checks", response_model=MonitorCheckListResponse
)
async def list_monitor_checks(
    monitor_id: str, limit: int = 50
) -> MonitorCheckListResponse:
    """List check history for a monitor, newest first."""
    cfg = get_monitor(monitor_id)
    if cfg is None:
        raise NotFoundError(
            detail="Monitor not found", details={"monitor_id": monitor_id}
        )
    checks = get_monitor_checks(monitor_id, limit=limit)
    items = [MonitorCheckItem(**c) for c in checks]
    return MonitorCheckListResponse(data=items, total=len(items))


@router.get(
    "/v2/monitor/{monitor_id}/checks/{check_index}",
    response_model=MonitorCheckItem,
)
async def get_monitor_check_detail(
    monitor_id: str, check_index: int
) -> MonitorCheckItem:
    """Get a specific monitor check by its 0-based index.

    Index 0 is the most recent check.
    """
    cfg = get_monitor(monitor_id)
    if cfg is None:
        raise NotFoundError(
            detail="Monitor not found", details={"monitor_id": monitor_id}
        )
    check = get_monitor_check(monitor_id, check_index)
    if check is None:
        raise NotFoundError(
            detail="Check not found",
            details={"monitor_id": monitor_id, "check_index": check_index},
        )
    return MonitorCheckItem(**check)


# ----- Parse -----

PARSE_SVC_URL = "http://parse-svc:8013"
PARSE_UPLOAD_TTL = 3600  # 1 hour TTL for upload temp storage


@router.post("/v2/parse/upload-url", response_model=ParseUploadUrlResponse)
async def request_parse_upload_url(request: Request) -> ParseUploadUrlResponse:
    """Request a pre-signed upload URL for large file parsing.

    Returns an upload_id and upload_url. The client PUTs the file to the
    upload_url, then calls POST /v2/parse with upload_id in the form.
    """
    import uuid

    from redis import Redis

    upload_id = str(uuid.uuid4())
    r = Redis.from_url("redis://valkey:6379/0", decode_responses=False)
    r.set(f"parse:upload:{upload_id}", b"pending", ex=PARSE_UPLOAD_TTL)

    scheme = request.url.scheme
    host = request.url.netloc
    upload_url = f"{scheme}://{host}/v2/parse/upload/{upload_id}"

    return ParseUploadUrlResponse(upload_id=upload_id, upload_url=upload_url)


@router.put("/v2/parse/upload/{upload_id}")
async def upload_parse_file(upload_id: str, request: Request) -> dict[str, Any]:
    """Upload file bytes for a previously requested upload_id."""
    from redis import Redis

    r = Redis.from_url("redis://valkey:6379/0", decode_responses=False)
    meta = r.get(f"parse:upload:{upload_id}")
    if meta is None:
        raise NotFoundError(
            detail="Upload ID not found or expired",
            details={"upload_id": upload_id},
        )

    raw_body = await request.body()
    if not raw_body:
        raise InvalidRequestError(detail="Empty body — no file data received")

    r.set(f"parse:upload:{upload_id}:data", raw_body, ex=PARSE_UPLOAD_TTL)
    r.set(f"parse:upload:{upload_id}", b"uploaded", ex=PARSE_UPLOAD_TTL)
    return {"success": True, "upload_id": upload_id}


@router.post("/v2/parse", response_model=ParseResponse)
async def parse_file(request: Request) -> Any:
    """Upload a file and get its content as markdown.

    Supports two modes:
    - Direct: multipart form with 'file' field (small files)
    - Two-step: form field 'upload_id' referencing a pre-uploaded file
    """
    import httpx
    from redis import Redis

    form = await request.form()
    r = Redis.from_url("redis://valkey:6379/0", decode_responses=False)

    upload_id = form.get("upload_id")
    if upload_id is not None:
        # Two-step mode: retrieve pre-uploaded file
        upload_id_str = str(upload_id)
        content = r.get(f"parse:upload:{upload_id_str}:data")
        if content is None:
            raise NotFoundError(
                detail="Upload ID not found or no data uploaded",
                details={"upload_id": upload_id_str},
            )
        filename = "uploaded_file"
        content_type = "application/octet-stream"
        # Clean up temp keys
        r.delete(f"parse:upload:{upload_id_str}", f"parse:upload:{upload_id_str}:data")
    elif "file" not in form:
        raise InvalidRequestError(
            detail="No file provided. Use multipart form with 'file' field or 'upload_id' for two-step upload."
        )
    else:
        upload = form["file"]  # type: ignore[union-attr]
        content = await upload.read()  # type: ignore[union-attr]
        filename = upload.filename or "file"  # type: ignore[union-attr]
        content_type = upload.content_type or "application/octet-stream"  # type: ignore[union-attr]

    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            f"{PARSE_SVC_URL}/parse",
            files={
                "file": (filename, content, content_type)
            },
        )
        try:
            return resp.json()
        except Exception:
            raise UpstreamError(
                detail=f"Parse service error: {resp.text[:200]}"
            ) from None


# ----- LLMs.txt Generator -----


@router.post("/v2/generate-llmstxt", response_model=LLMsTextCreateResponse)
async def create_llmstxt(
    request: Request, body: LLMsTextRequest
) -> LLMsTextCreateResponse:
    """Generate an llms.txt file for a website."""
    store: JobStore = request.app.state.job_store
    job_id = store.create_job(kind="llmstxt", payload=body.model_dump())

    from .worker import _process_llmstxt_async

    request.app.state.task_tracker.create_background_task(
        _process_llmstxt_async(
            job_id=job_id,
            url=body.url,
            max_pages=body.max_pages,
            scraper_url=request.app.state.scraper_url,
            webhook_config=body.webhook,
        )
    )
    return LLMsTextCreateResponse(id=job_id)


@router.get("/v2/generate-llmstxt/{job_id}", response_model=LLMsTextStatusResponse)
async def get_llmstxt_status(request: Request, job_id: str) -> LLMsTextStatusResponse:
    """Get llms.txt generation job status and results."""
    store: JobStore = request.app.state.job_store
    job = store.get_job(job_id)
    if job is None:
        raise NotFoundError(detail="Job not found", details={"job_id": job_id})
    return LLMsTextStatusResponse(
        success=True,
        status=job.get("status", "processing"),
        data=job.get("data"),
        error=job.get("error"),
        expires_at=job.get("completed_at") or job.get("created_at"),
    )


async def _index_scrape(url: str, title: str, content: str, request: Request) -> None:
    """Fire-and-forget index a scraped page in the vector index."""
    semantic = None
    try:
        from .semantic_client import SemanticClient

        semantic = SemanticClient(request.app.state.semantic_url)
        await semantic.index_page(url, title, content[:2000])
    except Exception:
        logger.warning(
            "Semantic indexing failed for %s — page will not appear in vector search",
            url,
            exc_info=True,
        )
    finally:
        if semantic is not None:
            await semantic.close()
