"""Scrape route handlers — single-page scraping and batch scrape."""

import logging
from datetime import datetime as _dt

from fastapi import APIRouter, Request

from ..exceptions import CaptchaError, NotFoundError, ScrapeError
from ..models import (
    AgentCancelResponse,
    BatchScrapeErrorsResponse,
    BatchScrapeRequest,
    BatchScrapeStatusResponse,
    CrawlCreateResponse,
    CrawlErrorItem,
    ImageData,
    ScrapeData,
    ScrapeRequest,
    ScrapeResponse,
)
from ..store import JobStore
from ._helpers import _index_scrape

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/v2/scrape", response_model=ScrapeResponse)
async def scrape(request: Request, body: ScrapeRequest) -> ScrapeResponse:
    scraper = request.app.state.scraper_client
    scrape_opts = {"formats": body.formats}
    result = await scraper.scrape(body.url, scrape_options=scrape_opts)
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
                images=[ImageData(**img) for img in scraper_data.get("images", [])]
                if scraper_data.get("images")
                else None,
            ),
        )
    if result.get("error_code") == "CAPTCHA_UNRESOLVED":
        raise CaptchaError(
            detail=result.get("error", "CAPTCHA challenge could not be resolved"),
            details=result.get("details") or result.get("barrier"),
        )
    raise ScrapeError(detail=result.get("error", "Scrape failed"))


@router.post("/v2/batch/scrape", response_model=CrawlCreateResponse)
async def create_batch_scrape(
    request: Request, body: BatchScrapeRequest
) -> CrawlCreateResponse:
    store: JobStore = request.app.state.job_store
    job_id = store.create_job(kind="batch_scrape", payload=body.model_dump())

    from ..worker import _process_batch_scrape_async

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
