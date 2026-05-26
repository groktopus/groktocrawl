"""Route handlers implementing the GroktoCrawl API surface.

Targets Firecrawl v2 API compatibility where possible.
"""

import logging
from typing import Any

from fastapi import APIRouter, Request, HTTPException
from redis import Redis
from rq import Queue
from urllib.parse import urlparse
from bs4 import BeautifulSoup
import httpx

from .models import (
    AgentRequest, AgentCreateResponse, AgentStatusResponse, AgentCancelResponse,
    ScrapeRequest, ScrapeResponse, ScrapeData,
    CrawlRequest, CrawlCreateResponse, CrawlStatusResponse,
    BatchScrapeRequest,
    SearchRequest, SearchResponse, SearchResult,
    MapRequest, MapResponse,
    ExtractRequest, ExtractCreateResponse, ExtractStatusResponse,
    BrowserCreateRequest, BrowserCreateResponse,
    BrowserExecuteRequest, BrowserExecuteResponse,
    BrowserListResponse, BrowserDeleteResponse,
    MonitorCreateRequest, MonitorUpdateRequest, MonitorResponse,
    MonitorListResponse, MonitorDeleteResponse,
    ParseResponse,
    LLMsTextRequest, LLMsTextCreateResponse, LLMsTextStatusResponse,
    ActivityResponse, ActivityItem,
)
from .store import JobStore
from .monitor import get_all_monitors, get_monitor, save_monitor, delete_monitor

logger = logging.getLogger(__name__)

router = APIRouter()


def _enqueue(queue: Queue, func: str, **kwargs: Any) -> None:
    queue.enqueue(func, **kwargs)


@router.get("/health")
async def health():
    return {"status": "ok"}


@router.get("/v2/activity", response_model=ActivityResponse)
async def list_activity(request: Request):
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
        items.append(ActivityItem(
            id=job["id"],
            kind=job.get("kind", "unknown"),
            status=job.get("status", "processing"),
            url=url,
            created_at=job.get("created_at", ""),
            completed_at=job.get("completed_at"),
        ))
    return ActivityResponse(data=items)


@router.post("/v2/scrape", response_model=ScrapeResponse)
async def scrape(request: Request, body: ScrapeRequest):
    scraper = request.app.state.scraper_client
    result = await scraper.scrape(body.url)
    if result.get("success"):
        return ScrapeResponse(
            success=True,
            data=ScrapeData(
                markdown=result["data"].get("markdown", ""),
                metadata={"source": result["data"].get("source", "unknown")},
            ),
        )
    return ScrapeResponse(success=False, error=result.get("error", "Scrape failed"))


@router.post("/v2/agent", response_model=AgentCreateResponse)
async def create_agent(request: Request, body: AgentRequest):
    store: JobStore = request.app.state.job_store
    job_id = store.create_job(kind="agent", payload=body.model_dump(exclude_none=True, by_alias=True))

    # Process inline (synchronous) for MVP — no RQ worker needed.
    # A separate worker container can be added later for proper async.
    import asyncio
    from .worker import _process_agent_async
    asyncio.create_task(
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
    return AgentCreateResponse(id=job_id)


@router.get("/v2/agent/{job_id}", response_model=AgentStatusResponse)
async def get_agent_status(request: Request, job_id: str):
    store: JobStore = request.app.state.job_store
    job = store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return AgentStatusResponse(
        success=True,
        status=job.get("status", "processing"),
        data=job.get("data"),
        error=job.get("error"),
        expires_at=job.get("completed_at") or job.get("created_at"),
    )


@router.delete("/v2/agent/{job_id}", response_model=AgentCancelResponse)
async def cancel_agent(request: Request, job_id: str):
    store: JobStore = request.app.state.job_store
    if not store.cancel_job(job_id):
        raise HTTPException(status_code=404, detail="Job not found or already completed")
    return AgentCancelResponse(success=True)


@router.post("/v2/crawl", response_model=CrawlCreateResponse)
async def create_crawl(request: Request, body: CrawlRequest):
    store: JobStore = request.app.state.job_store
    job_id = store.create_job(kind="crawl", payload=body.model_dump())
    import asyncio
    from .worker import _process_crawl_async
    asyncio.create_task(_process_crawl_async(job_id=job_id, url=body.url, max_pages=body.max_pages, max_depth=body.max_depth, scraper_url=request.app.state.scraper_url, webhook_config=body.webhook))
    return CrawlCreateResponse(id=job_id)


@router.get("/v2/crawl/{job_id}", response_model=CrawlStatusResponse)
async def get_crawl_status(request: Request, job_id: str):
    store: JobStore = request.app.state.job_store
    job = store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    data = job.get("data") or {}
    return CrawlStatusResponse(status=job.get("status", "processing"), completed=data.get("completed", 0), total=data.get("total", 0), data=data.get("pages"), error=job.get("error"))


@router.delete("/v2/crawl/{job_id}", response_model=AgentCancelResponse)
async def cancel_crawl(request: Request, job_id: str):
    store: JobStore = request.app.state.job_store
    if not store.cancel_job(job_id):
        raise HTTPException(status_code=404, detail="Job not found or already completed")
    return AgentCancelResponse(success=True)


@router.post("/v2/batch/scrape", response_model=CrawlCreateResponse)
async def create_batch_scrape(request: Request, body: BatchScrapeRequest):
    store: JobStore = request.app.state.job_store
    job_id = store.create_job(kind="batch_scrape", payload=body.model_dump())
    import asyncio
    from .worker import _process_batch_scrape_async
    asyncio.create_task(_process_batch_scrape_async(job_id=job_id, urls=body.urls, scraper_url=request.app.state.scraper_url, webhook_config=body.webhook))
    return CrawlCreateResponse(id=job_id)


@router.post("/v2/search", response_model=SearchResponse)
async def search(request: Request, body: SearchRequest):
    from .searxng_client import SearXNGClient

    searxng = SearXNGClient(request.app.state.searxng_url)
    try:
        results = await searxng.search(body.query, limit=body.limit)
        search_results = [
            SearchResult(url=r["url"], title=r["title"], description=r.get("description", ""))
            for r in results
        ]
        return SearchResponse(data={"web": search_results, "images": [], "news": []})
    finally:
        await searxng.close()


@router.post("/v2/extract", response_model=ExtractCreateResponse)
async def create_extract(request: Request, body: ExtractRequest):
    """Extract structured data from provided URLs."""
    store: JobStore = request.app.state.job_store
    job_id = store.create_job(kind="extract", payload=body.model_dump(exclude_none=True, by_alias=True))
    import asyncio
    from .worker import _process_extract_async
    asyncio.create_task(
        _process_extract_async(
            job_id=job_id, urls=body.urls, prompt=body.prompt, schema_=body.schema_,
            llm_base_url=request.app.state.llm_base_url, llm_api_key=request.app.state.llm_api_key,
            llm_model=request.app.state.llm_model, scraper_url=request.app.state.scraper_url,
            webhook_config=body.webhook,
        )
    )
    return ExtractCreateResponse(id=job_id)


@router.get("/v2/extract/{job_id}", response_model=ExtractStatusResponse)
async def get_extract_status(request: Request, job_id: str):
    """Get extract job status and results."""
    store: JobStore = request.app.state.job_store
    job = store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return ExtractStatusResponse(
        success=True,
        status=job.get("status", "processing"),
        data=job.get("data"),
        error=job.get("error"),
        expires_at=job.get("completed_at") or job.get("created_at"),
    )


# ----- Map -----

@router.post("/v2/map", response_model=MapResponse)
async def map_site(request: Request, body: MapRequest):
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
            resp = await client.get(body.url)
            if resp.status_code != 200:
                return MapResponse(success=False, links=[])
            soup = BeautifulSoup(resp.text, "html.parser")
            links: list[str] = []
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if href.startswith("/"):
                    parsed = urlparse(body.url)
                    href = f"{parsed.scheme}://{parsed.netloc}{href}"
                if href.startswith(body.url.rstrip("/")) or href.startswith(f"{urlparse(body.url).scheme}://{urlparse(body.url).netloc}"):
                    if href not in links:
                        links.append(href)
                        if len(links) >= body.limit:
                            break
            if body.search:
                links = [l for l in links if body.search.lower() in l.lower()]
            return MapResponse(links=links)
    except Exception as e:
        logger.error("Map failed for %s: %s", body.url, e)
        return MapResponse(success=False, links=[])


# ----- Browser Sessions -----

BROWSER_SVC_URL = "http://browser-svc:8012"


async def _browser_proxy(path: str, method: str = "POST", json_data: dict | None = None) -> dict:
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
async def create_browser(body: BrowserCreateRequest):
    result = await _browser_proxy("/browsers", json_data=body.model_dump())
    if not result.get("success"):
        raise HTTPException(status_code=502, detail=result.get("detail", result.get("error", "Browser service error")))
    return BrowserCreateResponse(id=result["id"])


@router.post("/v2/browser/{session_id}/execute", response_model=BrowserExecuteResponse)
async def execute_browser(session_id: str, body: BrowserExecuteRequest):
    result = await _browser_proxy(f"/browsers/{session_id}/execute", json_data=body.model_dump())
    return BrowserExecuteResponse(success=result.get("success", False), result=result.get("result"), error=result.get("error"))


@router.get("/v2/browser", response_model=BrowserListResponse)
async def list_browsers():
    result = await _browser_proxy("/browsers", method="GET")
    return BrowserListResponse(sessions=result.get("sessions", []))


@router.delete("/v2/browser/{session_id}", response_model=BrowserDeleteResponse)
async def destroy_browser(session_id: str):
    result = await _browser_proxy(f"/browsers/{session_id}", method="DELETE")
    if not result.get("success"):
        raise HTTPException(status_code=404, detail="Session not found")
    return BrowserDeleteResponse(id=session_id)


# ----- Monitor -----

@router.post("/v2/monitor", response_model=MonitorResponse)
async def create_monitor(body: MonitorCreateRequest):
    import uuid
    from datetime import datetime, timezone
    monitor_id = str(uuid.uuid4())
    config = {
        "url": body.url,
        "schedule": body.schedule,
        "webhook": body.webhook,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "last_content": "",
    }
    save_monitor(monitor_id, config)
    return MonitorResponse(
        id=monitor_id, url=body.url, schedule=body.schedule,
        webhook=body.webhook, created_at=config["created_at"],
    )


@router.get("/v2/monitor", response_model=MonitorListResponse)
async def list_monitors():
    monitors = get_all_monitors()
    items = []
    for mid, cfg in monitors.items():
        items.append(MonitorResponse(
            id=mid, url=cfg.get("url", ""), schedule=cfg.get("schedule", ""),
            webhook=cfg.get("webhook"), last_checked=cfg.get("last_checked"),
            last_result=cfg.get("last_result"), created_at=cfg.get("created_at", ""),
        ))
    return MonitorListResponse(monitors=items)


@router.get("/v2/monitor/{monitor_id}", response_model=MonitorResponse)
async def get_monitor_status(monitor_id: str):
    cfg = get_monitor(monitor_id)
    if cfg is None:
        raise HTTPException(status_code=404, detail="Monitor not found")
    return MonitorResponse(
        id=monitor_id, url=cfg.get("url", ""), schedule=cfg.get("schedule", ""),
        webhook=cfg.get("webhook"), last_checked=cfg.get("last_checked"),
        last_result=cfg.get("last_result"), created_at=cfg.get("created_at", ""),
    )


@router.patch("/v2/monitor/{monitor_id}", response_model=MonitorResponse)
async def update_monitor(monitor_id: str, body: MonitorUpdateRequest):
    cfg = get_monitor(monitor_id)
    if cfg is None:
        raise HTTPException(status_code=404, detail="Monitor not found")
    if body.url is not None:
        cfg["url"] = body.url
    if body.schedule is not None:
        cfg["schedule"] = body.schedule
    if body.webhook is not None:
        cfg["webhook"] = body.webhook
    save_monitor(monitor_id, cfg)
    return MonitorResponse(
        id=monitor_id, url=cfg.get("url", ""), schedule=cfg.get("schedule", ""),
        webhook=cfg.get("webhook"), last_checked=cfg.get("last_checked"),
        last_result=cfg.get("last_result"), created_at=cfg.get("created_at", ""),
    )


@router.delete("/v2/monitor/{monitor_id}", response_model=MonitorDeleteResponse)
async def delete_monitor_route(monitor_id: str):
    cfg = get_monitor(monitor_id)
    if cfg is None:
        raise HTTPException(status_code=404, detail="Monitor not found")
    delete_monitor(monitor_id)
    return MonitorDeleteResponse(success=True)


# ----- Parse -----

PARSE_SVC_URL = "http://parse-svc:8013"


@router.post("/v2/parse", response_model=ParseResponse)
async def parse_file(request: Request):
    """Upload a file and get its content as markdown."""
    import httpx

    form = await request.form()
    if "file" not in form:
        raise HTTPException(status_code=400, detail="No file provided. Use multipart form with 'file' field.")

    upload = form["file"]
    content = await upload.read()

    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            f"{PARSE_SVC_URL}/parse",
            files={"file": (upload.filename or "file", content, upload.content_type or "application/octet-stream")},
        )
        try:
            return resp.json()
        except Exception:
            return ParseResponse(success=False, error=f"Parse service error: {resp.text[:200]}")


# ----- LLMs.txt Generator -----

@router.post("/v2/generate-llmstxt", response_model=LLMsTextCreateResponse)
async def create_llmstxt(request: Request, body: LLMsTextRequest):
    """Generate an llms.txt file for a website."""
    store: JobStore = request.app.state.job_store
    job_id = store.create_job(kind="llmstxt", payload=body.model_dump())
    import asyncio
    from .worker import _process_llmstxt_async
    asyncio.create_task(
        _process_llmstxt_async(
            job_id=job_id, url=body.url, max_pages=body.max_pages,
            scraper_url=request.app.state.scraper_url, webhook_config=body.webhook,
        )
    )
    return LLMsTextCreateResponse(id=job_id)


@router.get("/v2/generate-llmstxt/{job_id}", response_model=LLMsTextStatusResponse)
async def get_llmstxt_status(request: Request, job_id: str):
    """Get llms.txt generation job status and results."""
    store: JobStore = request.app.state.job_store
    job = store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return LLMsTextStatusResponse(
        success=True,
        status=job.get("status", "processing"),
        data=job.get("data"),
        error=job.get("error"),
        expires_at=job.get("completed_at") or job.get("created_at"),
    )
