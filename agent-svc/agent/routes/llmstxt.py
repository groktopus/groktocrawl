"""LLMs.txt route handlers — generate llms.txt files for websites."""

import logging

from fastapi import APIRouter, Request

from ..exceptions import NotFoundError
from ..models import (
    LLMsTextCreateResponse,
    LLMsTextRequest,
    LLMsTextStatusResponse,
)
from ..store import JobStore

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/v2/generate-llmstxt", response_model=LLMsTextCreateResponse)
async def create_llmstxt(
    request: Request, body: LLMsTextRequest
) -> LLMsTextCreateResponse:
    """Generate an llms.txt file for a website."""
    store: JobStore = request.app.state.job_store
    job_id = store.create_job(kind="llmstxt", payload=body.model_dump())

    from ..job_execution import submit_job
    from ..worker import _process_llmstxt_async

    submit_job(
        request,
        job_id,
        _process_llmstxt_async(
            job_id=job_id,
            url=body.url,
            max_pages=body.max_pages,
            scraper_url=request.app.state.scraper_url,
            webhook_config=body.webhook,
        ),
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
        error_code=job.get("error_code"),
        expires_at=job.get("completed_at") or job.get("created_at"),
    )
