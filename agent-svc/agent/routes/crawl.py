"""Crawl route handlers — recursive website crawling with SSE streaming."""

import json
import logging
from datetime import datetime as _dt
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import StreamingResponse

from ..exceptions import NotFoundError, RateLimitedError
from ..metrics import METRICS
from ..models import (
    AgentCancelResponse,
    CrawlActiveItem,
    CrawlActiveResponse,
    CrawlCreateResponse,
    CrawlErrorItem,
    CrawlErrorsResponse,
    CrawlRequest,
    CrawlStatusResponse,
    ParamsPreviewRequest,
    ParamsPreviewResponse,
)
from ..store import JobStore
from ._helpers import _get_client_ip

logger = logging.getLogger(__name__)

router = APIRouter()


def _resolve_effective_max_pages(max_pages: int, limit: int | None) -> int:
    """Resolve ``limit`` against ``max_pages`` into one engine cap.

    Firecrawl-parity semantics: ``limit`` caps the number of pages a
    crawl processes, exactly like ``max_pages``; when both are set the
    stricter (smaller positive) bound wins. ``max_pages=0`` means
    unlimited, so a plain ``min()`` would let an unset ``max_pages``
    silently swallow a set ``limit`` — the accepted-but-ignored bug
    found during mission validation of #587/#588/#589.

    Returns 0 (= unlimited) only when neither parameter bounds the
    crawl.
    """
    candidates = [c for c in (max_pages, limit) if c is not None and c > 0]
    return min(candidates) if candidates else 0


@router.post("/v2/crawl", response_model=CrawlCreateResponse)
async def create_crawl(
    request: Request, body: CrawlRequest, response: Response
) -> CrawlCreateResponse:
    # ── Per-client rate limit check (VAL-CONC-047) ────────────
    client_ip = _get_client_ip(request)
    rate_limiter = request.app.state.rate_limiter
    allowed, rate_remaining = await rate_limiter.check(f"{client_ip}:crawl")
    if not allowed:
        retry_after = rate_limiter.retry_after_seconds()
        METRICS.counter("search_calls_total", "Total search calls", ["status"]).inc(
            {"status": "rate_limited"}
        )
        METRICS.counter(
            "rate_limited_admissions_total",
            "Admission requests rejected by per-client rate limit",
            ["operation", "bucket"],
        ).inc({"operation": "crawl", "bucket": "crawl"})
        raise RateLimitedError(
            detail=(
                f"Per-client rate limit exceeded "
                f"({rate_limiter.limit}/{rate_limiter.window}s) — retry in {retry_after}s"
            ),
            retry_after_seconds=retry_after,
            bucket="crawl",
            limit=rate_limiter.limit,
            remaining=0,
            reset_at=rate_limiter.reset_at_iso(),
        )

    response.headers["X-Crawl-Rate-Remaining"] = (
        f"{rate_remaining}/{rate_limiter.limit}"
    )

    # ── NL→params: derive from prompt, merge with explicit params ──
    include_paths = body.include_paths
    exclude_paths = body.exclude_paths
    max_depth = body.max_depth

    if body.prompt:
        from ..nl_params import derive_crawl_params, merge_params

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

    # Resolve limit vs max_pages conflict (stricter wins, per VAL-CRAWL-089).
    # max_pages=0 means unlimited, so limit must feed the resolver directly
    # — a plain min(max_pages, limit) would collapse to 0 and silently
    # ignore the requested page cap.
    effective_max_pages = _resolve_effective_max_pages(body.max_pages, body.limit)

    # ── NL→params: apply LLM-derived page cap ────────────────────
    # The merge above covers include/exclude paths and max_depth only;
    # derive_crawl_params() also returns ``max_pages`` (the NL→params
    # schema's name for the same Firecrawl ``limit`` knob), which was
    # previously computed and then silently dropped. Reuse the first
    # call's result — no extra LLM round-trip. Only honor a derived cap
    # when the caller did not set one explicitly.
    if (
        body.prompt
        and "limit" not in explicitly_set
        and "max_pages" not in explicitly_set
    ):
        derived_cap = llm_result.get("max_pages")
        if (
            isinstance(derived_cap, int)
            and derived_cap >= 1
            and (effective_max_pages == 0 or derived_cap < effective_max_pages)
        ):
            logger.info(
                "NL→params derived max_pages=%d applied to crawl %s",
                derived_cap,
                job_id,
            )
            effective_max_pages = derived_cap

    # ── Streaming path: run inline, return SSE ────────────
    if body.stream:
        from ..crawl_stream import crawl_event_stream
        from ..settings import load_settings as _load_crawl_settings

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
    from ..worker import _process_crawl_async

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
        ),
        job_id=job_id,
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
        outcome_summary=data.get("outcome_summary"),
        next=next_url,
        created_at=created_at,
        completed_at=completed_at,
        expires_at=job.get("expires_at"),
        duration=duration,
        retry_at=job.get("retry_at"),
        retry_attempt=job.get("retry_attempt"),
        retry_limit=job.get("retry_limit"),
        retryable=True if job.get("status") == "retry_scheduled" else None,
        retry_reason=job.get("retry_reason"),
    )


@router.delete("/v2/crawl/{job_id}", response_model=AgentCancelResponse)
async def cancel_crawl(request: Request, job_id: str) -> AgentCancelResponse:
    store: JobStore = request.app.state.job_store
    if not store.cancel_job(job_id):
        raise NotFoundError(
            detail="Job not found or already completed", details={"job_id": job_id}
        )
    request.app.state.task_tracker.cancel_job(job_id)
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

    async def event_stream() -> Any:
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
    from ..nl_params import derive_crawl_params

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
