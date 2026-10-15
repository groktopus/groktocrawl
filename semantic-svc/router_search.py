"""Vector search route for semantic-svc.

Extracted from app.py per ADR-0037.
"""

import asyncio
import logging

import app as app_module
from app import (
    COLLECTION_NAME,
    QDRANT_QUERY_TIMEOUT,
    SHADOW_CONFIG,
    _create_background_task,
    _ensure_qdrant,
    _get_active_model,
    _get_embed_model,
    _run_required_pgvector_operation,
    _run_shadow_operation,
    _shadow_store,
    run_inference,
)
from fastapi import APIRouter, HTTPException
from metrics import METRICS
from models import VectorSearchRequest, VectorSearchResponse, VectorSearchResult
from router_index import _track_access
from shadow_pgvector import compare_results, should_sample

logger = logging.getLogger(__name__)

router_search = APIRouter()


async def _compare_shadow_results(
    query_embedding: list[float], active_model: str, limit: int, hits: list
) -> None:
    """Compare pgvector with Qdrant without retaining query text or serving it."""
    shadow_results = await _run_shadow_operation(
        "search",
        lambda: _shadow_store.search(query_embedding, model=active_model, limit=limit),
    )
    if shadow_results is None:
        outcome = "failure"
    else:
        comparison = compare_results(
            [(int(hit.id), float(hit.score)) for hit in hits],
            shadow_results,
            score_tolerance=SHADOW_CONFIG.score_tolerance,
        )
        outcome = "match" if comparison.matches else "mismatch"
        if comparison.maximum_score_delta is not None:
            METRICS.histogram(
                "groktocrawl_pgvector_shadow_score_delta",
                "Maximum score delta per Qdrant and pgvector comparison",
            ).observe({}, comparison.maximum_score_delta)
        if not comparison.matches:
            logger.info(
                "pgvector shadow mismatch: authoritative_ids=%s shadow_ids=%s",
                comparison.authoritative_ids,
                comparison.shadow_ids,
            )
    METRICS.counter(
        "groktocrawl_pgvector_shadow_comparisons_total",
        "Pgvector search comparisons by outcome",
        ["outcome"],
    ).inc({"outcome": outcome})


@router_search.post("/vector", response_model=VectorSearchResponse)
async def search_vector(body: VectorSearchRequest):
    """Search the vector index by semantic similarity.

    Phase 4: searches the active named vector. The active model
    is determined by _get_active_model() — defaults to env var,
    overridable via /migrate/cutover.
    """
    if not app_module._models_ready:
        raise HTTPException(
            503, "Models are still loading — please retry in a few seconds"
        )
    model = _get_embed_model()

    query_embedding = await run_inference(
        "vector_search",
        lambda: model.encode(body.query, normalize_embeddings=True).tolist(),
    )

    loop = asyncio.get_running_loop()

    active_nv = _get_active_model()

    if SHADOW_CONFIG.serving:
        pgvector_results = await _run_required_pgvector_operation(
            "search",
            lambda: _shadow_store.search(
                query_embedding, model=active_nv, limit=body.limit
            ),
        )
        return VectorSearchResponse(
            results=[
                VectorSearchResult(
                    url=result.url,
                    title=result.title,
                    score=result.score,
                    description=result.description,
                    indexed_at=result.indexed_at,
                )
                for result in pgvector_results
            ]
        )

    qdrant = await _ensure_qdrant()

    # Qdrant query_points() is a blocking call. Run it off the event loop
    # with a bounded timeout so a slow or unhealthy index degrades to a
    # structured 503 instead of escaping as an unhandled 500.
    try:
        hits = await asyncio.wait_for(
            loop.run_in_executor(
                None,
                lambda: (
                    qdrant.query_points(
                        collection_name=COLLECTION_NAME,
                        query=query_embedding,
                        using=active_nv,
                        limit=body.limit,
                    ).points
                ),
            ),
            timeout=QDRANT_QUERY_TIMEOUT,
        )
    except TimeoutError:
        logger.error(
            "Vector search timed out querying Qdrant after %ss", QDRANT_QUERY_TIMEOUT
        )
        raise HTTPException(503, "Vector index query timed out")
    except Exception:
        logger.exception("Vector search failed querying Qdrant")
        raise HTTPException(503, "Vector index unavailable")

    results = [
        VectorSearchResult(
            url=h.payload.get("url", ""),  # type: ignore[union-attr]
            title=h.payload.get("title", ""),  # type: ignore[union-attr]
            score=float(h.score),
            description=h.payload.get("content_excerpt", ""),  # type: ignore[union-attr]
            indexed_at=h.payload.get("last_indexed_at"),  # type: ignore[union-attr]
        )
        for h in hits
    ]

    # Fire-and-forget access tracking (Phase 3)
    if hits:
        _create_background_task(_track_access(qdrant, hits))

    if SHADOW_CONFIG.enabled and should_sample(body.query, SHADOW_CONFIG.sample_rate):
        _create_background_task(
            _compare_shadow_results(query_embedding, active_nv, body.limit, hits)
        )

    return VectorSearchResponse(results=results)
