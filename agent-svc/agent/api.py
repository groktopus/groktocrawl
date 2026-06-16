"""Route handlers implementing the GroktoCrawl API surface.

Targets Firecrawl v2 API compatibility where possible.
"""

import logging
from datetime import UTC

import httpx
from bs4 import BeautifulSoup
from fastapi import APIRouter, Request, Response

from common.url import extract_domain, is_same_origin

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
    BatchScrapeRequest,
    BrowserCreateRequest,
    BrowserCreateResponse,
    BrowserDeleteResponse,
    BrowserExecuteRequest,
    BrowserExecuteResponse,
    BrowserListResponse,
    Citation,
    CrawlCreateResponse,
    CrawlRequest,
    CrawlStatusResponse,
    ExtractCreateResponse,
    ExtractRequest,
    ExtractStatusResponse,
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
    ParseResponse,
    ScrapeData,
    ScrapeRequest,
    ScrapeResponse,
    SearchRequest,
    SearchResponse,
    SearchResult,
    Source,
)
from .monitor import delete_monitor, get_all_monitors, get_monitor, save_monitor
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
async def scrape(request: Request, body: ScrapeRequest):
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
async def create_agent(request: Request, body: AgentRequest, response: Response):
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
        from fastapi.responses import StreamingResponse

        async def event_stream():
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
            yield "data: [DONE]\n\n"

        headers = {
            "X-Search-Budget": f"{max_searches}/{max_searches}",
            "X-Search-Rate-Remaining": f"{rate_remaining}/{rate_limiter.limit}",
        }
        return StreamingResponse(
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
async def get_agent_status(request: Request, job_id: str):
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
async def cancel_agent(request: Request, job_id: str):
    store: JobStore = request.app.state.job_store
    if not store.cancel_job(job_id):
        raise NotFoundError(
            detail="Job not found or already completed", details={"job_id": job_id}
        )
    return AgentCancelResponse(success=True)


@router.post("/v2/crawl", response_model=CrawlCreateResponse)
async def create_crawl(request: Request, body: CrawlRequest):
    store: JobStore = request.app.state.job_store
    job_id = store.create_job(kind="crawl", payload=body.model_dump())

    from .worker import _process_crawl_async

    request.app.state.task_tracker.create_background_task(
        _process_crawl_async(
            job_id=job_id,
            url=body.url,
            max_pages=body.max_pages,
            max_depth=body.max_depth,
            scraper_url=request.app.state.scraper_url,
            webhook_config=body.webhook,
            task_tracker=request.app.state.task_tracker,
        )
    )
    return CrawlCreateResponse(id=job_id)


@router.get("/v2/crawl/{job_id}", response_model=CrawlStatusResponse)
async def get_crawl_status(request: Request, job_id: str):
    store: JobStore = request.app.state.job_store
    job = store.get_job(job_id)
    if job is None:
        raise NotFoundError(detail="Job not found", details={"job_id": job_id})
    data = job.get("data") or {}
    return CrawlStatusResponse(
        status=job.get("status", "processing"),
        completed=data.get("completed", 0),
        total=data.get("total", 0),
        data=data.get("pages"),
        error=job.get("error"),
    )


@router.delete("/v2/crawl/{job_id}", response_model=AgentCancelResponse)
async def cancel_crawl(request: Request, job_id: str):
    store: JobStore = request.app.state.job_store
    if not store.cancel_job(job_id):
        raise NotFoundError(
            detail="Job not found or already completed", details={"job_id": job_id}
        )
    return AgentCancelResponse(success=True)


@router.post("/v2/batch/scrape", response_model=CrawlCreateResponse)
async def create_batch_scrape(request: Request, body: BatchScrapeRequest):
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


@router.post("/v1/search")
async def search_v1(request: Request, body: SearchRequest):
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
async def search(request: Request, body: SearchRequest):
    from .searxng_client import SearXNGClient

    searxng = SearXNGClient(request.app.state.searxng_url)
    try:
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
                texts = [body.query] + contents
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
                        sum(a * b for a, b in zip(query_embedding, doc_emb))
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
        data: dict[str, list] = {"web": [], "images": [], "news": []}
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

        return SearchResponse(data=data, output=output)
    finally:
        await searxng.close()


@router.post("/v2/answer", response_model=AnswerResponse)
async def answer(request: Request, body: AnswerRequest, response: Response):
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

        async def event_stream():
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
        return StreamingResponse(
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


@router.post("/v2/extract", response_model=ExtractCreateResponse)
async def create_extract(request: Request, body: ExtractRequest):
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
async def get_extract_status(request: Request, job_id: str):
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
async def map_site(request: Request, body: MapRequest):
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
            resp = await client.get(body.url)
            if resp.status_code != 200:
                raise UpstreamError(detail=f"Site returned HTTP {resp.status_code}")
            soup = BeautifulSoup(resp.text, "html.parser")
            links: list[str] = []
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if href.startswith("/"):
                    href = f"{extract_domain(body.url, include_scheme=True)}{href}"
                if href.startswith(body.url.rstrip("/")) or is_same_origin(
                    body.url, href
                ):
                    if href not in links:
                        links.append(href)
                        if len(links) >= body.limit:
                            break
            if body.search:
                links = [l for l in links if body.search.lower() in l.lower()]
            return MapResponse(links=links)
    except Exception as e:
        logger.error("Map failed for %s: %s", body.url, e)
        raise UpstreamError(detail=str(e)) from e


# ----- Browser Sessions -----

BROWSER_SVC_URL = "http://browser-svc:8012"


async def _browser_proxy(
    path: str, method: str = "POST", json_data: dict | None = None
) -> dict:
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
        raise BrowserError(
            detail=result.get("detail", result.get("error", "Browser service error"))
        )
    return BrowserCreateResponse(id=result["id"])


@router.post("/v2/browser/{session_id}/execute", response_model=BrowserExecuteResponse)
async def execute_browser(session_id: str, body: BrowserExecuteRequest):
    result = await _browser_proxy(
        f"/browsers/{session_id}/execute", json_data=body.model_dump()
    )
    if not result.get("success"):
        raise BrowserError(detail=result.get("error", "Browser execution failed"))
    return BrowserExecuteResponse(success=True, result=result.get("result"))


@router.get("/v2/browser", response_model=BrowserListResponse)
async def list_browsers():
    result = await _browser_proxy("/browsers", method="GET")
    return BrowserListResponse(sessions=result.get("sessions", []))


@router.delete("/v2/browser/{session_id}", response_model=BrowserDeleteResponse)
async def destroy_browser(session_id: str):
    result = await _browser_proxy(f"/browsers/{session_id}", method="DELETE")
    if not result.get("success"):
        raise NotFoundError(
            detail="Session not found", details={"session_id": session_id}
        )
    return BrowserDeleteResponse(id=session_id)


# ----- Monitor -----


@router.post("/v2/monitor", response_model=MonitorResponse)
async def create_monitor(body: MonitorCreateRequest):
    import uuid
    from datetime import datetime

    monitor_id = str(uuid.uuid4())
    config = {
        "url": body.url,
        "schedule": body.schedule,
        "webhook": body.webhook,
        "created_at": datetime.now(UTC).isoformat(),
        "last_content": "",
    }
    save_monitor(monitor_id, config)
    return MonitorResponse(
        id=monitor_id,
        url=body.url,
        schedule=body.schedule,
        webhook=body.webhook,
        created_at=config["created_at"],
    )


@router.get("/v2/monitor", response_model=MonitorListResponse)
async def list_monitors():
    monitors = get_all_monitors()
    items = []
    for mid, cfg in monitors.items():
        items.append(
            MonitorResponse(
                id=mid,
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
async def get_monitor_status(monitor_id: str):
    cfg = get_monitor(monitor_id)
    if cfg is None:
        raise NotFoundError(
            detail="Monitor not found", details={"monitor_id": monitor_id}
        )
    return MonitorResponse(
        id=monitor_id,
        url=cfg.get("url", ""),
        schedule=cfg.get("schedule", ""),
        webhook=cfg.get("webhook"),
        last_checked=cfg.get("last_checked"),
        last_result=cfg.get("last_result"),
        created_at=cfg.get("created_at", ""),
    )


@router.patch("/v2/monitor/{monitor_id}", response_model=MonitorResponse)
async def update_monitor(monitor_id: str, body: MonitorUpdateRequest):
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
    save_monitor(monitor_id, cfg)
    return MonitorResponse(
        id=monitor_id,
        url=cfg.get("url", ""),
        schedule=cfg.get("schedule", ""),
        webhook=cfg.get("webhook"),
        last_checked=cfg.get("last_checked"),
        last_result=cfg.get("last_result"),
        created_at=cfg.get("created_at", ""),
    )


@router.delete("/v2/monitor/{monitor_id}", response_model=MonitorDeleteResponse)
async def delete_monitor_route(monitor_id: str):
    cfg = get_monitor(monitor_id)
    if cfg is None:
        raise NotFoundError(
            detail="Monitor not found", details={"monitor_id": monitor_id}
        )
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
        raise InvalidRequestError(
            detail="No file provided. Use multipart form with 'file' field."
        )

    upload = form["file"]
    content = await upload.read()

    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            f"{PARSE_SVC_URL}/parse",
            files={
                "file": (
                    upload.filename or "file",
                    content,
                    upload.content_type or "application/octet-stream",
                )
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
async def create_llmstxt(request: Request, body: LLMsTextRequest):
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
async def get_llmstxt_status(request: Request, job_id: str):
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


async def _index_scrape(url: str, title: str, content: str, request) -> None:
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
