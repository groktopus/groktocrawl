"""Vector search route for semantic-svc.

Extracted from app.py per ADR-0037.
"""

import asyncio
import logging

from fastapi import APIRouter, HTTPException

from app import (
    COLLECTION_NAME,
    _ensure_qdrant,
    _get_active_model,
    _get_embed_model,
    _models_ready,
)
from models import VectorSearchRequest, VectorSearchResult, VectorSearchResponse
from router_index import _track_access

logger = logging.getLogger(__name__)

router_search = APIRouter()


@router_search.post("/vector", response_model=VectorSearchResponse)
async def search_vector(body: VectorSearchRequest):
    """Search the vector index by semantic similarity.

    Phase 4: searches the active named vector. The active model
    is determined by _get_active_model() — defaults to env var,
    overridable via /migrate/cutover.
    """
    if not _models_ready:
        raise HTTPException(
            503, "Models are still loading — please retry in a few seconds"
        )
    qdrant = await _ensure_qdrant()
    model = _get_embed_model()

    loop = asyncio.get_event_loop()
    query_embedding = await loop.run_in_executor(
        None,
        lambda: model.encode(body.query, normalize_embeddings=True).tolist(),
    )

    active_nv = _get_active_model()

    hits = qdrant.query_points(
        collection_name=COLLECTION_NAME,
        query=query_embedding,
        using=active_nv,
        limit=body.limit,
    ).points

    results = [
        VectorSearchResult(
            url=h.payload.get("url", ""),
            title=h.payload.get("title", ""),
            score=float(h.score),
        )
        for h in hits
    ]

    # Fire-and-forget access tracking (Phase 3)
    if hits:
        asyncio.ensure_future(_track_access(qdrant, hits))

    return VectorSearchResponse(results=results)
