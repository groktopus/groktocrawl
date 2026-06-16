"""Migration routes for semantic-svc — model migration lifecycle.

Extracted from app.py per ADR-0037.
"""

import json
import logging

from fastapi import APIRouter, HTTPException
from qdrant_client import models
from sentence_transformers import SentenceTransformer

from app import (
    COLLECTION_NAME,
    EMBED_DIM,
    EMBED_MODEL_NAME,
    _ensure_qdrant,
    _migration,
    _named_vector_name,
    _now_iso,
    _set_active_override,
    _set_migration_task,
    app as fastapi_app,
)
from models import MigrationStartRequest, MigrationStatusResponse

logger = logging.getLogger(__name__)

router_migration = APIRouter()


# ── Migration logic ────────────────────────────────────────────────


async def _run_backfill(qdrant, target_name: str, target_dim: int):
    """Background task: scroll all points, re-embed with new model, add named vector.

    This runs as an asyncio task and updates _migration state as it progresses.
    """
    logger.info(
        "Migration backfill started: %s (%d) -> %s (%d)",
        EMBED_MODEL_NAME,
        EMBED_DIM,
        target_name,
        target_dim,
    )

    # Load the target embedding model
    target_model = SentenceTransformer(target_name)

    # Count total docs and scroll
    total = qdrant.count(COLLECTION_NAME).count
    _migration["docs_total"] = total
    _migration["status"] = "backfilling"

    processed = 0
    next_offset: int | None = None
    page_size = 100  # Smaller batch to avoid OOM on large embeddings

    try:
        while True:
            page, next_offset = qdrant.scroll(
                COLLECTION_NAME,
                limit=page_size,
                offset=next_offset,
                with_payload=True,
                with_vectors=False,
            )
            if not page:
                break

            nv_name = _named_vector_name(target_name)
            now = _now_iso()

            for point in page:
                if point.payload is None:
                    continue
                content = (
                    point.payload.get("title", "") + " " + point.payload.get("url", "")
                )
                # If we had original content stored, we'd use that. For backfill,
                # we embed from URL+title as a stopgap. Full re-indexing would
                # re-scrape the source.
                # Embed with the new model
                try:
                    # Use a truncated content signal for backfill embedding
                    signal = f"{point.payload.get('title', '')} {point.payload.get('url', '')}"
                    if not signal.strip():
                        signal = point.payload.get("url", "")
                    embedding = target_model.encode(
                        signal[:2000], normalize_embeddings=True
                    ).tolist()
                except Exception as e:
                    logger.warning(
                        "Backfill embed failed for point %s: %s", point.id, e
                    )
                    continue

                # Update the point: add named vector + update embedding_models list
                em_raw = point.payload.get("embedding_models", "[]")
                if isinstance(em_raw, list):
                    existing_models = em_raw
                else:
                    existing_models = json.loads(em_raw)
                model_short = _named_vector_name(EMBED_MODEL_NAME)
                target_short = nv_name
                if model_short not in existing_models:
                    existing_models.append(model_short)
                if target_short not in existing_models:
                    existing_models.append(target_short)

                qdrant.upsert(
                    COLLECTION_NAME,
                    points=[
                        models.PointStruct(
                            id=point.id,
                            vector={
                                nv_name: embedding,
                            },
                            payload={
                                "embedding_model": EMBED_MODEL_NAME,
                                "embedding_dim": EMBED_DIM,
                                "embedding_models": existing_models,
                            },
                        )
                    ],
                )
                processed += 1
                if processed % 100 == 0:
                    _migration["docs_processed"] = processed
                    logger.info("Migration backfill: %d / %d", processed, total)

            if next_offset is None:
                break

        _migration["docs_processed"] = processed
        _migration["status"] = "dual_write"
        logger.info(
            "Migration backfill complete: %d / %d documents. Entering dual-write phase.",
            processed,
            total,
        )

    except Exception as e:
        _migration["status"] = "idle"
        logger.error("Migration backfill failed: %s", e)
        raise


# ── Migration endpoints ───────────────────────────────────────────


@router_migration.post("/start", status_code=202)
async def migrate_start(body: MigrationStartRequest):
    """Start an embedding model migration (backfill phase).

    Background task scrolls all points, re-embeds with the target
    model, and adds a named vector. Queries continue using the
    active model until cutover.
    """

    if _migration["status"] != "idle":
        raise HTTPException(
            409, f"Migration already in progress: {_migration['status']}"
        )

    qdrant = await _ensure_qdrant()

    target_model_id = body.target_model
    target_dim = body.target_dim

    # Validate target named vector exists on the collection
    target_nv = _named_vector_name(target_model_id)
    try:
        collection_info = qdrant.get_collection(COLLECTION_NAME)
        vectors_config = collection_info.config.params.vectors
        if hasattr(vectors_config, 'get') and target_nv not in vectors_config:
            raise HTTPException(
                400,
                f"Target named vector '{target_nv}' is not configured on the collection. "
                "Named vectors cannot be added post-creation — recreate the collection "
                "with the target model's named vector first.",
            )
    except AttributeError:
        # Single-vector collection — can't migrate to named vectors
        raise HTTPException(
            400,
            "Collection uses single-vector mode. Recreate with named vectors enabled "
            "before running migration.",
        )

    _migration.clear()
    _migration.update({
        "status": "backfilling",
        "source_model": EMBED_MODEL_NAME,
        "source_dim": EMBED_DIM,
        "target_model": target_model_id,
        "target_dim": target_dim,
        "docs_processed": 0,
        "docs_total": 0,
        "started_at": _now_iso(),
        "completed_at": "",
    })

    _set_migration_task(
        fastapi_app.state.task_tracker.create_background_task(
            _run_backfill(qdrant, target_model_id, target_dim)
        )
    )

    return {
        "status": "accepted",
        "message": f"Backfill started: {EMBED_MODEL_NAME} -> {target_model_id}",
    }


@router_migration.get("/status", response_model=MigrationStatusResponse)
async def migrate_status():
    """Return migration progress."""
    return MigrationStatusResponse(
        status=_migration["status"],
        source_model=_migration["source_model"],
        source_dim=_migration["source_dim"],
        target_model=_migration["target_model"],
        target_dim=_migration["target_dim"],
        docs_processed=_migration["docs_processed"],
        docs_total=_migration["docs_total"],
        started_at=_migration["started_at"],
        completed_at=_migration["completed_at"],
    )


@router_migration.post("/cutover")
async def migrate_cutover():
    """Switch queries to the new model.

    The in-memory active model override flips to the target model's
    named vector. On container restart, reverts to ACTIVE_EMBED_MODEL
    env var. For permanent cutover, update the env var and restart.
    """

    if _migration["status"] not in ("dual_write", "backfilling"):
        raise HTTPException(
            409, f"Cannot cutover: migration status is '{_migration['status']}'"
        )

    if not _migration["target_model"]:
        raise HTTPException(400, "No target model configured")

    target_nv = _named_vector_name(_migration["target_model"])
    _set_active_override(target_nv)
    _migration["status"] = "cutover"
    _migration["completed_at"] = _now_iso()

    logger.info(
        "Migration cutover: active model switched to '%s' (named vector: '%s')",
        _migration["target_model"],
        target_nv,
    )

    return {
        "status": "cutover",
        "active_named_vector": target_nv,
        "message": f"Queries now using '{target_nv}'. "
        f"Update ACTIVE_EMBED_MODEL env var and restart to persist.",
    }
