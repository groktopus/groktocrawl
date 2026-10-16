"""Plan route handlers — research plan generation, retrieval, and execution."""

import copy
import logging
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import StreamingResponse

from ..exceptions import NotFoundError
from ..models import (
    AgentCreateResponse,
    ExecutePlanRequest,
    PlanModification,
    PlanModifications,
    PlanRequest,
    PlanResponse,
)
from ..store import JobStore

logger = logging.getLogger(__name__)

router = APIRouter()


async def _handle_plan_mode(request: Request, body: Any, response: Response) -> Any:
    """Generate a structured research plan from an AgentRequest or PlanRequest.

    Called from ``create_agent`` when ``mode: "plan"`` and from
    ``/v2/agent/plan``.  Supports dual-path: streaming SSE or synchronous
    response.
    """
    from ..llm import LLMClient
    from ..planner import PlanStore, ResearchPlanner
    from ..settings import load_settings

    settings = load_settings()
    redis_url = (
        f"redis://{settings.valkey_host}:{settings.valkey_port}/{settings.valkey_db}"
    )

    effective_model = (
        body.model
        if body.model and body.model != "default"
        else request.app.state.llm_model
    )
    prompt = body.prompt
    urls = getattr(body, "urls", None)
    stream = getattr(body, "stream", False)

    llm = LLMClient(
        base_url=request.app.state.llm_base_url,
        api_key=request.app.state.llm_api_key,
        model=effective_model,
    )
    planner = ResearchPlanner()

    # ── Streaming path ───────────────────────────────────────
    if stream:
        # Pre-flight LLM health check
        if not await llm.check_health():
            await llm.close()
            from fastapi import HTTPException

            raise HTTPException(
                status_code=503,
                detail="LLM backend is not available. Cannot generate plan.",
            )

        async def event_stream() -> Any:
            import json

            try:
                async for event_type, data in planner.plan_stream(
                    prompt=prompt,
                    llm_client=llm,
                    urls=urls,
                ):
                    if event_type == "token":
                        yield f"data: {json.dumps({'type': 'token', 'content': data})}\n\n"
                    elif event_type == "plan":
                        store = PlanStore(redis_url=redis_url)
                        plan_id = store.create(prompt=prompt, plan=data)
                        yield f"data: {json.dumps({'type': 'plan', 'plan_id': plan_id, 'plan': data})}\n\n"
                        yield f"data: {json.dumps({'type': 'done', 'plan_id': plan_id, 'plan': data})}\n\n"
                    elif event_type == "error":
                        yield f"data: {json.dumps({'type': 'error', 'content': data})}\n\n"
            except Exception as e:
                logger.error("Plan stream failed: %s", e)
                yield f"data: {json.dumps({'type': 'error', 'content': str(e)})}\n\n"
            finally:
                await llm.close()
            yield "data: [DONE]\n\n"

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    # ── Sync path ────────────────────────────────────────────
    try:
        plan = await planner.plan(prompt=prompt, llm_client=llm, urls=urls)
    finally:
        await llm.close()

    store = PlanStore(redis_url=redis_url)
    plan_id = store.create(prompt=prompt, plan=plan)
    doc = store.get(plan_id) or {}
    return PlanResponse(
        plan_id=plan_id,
        plan=plan,
        created_at=doc.get("created_at", ""),
        expires_at=doc.get("expires_at", ""),
    )


@router.post("/v2/agent/plan", response_model=PlanResponse)
async def create_plan(
    request: Request, body: PlanRequest, response: Response
) -> PlanResponse:
    """Generate a structured research plan for a given prompt.

    Calls the LLM to decompose the prompt into ordered phases (search,
    scrape, synthesize), estimates how many sources the research will
    need, and identifies analysis dimensions.

    The plan is persisted in Valkey with a 1-hour TTL.  The client can
    review the plan, modify it, and then execute it via
    ``POST /v2/agent/execute``.

    Args:
        body: Contains ``prompt``, optional ``model`` override, optional
            ``urls`` (seed URLs), and optional ``stream`` (SSE mode).

    Returns:
        ``PlanResponse`` with ``plan_id``, full ``plan`` dict
        (``phases``, ``estimated_sources``, ``comparison_dimensions``),
        ``created_at``, and ``expires_at``.
    """
    return await _handle_plan_mode(request, body, response)


@router.get("/v2/agent/plan/{plan_id}")
async def get_plan(request: Request, plan_id: str) -> dict[str, Any]:
    """Retrieve a previously-generated research plan by ID.

    Returns the full plan document including ``plan_id``, ``prompt``,
    ``plan`` (phases, estimated_sources, comparison_dimensions),
    ``created_at``, and ``expires_at``.

    Returns 404 if the plan was not found, expired, or already consumed.
    """
    from ..planner import PlanStore
    from ..settings import load_settings

    settings = load_settings()
    redis_url = (
        f"redis://{settings.valkey_host}:{settings.valkey_port}/{settings.valkey_db}"
    )

    store = PlanStore(redis_url=redis_url)
    doc = store.get(plan_id)
    if doc is None:
        raise NotFoundError(
            detail="Plan not found or expired",
            details={"plan_id": plan_id},
        )
    return {"success": True, **doc}


@router.post("/v2/agent/execute")
async def execute_plan(
    request: Request, body: ExecutePlanRequest, response: Response
) -> Any:
    """Execute a previously-generated research plan with optional modifications.

    Loads the plan from Valkey, applies any modifications (narrow scope,
    add/remove dimensions), and either streams results as SSE or creates
    a job for async polling.

    Plans are ONE-SHOT: consumed (deleted) on first successful execution
    attempt.  Re-executing the same plan_id returns 404.

    Supports two modification formats:
        - List form: ``[{type: "narrow", params: {focus: "..."}}, ...]``
        - Dict form (legacy): ``{narrow: "...", add_dimension: [...], ...}``

    Args:
        body: Contains ``plan_id`` and optional ``modifications``.

    Returns:
        - When streaming: ``StreamingResponse`` (text/event-stream)
        - Sync path: ``AgentCreateResponse`` with job ID for polling
    """
    from ..planner import PlanStore
    from ..settings import load_settings

    settings = load_settings()
    redis_url = (
        f"redis://{settings.valkey_host}:{settings.valkey_port}/{settings.valkey_db}"
    )

    plan_store = PlanStore(redis_url=redis_url)
    doc = plan_store.get(body.plan_id)
    if doc is None:
        raise NotFoundError(
            detail="Plan not found, expired, or already executed",
            details={"plan_id": body.plan_id},
        )

    plan = doc["plan"]
    prompt = doc["prompt"]

    # Normalize modifications into a unified dict form (backward compatible)
    mods: dict[str, Any] | None = _normalize_modifications(body.modifications)

    # Apply modifications in-memory (do NOT mutate the stored plan)
    if mods:
        plan = _apply_plan_modifications(plan, mods, prompt)

    # ── Sync path: create job, process in background ──────────
    if not body.stream:
        store: JobStore = request.app.state.job_store
        job_id = store.create_plan_execution_job(
            body.plan_id,
            {
                "plan_id": body.plan_id,
                "prompt": prompt,
                "plan": plan,
                "modifications": mods,
                "webhook": body.webhook,
            },
        )
        if job_id is None:
            raise NotFoundError(
                detail="Plan not found, expired, or already executed",
                details={"plan_id": body.plan_id},
            )
        from ..job_execution import submit_job
        from ..worker import _process_plan_execution_async

        submit_job(
            request,
            job_id,
            _process_plan_execution_async(
                job_id=job_id,
                prompt=prompt,
                plan=plan,
                modifications=mods,
                llm_base_url=request.app.state.llm_base_url,
                llm_api_key=request.app.state.llm_api_key,
                llm_model=request.app.state.llm_model,
                searxng_url=request.app.state.searxng_url,
                scraper_url=request.app.state.scraper_url,
                webhook_config=body.webhook,
            ),
        )
        return AgentCreateResponse(id=job_id)

    # ── Streaming path: run inline, return SSE ────────────────
    async def event_stream() -> Any:
        import json
        import time as _time

        from ..llm import LLMClient
        from ..research import _scrape_urls
        from ..scraper_client import ScraperClient
        from ..searxng_client import SearXNGClient

        start = _time.monotonic()

        effective_model = request.app.state.llm_model
        llm = LLMClient(
            base_url=request.app.state.llm_base_url,
            api_key=request.app.state.llm_api_key,
            model=effective_model,
        )
        searxng = SearXNGClient(request.app.state.searxng_url)
        scraper = ScraperClient(request.app.state.scraper_url)

        all_sources: list[dict] = []
        accumulated_context_parts: list[str] = []
        seen_urls: set[str] = set()

        try:
            phases = plan.get("phases", [])
            full_synthesis = ""
            for phase_idx, phase in enumerate(phases):
                action = phase.get("action", "search")
                description = phase.get("description", "")

                yield f"data: {json.dumps({'type': 'phase', 'phase_index': phase_idx + 1, 'action': action, 'description': description, 'total_phases': len(phases)})}\n\n"

                if action == "search":
                    # Build query: combine prompt + phase description + narrow hint
                    query = description or prompt
                    if mods and mods.get("narrow"):
                        query = f"{mods.get('narrow')} {query}"
                    # Apply modify_query modifications for this specific phase
                    if mods and mods.get("modify_queries"):
                        for mq in mods["modify_queries"]:
                            if mq.get("phase_index") == phase_idx and mq.get(
                                "new_query"
                            ):
                                query = mq["new_query"]
                                break

                    try:
                        results, _health = await searxng.search(query, limit=10)
                    except Exception:
                        results = []

                    new_urls = []
                    for r in results:
                        url = r.get("url", "")
                        if url and url not in seen_urls:
                            seen_urls.add(url)
                            new_urls.append(url)
                            all_sources.append(
                                {
                                    "url": url,
                                    "title": r.get("title", ""),
                                    "relevance": r.get("description", ""),
                                }
                            )

                    yield f"data: {json.dumps({'type': 'search', 'query': query, 'result_count': len(new_urls), 'new_urls': new_urls[:10]})}\n\n"

                    # Scrape discovered URLs
                    if new_urls:
                        artifacts = await _scrape_urls(
                            new_urls[:5],
                            scraper,
                            min_sources=1,
                            max_attempts=min(5, len(new_urls)),
                        )
                        for artifact in artifacts:
                            doc = artifact.to_document()
                            accumulated_context_parts.append(doc)
                            yield f"data: {json.dumps({'type': 'scrape', 'url': artifact.url, 'chars': len(doc)})}\n\n"

                elif action == "scrape":
                    # Phase description may contain URLs or URL hints
                    pass

                elif action == "synthesize":
                    context = (
                        "\n\n---\n\n".join(accumulated_context_parts)
                        if accumulated_context_parts
                        else ""
                    )
                    synthesis_prompt = (
                        description or f"Synthesise findings for: {prompt}"
                    )

                    # Include dimensions in the synthesis prompt
                    dimensions = list(
                        plan.get("comparison_dimensions", plan.get("dimensions", []))
                    )
                    if mods:
                        if mods.get("add_dimension"):
                            for d in mods.get("add_dimension", []):
                                if d not in dimensions:
                                    dimensions.append(d)
                        if mods.get("remove_dimension"):
                            dimensions = [
                                d
                                for d in dimensions
                                if d not in (mods.get("remove_dimension") or [])
                            ]

                    if dimensions:
                        dims_str = ", ".join(dimensions)
                        synthesis_prompt = (
                            f"{synthesis_prompt}\n\n"
                            f"CRITICAL: Address each of these analysis dimensions: {dims_str}. "
                            f"For each dimension, provide specific evidence from the sources below."
                        )

                    synthesis_prompt = (
                        f"{synthesis_prompt}\n\n"
                        f"Base your answer ONLY on the source content provided below. "
                        f"Cite sources by their URL. Be thorough and precise."
                    )

                    full_synthesis = ""
                    async for chunk in llm.generate_stream(
                        system_prompt=(
                            "You are GroktoCrawl, a research synthesis agent. "
                            "Synthesise information from the provided sources into a "
                            "comprehensive, well-structured answer. Be thorough, precise, "
                            "and cite specific sources."
                        ),
                        user_prompt=synthesis_prompt,
                        context=context or None,
                    ):
                        if chunk["type"] == "token":
                            full_synthesis += chunk["content"]
                            yield f"data: {json.dumps({'type': 'token', 'content': chunk['content']})}\n\n"
                        elif chunk["type"] == "error":
                            yield f"data: {json.dumps({'type': 'error', 'content': chunk['content']})}\n\n"

                    if full_synthesis:
                        accumulated_context_parts.append(full_synthesis)

            # Final done event
            latency_ms = int((_time.monotonic() - start) * 1000)
            result_data = {
                "result": full_synthesis,
                "sources": all_sources,
                "latency_ms": latency_ms,
            }
            yield f"data: {json.dumps({'type': 'done', **result_data})}\n\n"

            # Fire webhook on completion (fire-and-forget, don't block stream)
            if body.webhook:
                from ..webhook import deliver_webhook

                await deliver_webhook(
                    body.webhook,
                    "completed",
                    body.plan_id,
                    result_data,
                )

        except Exception as e:
            logger.error("Plan execution failed: %s", e)
            yield f"data: {json.dumps({'type': 'error', 'content': str(e)})}\n\n"

            # Fire webhook on failure
            if body.webhook:
                from ..webhook import deliver_webhook

                await deliver_webhook(
                    body.webhook,
                    "failed",
                    body.plan_id,
                    {"error": str(e)},
                    success=False,
                    error=str(e),
                )
        finally:
            await llm.close()
            await searxng.close()
            await scraper.close()

        yield "data: [DONE]\n\n"

    if plan_store.consume(body.plan_id) is None:
        raise NotFoundError(
            detail="Plan not found, expired, or already executed",
            details={"plan_id": body.plan_id},
        )
    return StreamingResponse(event_stream(), media_type="text/event-stream")


def _normalize_modifications(raw: Any) -> dict[str, Any] | None:
    """Normalize modifications from list or dict form into a unified dict.

    - List form: ``[{type: "narrow", params: {focus: "..."}}, ...]`` →
      ``{narrow: "...", add_dimension: [...]}``
    - Dict form / PlanModifications: passthrough
    - None / empty: returns None

    Args:
        raw: Raw modifications from the request body (after model validation).

    Returns:
        A unified dict with optional ``narrow`` (str), ``add_dimension``
        (list[str]), and ``remove_dimension`` (list[str]) keys, or ``None``.
    """
    if raw is None:
        return None

    # Already normalized dict form (PlanModifications)
    if isinstance(raw, PlanModifications):
        result: dict[str, Any] = {}
        if raw.narrow:
            result["narrow"] = raw.narrow
        if raw.add_dimension:
            result["add_dimension"] = raw.add_dimension
        if raw.remove_dimension:
            result["remove_dimension"] = raw.remove_dimension
        return result or None

    if isinstance(raw, dict) and "type" not in raw:
        # Legacy dict form
        return raw or None

    # List form: convert each PlanModification into unified dict
    items = raw if isinstance(raw, list) else [raw]
    if isinstance(items, (PlanModifications, dict)):
        return _normalize_modifications(items)

    result = {}
    for mod in items:
        if isinstance(mod, PlanModification):
            mod_type = mod.type
            mod_params = mod.params
        elif isinstance(mod, dict):
            mod_type = mod.get("type", "")
            mod_params = mod.get("params", {})
        else:
            continue

        if mod_type == "narrow":
            result["narrow"] = mod_params.get("focus", mod_params.get("narrow", ""))
        elif mod_type == "add_dimension":
            dim = mod_params.get("dimension", mod_params.get("name", ""))
            if dim:
                result.setdefault("add_dimension", []).append(dim)
        elif mod_type == "modify_query":
            # Store modify_query params for future use
            result.setdefault("modify_queries", []).append(mod_params)
        elif mod_type == "remove_dimension":
            dim = mod_params.get("dimension", mod_params.get("name", ""))
            if dim:
                result.setdefault("remove_dimension", []).append(dim)

    return result or None


def _apply_plan_modifications(
    plan: dict,
    modifications: dict[str, Any],
    prompt: str,
) -> dict:
    """Apply user modifications to a plan in-memory.

    Returns a deep copy of the plan with modifications applied.
    Does NOT mutate the original dict or persist changes to Valkey.

    Args:
        plan: The original plan dict with ``phases``, ``dimensions``, etc.
        modifications: A unified dict with optional ``narrow`` (str),
            ``add_dimension`` (list[str]), ``remove_dimension`` (list[str]),
            and ``modify_queries`` (list[dict]).
        prompt: The original research prompt (used when narrowing).

    Returns:
        A modified plan dict.
    """
    plan = copy.deepcopy(plan)

    # Narrow — inject focus into the first search phase
    narrow = modifications.get("narrow")
    if narrow:
        phases = plan.get("phases", [])
        for phase in phases:
            if phase.get("action") == "search":
                phase["description"] = (
                    f"[FOCUS: {narrow}] {phase.get('description', '')}"
                )
                break
        else:
            # No search phase exists — prepend one
            phases.insert(
                0,
                {
                    "action": "search",
                    "description": f"[FOCUS: {narrow}] Search for: {prompt}",
                },
            )

    # Apply modify_query changes
    modify_queries = modifications.get("modify_queries", [])
    for mq in modify_queries:
        phase_index = mq.get("phase_index")
        new_query = mq.get("new_query")
        if phase_index is not None and new_query:
            phases = plan.get("phases", [])
            if 0 <= phase_index < len(phases):
                phases[phase_index]["description"] = new_query

    # Add dimensions (supports both "dimensions" and "comparison_dimensions" keys)
    dims_key: str = (
        "comparison_dimensions" if "comparison_dimensions" in plan else "dimensions"
    )
    add_dims = modifications.get("add_dimension")
    if add_dims:
        existing = plan.setdefault(dims_key, [])
        for d in add_dims:
            if d not in existing:
                existing.append(d)

    # Remove dimensions
    remove_dims = modifications.get("remove_dimension")
    if remove_dims:
        plan[dims_key] = [d for d in plan.get(dims_key, []) if d not in remove_dims]

    return plan
