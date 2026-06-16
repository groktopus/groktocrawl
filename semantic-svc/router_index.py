"""Index routes for semantic-svc — ingest, delete, stats, model info.

Extracted from app.py per ADR-0037.
"""

import asyncio
import json
import logging
import time

from fastapi import APIRouter, HTTPException
from qdrant_client import models
from sentence_transformers import SentenceTransformer

from app import (
    COLLECTION_NAME,
    EMBED_DIM,
    EMBED_MODEL_NAME,
    MAX_DOCS,
    _ensure_qdrant,
    _get_active_model,
    _get_embed_model,
    _migration,
    _models_ready,
    _named_vector_name,
    _now_iso,
    _url_hash,
)
from metrics import METRICS
from models import (
    IndexBatchRequest,
    IndexBatchResponse,
    IndexRequest,
    IndexResponse,
    IndexStatsResponse,
    ModelInfoResponse,
)
from retention import (
    _compute_domain_category,
    _compute_retention_score,
    _evict_if_needed,
)

logger = logging.getLogger(__name__)

router_index = APIRouter()


# ── Payload building ──────────────────────────────────────────────


def _build_index_payload(url: str, title: str, existing_payload: dict | None) -> dict:
    """Build enriched payload for a new or updated index entry."""
    now = _now_iso()
    domain_category = _compute_domain_category(url)

    if existing_payload:
        first_indexed = existing_payload.get("first_indexed_at", now)
        access_count = int(existing_payload.get("access_count", 0))
        last_accessed = existing_payload.get("last_accessed_at", "")
        crawl_count = int(existing_payload.get("crawl_count", 0)) + 1
        # Preserve model metadata if re-indexing
        embedding_model = existing_payload.get("embedding_model", EMBED_MODEL_NAME)
        embedding_dim = int(existing_payload.get("embedding_dim", EMBED_DIM))
        embedding_models_raw = existing_payload.get("embedding_models", "[]")
        try:
            embedding_models = (
                json.loads(embedding_models_raw)
                if isinstance(embedding_models_raw, str)
                else list(embedding_models_raw)
            )
        except (json.JSONDecodeError, TypeError):
            embedding_models = [_named_vector_name(embedding_model)]
    else:
        first_indexed = now
        access_count = 0
        last_accessed = ""
        crawl_count = 1
        embedding_model = EMBED_MODEL_NAME
        embedding_dim = EMBED_DIM
        embedding_models = [_named_vector_name(embedding_model)]

    payload = {
        "url": url,
        "title": title,
        "domain_category": domain_category,
        "first_indexed_at": first_indexed,
        "last_indexed_at": now,
        "crawl_count": crawl_count,
        "access_count": access_count,
        "last_accessed_at": last_accessed,
        "embedding_model": embedding_model,
        "embedding_dim": embedding_dim,
        "embedding_models": json.dumps(embedding_models),
    }

    payload["retention_score"] = _compute_retention_score(payload)
    return payload


# ── Access tracking ───────────────────────────────────────────────


async def _track_access(qdrant, hits: list) -> None:
    """Increment access_count and update last_accessed_at for search results."""
    now = _now_iso()
    try:
        for hit in hits:
            point_id = hit.id
            payload = hit.payload or {}
            current_count = int(payload.get("access_count", 0))
            qdrant.set_payload(
                COLLECTION_NAME,
                points=[point_id],
                payload={
                    "access_count": current_count + 1,
                    "last_accessed_at": now,
                },
            )
        logger.debug("Tracked access for %d search results", len(hits))
    except Exception:
        logger.debug("Failed to track access for search results", exc_info=True)


# ── Index endpoints ───────────────────────────────────────────────


@router_index.post("", response_model=IndexResponse, status_code=201)
async def index_page(body: IndexRequest):
    """Embed and store a page in the persistent vector index.

    Phase 4: Uses named vectors. During dual-write migration,
    indexes with both the active model and the target model.
    """
    if not _models_ready:
        raise HTTPException(
            503, "Models are still loading — please retry in a few seconds"
        )
    qdrant = await _ensure_qdrant()
    model = _get_embed_model()

    point_id = _url_hash(body.url)
    existing_payload = None
    try:
        existing = qdrant.retrieve(
            COLLECTION_NAME,
            ids=[point_id],
            with_payload=True,
            with_vectors=False,
        )
        if existing and existing[0].payload:
            existing_payload = existing[0].payload
    except Exception:
        pass

    # Embed the content
    loop = asyncio.get_event_loop()
    embedding = await loop.run_in_executor(
        None,
        lambda: model.encode(body.content[:2000], normalize_embeddings=True).tolist(),
    )

    # Build enriched payload
    payload = _build_index_payload(body.url, body.title, existing_payload)

    # Build vector dict: at minimum the active named vector
    active_nv = _get_active_model()
    vectors = {active_nv: embedding}

    # During dual-write phase, also embed with the target model
    if _migration["status"] == "dual_write":
        target_name = _migration["target_model"]
        if target_name:
            try:
                target_model = SentenceTransformer(target_name)
                target_embedding = target_model.encode(
                    body.content[:2000], normalize_embeddings=True
                ).tolist()
                target_nv = _named_vector_name(target_name)
                vectors[target_nv] = target_embedding

                # Update embedding_models list
                existing_models = json.loads(payload.get("embedding_models", "[]"))
                for nv in [active_nv, target_nv]:
                    if nv not in existing_models:
                        existing_models.append(nv)
                payload["embedding_models"] = json.dumps(existing_models)
            except Exception as e:
                logger.warning("Dual-write embed failed for target model: %s", e)

    qdrant.upsert(
        collection_name=COLLECTION_NAME,
        points=[
            models.PointStruct(
                id=point_id,
                vector=vectors,
                payload=payload,
            )
        ],
    )

    await _evict_if_needed(qdrant)

    return IndexResponse(status="indexed", url_hash=point_id)


@router_index.post("/batch", response_model=IndexBatchResponse, status_code=201)
async def index_batch(body: IndexBatchRequest):
    """Embed and store multiple pages in a single batch.

    Ref: ADR-0030. For large crawls, replaces N per-page POST /index
    calls with a single batch call. Embeds all content in one
    SentenceTransformer call and upserts via Qdrant gRPC batch.
    Best-effort: failure is logged but never propagated.
    """
    if not _models_ready:
        raise HTTPException(
            503, "Models are still loading — please retry in a few seconds"
        )
    qdrant = await _ensure_qdrant()
    model = _get_embed_model()

    if not body.pages:
        return IndexBatchResponse(status="indexed", count=0)

    # Batch embed all content texts in one call
    contents = [p.content[:2000] for p in body.pages]
    embed_start = time.time()
    loop = asyncio.get_event_loop()
    embeddings = await loop.run_in_executor(
        None,
        lambda: model.encode(contents, normalize_embeddings=True).tolist(),
    )
    embed_duration = time.time() - embed_start
    METRICS.histogram(
        "groktocrawl_index_batch_embed_duration_seconds",
        "Batch embedding inference latency",
    ).observe({"batch_size": str(len(contents))}, embed_duration)

    # Build points with payloads
    active_nv = _get_active_model()
    points = []
    for page, embedding in zip(body.pages, embeddings):
        point_id = _url_hash(page.url)
        existing_payload = None
        try:
            existing = qdrant.retrieve(
                COLLECTION_NAME,
                ids=[point_id],
                with_payload=True,
                with_vectors=False,
            )
            if existing and existing[0].payload:
                existing_payload = existing[0].payload
        except Exception:
            pass

        payload = _build_index_payload(page.url, page.title, existing_payload)
        vectors: dict[str, list[float]] = {active_nv: embedding}

        # Dual-write support: also embed with target model
        if _migration["status"] == "dual_write":
            target_name = _migration["target_model"]
            if target_name:
                try:
                    loop = asyncio.get_event_loop()
                    target_embedding = await loop.run_in_executor(
                        None,
                        lambda: (
                            SentenceTransformer(target_name)
                            .encode(page.content[:2000], normalize_embeddings=True)
                            .tolist()
                        ),
                    )
                    target_nv = _named_vector_name(target_name)
                    vectors[target_nv] = target_embedding
                    existing_models = json.loads(payload.get("embedding_models", "[]"))
                    for nv in [active_nv, target_nv]:
                        if nv not in existing_models:
                            existing_models.append(nv)
                    payload["embedding_models"] = json.dumps(existing_models)
                except Exception as e:
                    logger.warning(
                        "Batch dual-write embed failed for target model: %s", e
                    )

        points.append(
            models.PointStruct(
                id=point_id,
                vector=vectors,
                payload=payload,
            )
        )

    # Single batch upsert via Qdrant gRPC
    qdrant.upsert(collection_name=COLLECTION_NAME, points=points)

    METRICS.counter(
        "groktocrawl_index_batch_pages_total",
        "Total pages indexed via batch endpoint",
    ).inc(value=len(points))

    await _evict_if_needed(qdrant)

    return IndexBatchResponse(status="indexed", count=len(points))


@router_index.delete("/{url_hash}")
async def delete_index(url_hash: int):
    """Remove a page from the vector index by URL hash."""
    qdrant = await _ensure_qdrant()
    qdrant.delete(
        COLLECTION_NAME,
        points_selector=models.PointIdsList(points=[url_hash]),
    )
    return {"status": "deleted"}


@router_index.get("/stats", response_model=IndexStatsResponse)
async def index_stats():
    """Return index size and configuration."""
    qdrant = await _ensure_qdrant()
    count = qdrant.count(COLLECTION_NAME).count
    METRICS.gauge(
        "groktocrawl_index_docs_total", "Current document count in the vector index"
    ).set(value=float(count))
    return IndexStatsResponse(total_docs=count, max_docs=MAX_DOCS)


@router_index.get("/model", response_model=ModelInfoResponse)
async def index_model():
    """Return current embedding model config and migration state."""
    qdrant = await _ensure_qdrant()
    count = qdrant.count(COLLECTION_NAME).count
    return ModelInfoResponse(
        current_model=EMBED_MODEL_NAME,
        current_dim=EMBED_DIM,
        active_named_vector=_get_active_model(),
        collection=COLLECTION_NAME,
        total_docs=count,
        max_docs=MAX_DOCS,
        migration={
            "status": _migration["status"],
            "source_model": _migration["source_model"],
            "target_model": _migration["target_model"],
            "docs_processed": _migration["docs_processed"],
            "docs_total": _migration["docs_total"],
        },
    )
