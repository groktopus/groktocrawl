"""Research Memory route handlers — cross-session semantic memory."""

import logging
from typing import Any

from fastapi import APIRouter, Request

from ..exceptions import NotFoundError, RateLimitedError
from ..models import (
    MemoryBatchQueryEntry,
    MemoryBatchQueryRequest,
    MemoryBatchQueryResponse,
    MemoryBatchStoreRequest,
    MemoryBatchStoreResponse,
    MemoryBatchStoreResult,
    ResearchMemoryQueryRequest,
    ResearchMemoryQueryResponse,
    ResearchMemoryStoreRequest,
    ResearchMemoryStoreResponse,
)
from ._helpers import _derive_user_id, _get_client_ip

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post(
    "/v2/research-memory/query",
    response_model=ResearchMemoryQueryResponse,
)
async def research_memory_query(
    request: Request, body: ResearchMemoryQueryRequest
) -> ResearchMemoryQueryResponse:
    """Search research memory for a semantically similar cached artifact.

    Embeds the question via semantic-svc, searches Qdrant for similar
    entries, and fetches matching artifacts from Valkey.  Returns the
    best match with freshness classification.

    Args:
        body: Contains ``question`` (the text to search for) and
            optional ``max_age_hours`` (default 72).

    Returns:
        ``ResearchMemoryQueryResponse`` with ``hit``, ``artifact``,
        ``age_hours``, and ``freshness`` fields.
    """
    memory = request.app.state.research_memory
    user_id = _derive_user_id(request)
    result = await memory.query(
        prompt=body.question,
        user_id=user_id,
        max_age_hours=body.max_age_hours,
    )
    return ResearchMemoryQueryResponse(**result)


@router.post(
    "/v2/research-memory/store",
    response_model=ResearchMemoryStoreResponse,
)
async def research_memory_store(
    request: Request, body: ResearchMemoryStoreRequest
) -> ResearchMemoryStoreResponse:
    """Store a research artifact in the cross-session memory.

    Embeds the question via semantic-svc, stores the artifact in Valkey
    with TTL, and upserts a point in Qdrant for similarity search.

    Args:
        body: Contains ``question``, ``answer``, ``sources``, and
            optional ``metadata``.

    Returns:
        ``ResearchMemoryStoreResponse`` with the new ``artifact_id``.
    """
    memory = request.app.state.research_memory
    user_id = _derive_user_id(request)
    artifact_id = await memory.store(
        prompt=body.question,
        artifact=body.answer,
        sources=body.sources,
        user_id=user_id,
        metadata=body.metadata,
    )
    return ResearchMemoryStoreResponse(artifact_id=artifact_id)


@router.delete("/v2/research-memory/{artifact_id}")
async def research_memory_delete(request: Request, artifact_id: str) -> dict:
    """Delete a research memory artifact by ID from both Valkey and Qdrant.

    Args:
        artifact_id: The artifact ID returned by the store endpoint.

    Returns:
        ``{"success": true}`` if deleted, ``{"success": false}`` if
        the artifact was not found.
    """
    memory = request.app.state.research_memory
    deleted = await memory.delete(artifact_id)
    return {"success": deleted}


@router.get("/v2/memory/{memory_id}")
async def get_memory(request: Request, memory_id: str) -> dict[str, Any]:
    """Retrieve a research memory artifact by ID.

    Returns the full stored artifact including query, artifact text,
    sources, model, created_at, expires_at, and user_id.

    Args:
        memory_id: The memory ID (UUID v4).

    Returns:
        200 with the artifact dict, or 404 if not found.
    """
    memory = request.app.state.research_memory
    entry = await memory.get(memory_id)
    if entry is None:
        raise NotFoundError(
            detail="Memory artifact not found",
            details={"memory_id": memory_id},
        )
    return {"success": True, "memory_id": memory_id, **entry}


@router.delete("/v2/memory/{memory_id}")
async def delete_memory(request: Request, memory_id: str) -> dict[str, Any]:
    """Delete a research memory artifact from both Valkey and Qdrant.

    Args:
        memory_id: The memory ID to delete.

    Returns:
        ``{"success": true, "deleted": true}`` if deleted.

    Raises:
        ``NotFoundError`` (404) if the memory entry does not exist.
    """
    memory = request.app.state.research_memory
    deleted = await memory.delete(memory_id)
    if not deleted:
        raise NotFoundError(
            detail="Memory entry not found",
            details={"memory_id": memory_id},
        )
    return {"success": True, "deleted": True}


@router.post("/v2/memory/sweep")
async def sweep_memory(request: Request) -> dict[str, Any]:
    """Sweep orphaned Qdrant points whose Valkey keys have expired.

    Trigger a manual cleanup of the research_memory Qdrant collection.
    Scans all points and removes those with no corresponding Valkey key.

    **Admin-only operation**: Rate-limited to 1 call per 60s per
    client IP to prevent operational DoS from repeated full Qdrant
    scans.

    Returns:
        ``{"success": true, "swept": N}`` where N is the number of
        Qdrant points removed.
    """
    # Rate limit: one sweep per 60s per client IP
    client_ip = _get_client_ip(request)
    rate_limiter = request.app.state.memory_sweep_limiter
    allowed, _remaining = await rate_limiter.check(f"{client_ip}:memory_sweep")
    if not allowed:
        raise RateLimitedError(
            detail=f"Sweep rate limit exceeded — max 1 per {rate_limiter.window}s. "
            "This is an admin-only maintenance endpoint."
        )

    memory = request.app.state.research_memory
    count = await memory.sweep()
    return {"success": True, "swept": count}


@router.post(
    "/v2/memory/batch/query",
    response_model=MemoryBatchQueryResponse,
)
async def memory_batch_query(
    request: Request,
    body: MemoryBatchQueryRequest,
) -> MemoryBatchQueryResponse:
    """Batch lookup of multiple queries against research memory.

    Each query is independently embedded and searched.  Results are
    returned in the same order as the input queries.

    Args:
        body: Contains ``queries`` (list of strings).

    Returns:
        ``MemoryBatchQueryResponse`` with ``results`` array of
        per-query hit/miss, similarity, freshness, and artifact data.
    """
    if not body.queries:
        return MemoryBatchQueryResponse(success=True, results=[])

    memory = request.app.state.research_memory
    user_id = _derive_user_id(request)
    raw_results = await memory.batch_query(
        queries=body.queries,
        user_id=user_id,
    )
    results: list[MemoryBatchQueryEntry] = []
    for i, r in enumerate(raw_results):
        entry = MemoryBatchQueryEntry(
            hit=r.get("hit", False),
            similarity=r.get("similarity"),
            freshness=r.get("freshness"),
            memory_id=r.get("memory_id"),
        )
        if r.get("hit") and r.get("artifact"):
            art = r["artifact"]
            entry.query = art.get(
                "query", body.queries[i] if i < len(body.queries) else ""
            )
            entry.artifact = art.get("artifact", "")
            entry.sources = art.get("sources", [])
        results.append(entry)
    return MemoryBatchQueryResponse(success=True, results=results)


@router.post(
    "/v2/memory/batch/store",
    response_model=MemoryBatchStoreResponse,
)
async def memory_batch_store(
    request: Request,
    body: MemoryBatchStoreRequest,
) -> MemoryBatchStoreResponse:
    """Batch store multiple research artifacts in memory.

    Each entry is stored independently.  If one fails (e.g. embedding
    failure), the others still succeed.  Per-entry status is returned.

    Args:
        body: Contains ``entries`` list of ``{query, artifact, sources,
            model}`` dicts.

    Returns:
        ``MemoryBatchStoreResponse`` with ``stored_count``,
        ``failed_count``, and per-entry ``results``.
    """
    if not body.entries:
        return MemoryBatchStoreResponse(
            success=True,
            stored_count=0,
            failed_count=0,
            results=[],
        )

    memory = request.app.state.research_memory
    user_id = _derive_user_id(request)
    raw_results = await memory.batch_store(
        entries=[e.model_dump() for e in body.entries],
        user_id=user_id,
    )
    results: list[MemoryBatchStoreResult] = []
    stored = 0
    failed = 0
    for r in raw_results:
        if r.get("success"):
            stored += 1
        else:
            failed += 1
        results.append(
            MemoryBatchStoreResult(
                success=r.get("success", False),
                memory_id=r.get("memory_id"),
                error=r.get("error"),
            )
        )
    return MemoryBatchStoreResponse(
        success=True,
        stored_count=stored,
        failed_count=failed,
        results=results,
    )
