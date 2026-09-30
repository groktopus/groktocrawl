"""Agent route handlers — research agent, answer, and job management."""

import json
import logging
import os
from collections.abc import AsyncGenerator, AsyncIterator
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
    CitationStyle,
    Source,
)
from ..research_memory import compute_fingerprint
from ..store import JobStore
from ._helpers import _derive_user_id, _get_client_ip, _resolve_output_schema

logger = logging.getLogger(__name__)

router = APIRouter()


async def _serialize_answer_stream(
    events: AsyncIterator[dict[str, Any]],
) -> AsyncGenerator[str, None]:
    """Serialize answer events without masking terminal error semantics."""
    async for event in events:
        if event["type"] == "sources_pending":
            payload = {"type": "sources_pending", "sources": event["sources"]}
        elif event["type"] == "sources":
            payload = {"type": "sources", "sources": event["sources"]}
        elif event["type"] == "token":
            payload = {"type": "token", "content": event["content"]}
        elif event["type"] == "done":
            payload = {
                "type": "done",
                "answer": event["answer"],
                "citations": event["citations"],
                "latency_ms": event["latency_ms"],
            }
        elif event["type"] == "error":
            payload = {"type": "error", "content": event["content"]}
            for key in ("classification", "retry_after_seconds"):
                if key in event:
                    payload[key] = event[key]
            yield f"data: {json.dumps(payload)}\n\n"
            return
        else:
            continue
        yield f"data: {json.dumps(payload)}\n\n"
    yield "data: [DONE]\n\n"


def fingerprint_from_agent_request(body: AgentRequest) -> str:
    """Compute the canonical replay-compatibility fingerprint for *body*."""
    citation_style = (
        body.citation_style.value
        if isinstance(body.citation_style, CitationStyle)
        else str(body.citation_style)
    )
    return compute_fingerprint(
        prompt=body.prompt,
        urls=body.urls,
        schema=body.output_schema or body.schema_,
        model=body.model,
        search_type=body.search_type,
        include_images=body.include_images,
        include_source_content=body.include_source_content,
        citation_style=citation_style,
        strict_constrain_to_urls=body.strict_constrain_to_urls,
        force_fresh=body.force_fresh,
    )


# ── Agent cache helpers ──────────────────────────────────────────


async def _lookup_agent_cache(
    request: Request, body: AgentRequest, fingerprint: str
) -> dict | None:
    """Check Research Memory for a cached artifact matching the prompt.

    Returns the cache result dict on hit with ``fresh``/``aging``
    freshness, or on a stale-while-revalidate-eligible ``stale`` hit,
    or ``None`` on miss / incompatible / stale / error.
    """
    if body.force_fresh:
        return None
    if body.include_source_content:
        # Research Memory stores compact source metadata, not scraped bodies.
        # An export request must execute acquisition so the response can carry
        # the requested source content.
        return None
    try:
        memory = request.app.state.research_memory
        memory_scope = os.environ.get("RESEARCH_MEMORY_SCOPE", "global")
        user_id = _derive_user_id(request)
        cache_result = await memory.query(
            prompt=body.prompt,
            user_id=user_id if memory_scope == "per_user" else None,
            fingerprint=fingerprint,
            max_stale_hours=(
                body.max_stale_hours if body.stale_while_revalidate else None
            ),
        )
        if cache_result["hit"]:
            freshness = cache_result.get("freshness", "stale")
            if freshness in ("fresh", "aging"):
                return cache_result
            if (
                freshness == "stale"
                and body.stale_while_revalidate
                and cache_result.get("swr_eligible")
            ):
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
    fingerprint: str,
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
        freshness = cache_hit_data.get("freshness", "fresh")

        refresh_awaitable = None
        age_hours = None
        if (
            freshness == "stale"
            and body.stale_while_revalidate
            and cache_hit_data.get("swr_eligible")
        ):
            from ..research.memory import refresh_research_memory

            memory = request.app.state.research_memory
            age_hours = cache_hit_data.get("age_hours")

            def _refresh_factory() -> Any:
                return refresh_research_memory(
                    memory,
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
                    max_credits=body.max_credits,
                    include_images=body.include_images,
                    include_source_content=body.include_source_content,
                    citation_style=body.citation_style,
                    search_type=body.search_type,
                    user_id=_derive_user_id(request),
                    fingerprint=fingerprint,
                )

            refresh_awaitable = memory.start_refresh(fingerprint, _refresh_factory)

        return StreamingResponse(
            stream_cached_artifact(
                artifact_text=artifact_text,
                sources=sources,
                memory_id=cache_hit_data.get("memory_id", ""),
                freshness=freshness,
                similarity=cache_hit_data.get("similarity", 0),
                citation_style=body.citation_style,
                has_schema=has_schema,
                age_hours=age_hours,
                refresh_awaitable=refresh_awaitable,
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
                max_credits=body.max_credits,
                include_images=body.include_images,
                include_source_content=body.include_source_content,
                citation_style=body.citation_style,
                search_type=body.search_type,
                research_memory=request.app.state.research_memory,
                user_id=_derive_user_id(request),
                fingerprint=fingerprint,
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
        retry_after = rate_limiter.retry_after_seconds()
        METRICS.counter("search_calls_total", "Total search calls", ["status"]).inc(
            {"status": "rate_limited"}
        )
        METRICS.counter(
            "rate_limited_admissions_total",
            "Admission requests rejected by per-client rate limit",
            ["operation", "bucket"],
        ).inc({"operation": "agent", "bucket": "search"})
        raise RateLimitedError(
            detail=(
                f"Per-client rate limit exceeded "
                f"({rate_limiter.limit}/{rate_limiter.window}s) — retry in {retry_after}s"
            ),
            retry_after_seconds=retry_after,
            bucket="search",
            limit=rate_limiter.limit,
            remaining=0,
            reset_at=rate_limiter.reset_at_iso(),
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
    # Only the streaming path performs the lookup in the route (it needs the
    # cached artifact to replay it inline). The non-streaming path defers the
    # single lookup to the background worker so one request never performs
    # the semantic query twice.
    fingerprint = fingerprint_from_agent_request(body)
    cache_hit_data = (
        await _lookup_agent_cache(request, body, fingerprint) if body.stream else None
    )

    # ── Try streaming dispatch (cache hit replay or live pipeline) ─
    streaming_response = await _handle_agent_streaming(
        request, body, cache_hit_data, fingerprint, rate_remaining, max_searches
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
            include_source_content=body.include_source_content,
            citation_style=body.citation_style,
            force_fresh=body.force_fresh,
            stale_while_revalidate=body.stale_while_revalidate,
            max_stale_hours=body.max_stale_hours,
            user_id=user_id,
            research_memory=request.app.state.research_memory,
            search_type=body.search_type,
            max_searches_per_request=max_searches,
            max_credits=body.max_credits,
            fingerprint=fingerprint,
            task_tracker=request.app.state.task_tracker,
        ),
        job_id=job_id,
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
        retry_at=job.get("retry_at"),
        retry_attempt=job.get("retry_attempt"),
        retry_limit=job.get("retry_limit"),
        retryable=True if job.get("status") == "retry_scheduled" else None,
        retry_reason=job.get("retry_reason"),
    )


@router.delete("/v2/agent/{job_id}", response_model=AgentCancelResponse)
async def cancel_agent(request: Request, job_id: str) -> AgentCancelResponse:
    store: JobStore = request.app.state.job_store
    if not store.cancel_job(job_id):
        raise NotFoundError(
            detail="Job not found or already completed", details={"job_id": job_id}
        )
    request.app.state.task_tracker.cancel_job(job_id)
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
        retry_after = rate_limiter.retry_after_seconds()
        METRICS.counter("search_calls_total", "Total search calls", ["status"]).inc(
            {"status": "rate_limited"}
        )
        METRICS.counter(
            "rate_limited_admissions_total",
            "Admission requests rejected by per-client rate limit",
            ["operation", "bucket"],
        ).inc({"operation": "answer", "bucket": "search"})
        raise RateLimitedError(
            detail=(
                f"Per-client rate limit exceeded "
                f"({rate_limiter.limit}/{rate_limiter.window}s) — retry in {retry_after}s"
            ),
            retry_after_seconds=retry_after,
            bucket="search",
            limit=rate_limiter.limit,
            remaining=0,
            reset_at=rate_limiter.reset_at_iso(),
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

            events = run_answer_stream(
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
            async for chunk in _serialize_answer_stream(events):
                yield chunk

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
