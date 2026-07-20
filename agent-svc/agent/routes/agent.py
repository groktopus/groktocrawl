"""Agent route handlers — research agent, answer, and job management."""

import json
import logging
import os
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import StreamingResponse

from ..exceptions import NotFoundError, RateLimitedError
from ..metrics import METRICS
from ..models import (
    AgentCancelResponse,
    AgentCreateResponse,
    AgentRequest,
    AgentStatusResponse,
    AnswerRequest,
    AnswerResponse,
    Citation,
    Source,
)
from ..store import JobStore
from ._helpers import _derive_user_id, _get_client_ip, _resolve_output_schema

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Agent cache helpers ──────────────────────────────────────────


async def _lookup_agent_cache(request: Request, body: AgentRequest) -> dict | None:
    """Check Research Memory for a cached artifact matching the prompt.

    Returns the cache result dict on hit with ``fresh`` or ``aging``
    freshness, or ``None`` on miss / stale / error.
    """
    if body.force_fresh:
        return None
    try:
        memory = request.app.state.research_memory
        memory_scope = os.environ.get("RESEARCH_MEMORY_SCOPE", "global")
        user_id = _derive_user_id(request)
        cache_result = await memory.query(
            prompt=body.prompt,
            user_id=user_id if memory_scope == "per_user" else None,
        )
        if cache_result["hit"]:
            freshness = cache_result.get("freshness", "stale")
            if freshness in ("fresh", "aging"):
                return cache_result
        return None
    except Exception:
        logger.warning(
            "Agent cache lookup failed — proceeding with normal pipeline",
            exc_info=True,
        )
        return None


async def _handle_agent_streaming(
    request: Request,
    body: AgentRequest,
    cache_hit_data: dict | None,
    rate_remaining: int,
    max_searches: int,
) -> StreamingResponse | None:
    """Handle streaming dispatch: cache hit replay or live research pipeline.

    Returns a StreamingResponse for SSE paths, or None if the caller
    should fall through to the sync (create-and-poll) path.
    """
    rate_limiter = request.app.state.rate_limiter
    headers = {
        "X-Search-Budget": f"{max_searches}/{max_searches}",
        "X-Search-Rate-Remaining": f"{rate_remaining}/{rate_limiter.limit}",
    }

    # ── Cache HIT + streaming: return cached artifact as SSE ─────
    if cache_hit_data is not None and body.stream:
        from ..research.streaming import stream_cached_artifact

        entry = cache_hit_data["artifact"]
        artifact_text = entry.get("artifact", "")
        sources = entry.get("sources", [])
        has_schema = bool(body.output_schema or body.schema_)

        return StreamingResponse(
            stream_cached_artifact(
                artifact_text=artifact_text,
                sources=sources,
                memory_id=cache_hit_data.get("memory_id", ""),
                freshness=cache_hit_data.get("freshness", "fresh"),
                similarity=cache_hit_data.get("similarity", 0),
                citation_style=body.citation_style,
                has_schema=has_schema,
            ),
            media_type="text/event-stream",
            headers=headers,
        )

    # ── Streaming path (cache miss or force_fresh) ────────────────
    if body.stream:
        # Pre-flight LLM health check — fail fast before opening the stream
        from ..llm import LLMClient

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

        from ..research.streaming import stream_research_live

        return StreamingResponse(  # type: ignore[return-value]
            stream_research_live(
                prompt=body.prompt,
                urls=body.urls,
                schema=body.output_schema or body.schema_,
                searxng_url=request.app.state.searxng_url,
                scraper_url=request.app.state.scraper_url,
                llm_base_url=request.app.state.llm_base_url,
                llm_api_key=request.app.state.llm_api_key,
                llm_model=request.app.state.llm_model,
                requested_model=body.model if body.model != "default" else None,
                max_searches_per_request=max_searches,
                include_images=body.include_images,
                citation_style=body.citation_style,
                search_type=body.search_type,
                research_memory=request.app.state.research_memory,
                user_id=_derive_user_id(request),
            ),
            media_type="text/event-stream",
            headers=headers,
        )

    return None


# ── Route handlers ────────────────────────────────────────────────


@router.post("/v2/agent")
async def create_agent(request: Request, body: AgentRequest, response: Response) -> Any:
    # ── Per-client rate limit check ────────────────────────────
    client_ip = _get_client_ip(request)
    rate_limiter = request.app.state.rate_limiter
    allowed, rate_remaining = await rate_limiter.check(f"{client_ip}:search")
    if not allowed:
        METRICS.counter("search_calls_total", "Total search calls", ["status"]).inc(
            {"status": "rate_limited"}
        )
        raise RateLimitedError(
            detail=f"Per-client rate limit exceeded ({rate_limiter.limit}/{rate_limiter.window}s)"
        )

    max_searches = request.app.state.max_searches_per_request

    # ── Metrics ──────────────────────────────────────────────────
    METRICS.counter("search_calls_total", "Total search calls", ["status"]).inc(
        {"status": "allowed"}
    )

    # ── Mode: plan — generate a research plan ──
    if body.mode == "plan":
        response.headers["X-Search-Rate-Remaining"] = (
            f"{rate_remaining}/{rate_limiter.limit}"
        )
        from .plan import _handle_plan_mode

        return await _handle_plan_mode(request, body, response)

    # ── Check research memory cache ───────────────────────────────
    cache_hit_data = await _lookup_agent_cache(request, body)

    # ── Try streaming dispatch (cache hit replay or live pipeline) ─
    streaming_response = await _handle_agent_streaming(
        request, body, cache_hit_data, rate_remaining, max_searches
    )
    if streaming_response is not None:
        return streaming_response

    # ── Sync path — create job, process in background ─────────────
    store: JobStore = request.app.state.job_store
    job_id = store.create_job(
        kind="agent", payload=body.model_dump(exclude_none=True, by_alias=True)
    )

    from ..worker import _process_agent_async

    user_id = _derive_user_id(request)
    request.app.state.task_tracker.create_background_task(
        _process_agent_async(
            job_id=job_id,
            prompt=body.prompt,
            urls=body.urls,
            schema_=body.output_schema or body.schema_,
            llm_base_url=request.app.state.llm_base_url,
            llm_api_key=request.app.state.llm_api_key,
            llm_model=request.app.state.llm_model,
            searxng_url=request.app.state.searxng_url,
            scraper_url=request.app.state.scraper_url,
            webhook_config=body.webhook,
            requested_model=body.model,
            include_images=body.include_images,
            citation_style=body.citation_style,
            force_fresh=body.force_fresh,
            user_id=user_id,
            research_memory=request.app.state.research_memory,
            search_type=body.search_type,
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
        METRICS.counter("search_calls_total", "Total search calls", ["status"]).inc(
            {"status": "rate_limited"}
        )
        raise RateLimitedError(
            detail=f"Per-client rate limit exceeded ({rate_limiter.limit}/{rate_limiter.window}s)"
        )

    max_searches = request.app.state.max_searches_per_request

    # ── Metrics ──────────────────────────────────────────────────
    METRICS.counter("search_calls_total", "Total search calls", ["status"]).inc(
        {"status": "allowed"}
    )

    if body.stream:
        # Resolve effective schema: output_schema takes priority, empty dict treated as None
        effective_schema = _resolve_output_schema(body.output_schema, body.schema_)

        async def event_stream() -> Any:
            from ..research import run_answer_stream

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
                output_schema=effective_schema,
                citation_style=body.citation_style,
            ):
                if event["type"] == "sources_pending":
                    yield f"data: {json.dumps({'type': 'sources_pending', 'sources': event['sources']})}\n\n"
                elif event["type"] == "sources":
                    yield f"data: {json.dumps({'type': 'sources', 'sources': event['sources']})}\n\n"
                elif event["type"] == "token":
                    yield f"data: {json.dumps({'type': 'token', 'content': event['content']})}\n\n"
                elif event["type"] == "done":
                    yield f"data: {json.dumps({'type': 'done', 'answer': event['answer'], 'citations': event['citations'], 'latency_ms': event['latency_ms']})}\n\n"
                elif event["type"] == "error":
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
    from ..research import run_answer

    effective_schema = _resolve_output_schema(body.output_schema, body.schema_)

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
        output_schema=effective_schema,
        citation_style=body.citation_style,
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
