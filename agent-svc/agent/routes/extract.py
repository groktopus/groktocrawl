"""Extract route handlers — extract structured data from URLs."""

import logging

from fastapi import APIRouter, Request

from ..exceptions import NotFoundError
from ..models import (
    ExtractCreateResponse,
    ExtractRequest,
    ExtractStatusResponse,
)
from ..store import JobStore

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/v2/extract", response_model=ExtractCreateResponse)
async def create_extract(
    request: Request, body: ExtractRequest
) -> ExtractCreateResponse:
    """Extract structured data from provided URLs."""
    store: JobStore = request.app.state.job_store
    job_id = store.create_job(
        kind="extract", payload=body.model_dump(exclude_none=True, by_alias=True)
    )

    from ..job_execution import submit_job
    from ..worker import _process_extract_async

    submit_job(
        request,
        job_id,
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
        ),
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
        error_code=job.get("error_code"),
        expires_at=job.get("completed_at") or job.get("created_at"),
    )
