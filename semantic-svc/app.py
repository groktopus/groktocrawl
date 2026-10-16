"""Semantic search service — embedding, reranking, and vector index.

Phase 1 endpoints (existing):
- POST /embed — vectorize query and document texts via BGE-M3
- POST /rerank — cross-encode query against documents via BGE-reranker-v2-m3

Phase 2 endpoints:
- POST /index — embed and store a page in the persistent vector index
- POST /search/vector — query the vector index by semantic similarity
- DELETE /index/{url_hash} — remove a page from the index
- GET /index/stats — index size and configuration

Phase 3 endpoints:
- Retention scoring, domain classification, access tracking (see ADR-0027)

Phase 4 endpoints (this file):
- GET /index/model — current embedding model config and migration state
- POST /index/migrate/start — start embedding model migration
- GET /index/migrate/status — migration progress
- POST /index/migrate/cutover — switch to new model
|- Named vector support for multi-model coexistence (see ADR-0028)
|
|Observability (ADR-0029):
|- GET /metrics — Prometheus-compatible OpenMetrics endpoint (stdlib, no deps)
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime
import hashlib
import logging
import math
import os
import time
from collections.abc import Callable
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

import inference as inference_module
import numpy as np
from auth import verify_api_key
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from inference import (
    InferenceManager,
    InferenceOverloaded,
    run_inference,
)
from metrics import METRICS
from models import (
    EmbedRequest,
    EmbedResponse,
    RerankRequest,
    RerankResponse,
    RerankResult,
)
from qdrant_client import QdrantClient, models
from shadow_pgvector import PgvectorShadowStore, ShadowConfig

if TYPE_CHECKING:
    from sentence_transformers import CrossEncoder, SentenceTransformer

from common.logging import setup_logging
from common.middleware import add_request_id_middleware

setup_logging()
logger = logging.getLogger(__name__)
_inference_manager = inference_module._inference_manager


def _load_sentence_transformers():
    """Import model classes only when semantic inference is used."""
    from sentence_transformers import CrossEncoder, SentenceTransformer

    return CrossEncoder, SentenceTransformer


# ── TaskTracker (copied from agent-svc; avoids cross-service import) ──


class TaskTracker:
    """Tracks background tasks for graceful shutdown."""

    def __init__(self) -> None:
        self._tasks: set[asyncio.Task] = set()
        self._shutdown_event = asyncio.Event()

    def create_background_task(self, coro) -> asyncio.Task:
        """Create, track, and return a background task."""
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    @property
    def shutdown_requested(self) -> bool:
        return self._shutdown_event.is_set()

    async def shutdown(self, grace_period: float = 5.0) -> None:
        """Signal shutdown, cancel tracked tasks after grace period."""
        self._shutdown_event.set()
        if not self._tasks:
            return

        logger.info(
            "Shutting down %d background tasks (grace=%ss)",
            len(self._tasks),
            grace_period,
        )

        _, pending = await asyncio.wait(self._tasks, timeout=grace_period)

        if pending:
            logger.warning(
                "Cancelling %d tasks after %ss grace period",
                len(pending),
                grace_period,
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load SentenceTransformer and CrossEncoder models at startup."""
    global _embed_model, _rerank_model, _models_ready, _inference_manager
    app.state.task_tracker = TaskTracker()
    # A fresh manager is tied to this event loop. Its workers drain native
    # calls before the executor is closed during shutdown.
    _inference_manager = InferenceManager()
    inference_module._inference_manager = _inference_manager
    app.state.inference_manager = _inference_manager
    logger.info("Loading semantic models (~2.2GB, 2-5s)...")
    loop = asyncio.get_event_loop()
    try:
        cross_encoder_cls, sentence_transformer_cls = _load_sentence_transformers()
        _embed_model = await loop.run_in_executor(
            None, lambda: sentence_transformer_cls(EMBED_MODEL_NAME)
        )
        _rerank_model = await loop.run_in_executor(
            None, lambda: cross_encoder_cls(RERANK_MODEL_NAME)
        )
        _models_ready = True
        logger.info("Models loaded — semantic-svc ready")
    except Exception:
        logger.exception(
            "Failed to load semantic models — /health will report 'starting'"
        )
    yield
    await app.state.task_tracker.shutdown(grace_period=5.0)
    _models_ready = False
    _embed_model = None
    _rerank_model = None
    await _inference_manager.shutdown()
    _shadow_store.close()


app = FastAPI(title="semantic-svc", lifespan=lifespan)


@app.exception_handler(InferenceOverloaded)
async def inference_overloaded_handler(
    request: Request, exc: InferenceOverloaded
) -> JSONResponse:
    """Return a retryable overload response without exposing model internals."""
    del request
    return JSONResponse(
        status_code=503,
        content={
            "detail": "Semantic inference capacity is temporarily unavailable",
            "error": "inference_overloaded",
            "operation": exc.operation,
        },
        headers={"Retry-After": str(max(1, math.ceil(exc.retry_after)))},
    )


# ── Instrumentation ──────────────────────────────────────────
add_request_id_middleware(
    app,
    record_metric=lambda labels, val: METRICS.histogram(
        "http_request_duration_seconds",
        "HTTP request latency by path and method",
        ["method", "path"],
    ).observe(labels, val),
)

# ── Model config ──────────────────────────────────────────────────
# Configurable via env vars so embedding models can be swapped
# without code changes.
EMBED_MODEL_NAME = os.getenv("EMBED_MODEL_NAME", "BAAI/bge-m3")
EMBED_DIM = int(os.getenv("EMBED_DIM", "1024"))
RERANK_MODEL_NAME = "BAAI/bge-reranker-v2-m3"

# Active named vector: which model produces vectors for indexing
# and queries. Must match a named vector in the Qdrant collection.
# Named vector convention: v_{model_short} (e.g., v_bge-m3, v_bge-m4)
ACTIVE_EMBED_MODEL = os.getenv("ACTIVE_EMBED_MODEL", "bge-m3")

_embed_model: SentenceTransformer | None = None
_rerank_model: CrossEncoder | None = None
_models_ready: bool = False
_qdrant: QdrantClient | None = None
_qdrant_ready: bool = False

COLLECTION_NAME = "groktocrawl_pages"
MAX_DOCS = int(os.getenv("VECTOR_INDEX_MAX_DOCS", "250000"))
QDRANT_URL = os.getenv("QDRANT_URL", "http://qdrant:6333")
QDRANT_QUERY_TIMEOUT = float(os.getenv("QDRANT_QUERY_TIMEOUT", "10"))
# Client-side timeout for the blocking Qdrant HTTP calls. Defaults to
# QDRANT_QUERY_TIMEOUT so the asyncio.wait_for wrapper in router_search
# stays the binding bound — a lower hardcoded client timeout (the old 5s)
# would fire first and make slow-but-healthy indexes unreachable (issue #588).
# Rounded UP (ceil) to satisfy qdrant-client's int-typed timeout without ever
# landing below the fractional wrapper timeout (int() truncation would).
QDRANT_CLIENT_TIMEOUT = math.ceil(
    float(os.getenv("QDRANT_CLIENT_TIMEOUT", str(QDRANT_QUERY_TIMEOUT)))
)
SHADOW_CONFIG = ShadowConfig.from_env()
_shadow_store = PgvectorShadowStore(SHADOW_CONFIG, EMBED_DIM)


async def _run_shadow_operation(operation: str, function: Callable[[], Any]) -> Any:
    """Run a bounded shadow operation without changing Qdrant outcomes."""
    if not SHADOW_CONFIG.enabled:
        return None
    outcome = "success"
    try:

        def execute() -> Any:
            _shadow_store.ensure_schema()
            return function()

        return await asyncio.wait_for(
            asyncio.to_thread(execute), timeout=SHADOW_CONFIG.timeout_seconds
        )
    except Exception:
        outcome = "failure"
        logger.warning("pgvector shadow %s failed", operation, exc_info=True)
        return None
    finally:
        METRICS.counter(
            "groktocrawl_pgvector_shadow_operations_total",
            "Pgvector shadow operations by type and outcome",
            ["operation", "outcome"],
        ).inc({"operation": operation, "outcome": outcome})


async def _run_required_pgvector_operation(
    operation: str, function: Callable[[], Any]
) -> Any:
    """Run a bounded pgvector serving operation or return a stable 503."""
    outcome = "success"
    try:

        def execute() -> Any:
            _shadow_store.ensure_schema()
            return function()

        return await asyncio.wait_for(
            asyncio.to_thread(execute), timeout=SHADOW_CONFIG.timeout_seconds
        )
    except Exception:
        outcome = "failure"
        logger.exception("pgvector serving %s failed", operation)
        raise HTTPException(503, "Vector index unavailable")
    finally:
        METRICS.counter(
            "groktocrawl_pgvector_serving_operations_total",
            "Pgvector serving operations by type and outcome",
            ["operation", "outcome"],
        ).inc({"operation": operation, "outcome": outcome})


def _schedule_shadow_operation(operation: str, function: Callable[[], Any]) -> None:
    """Schedule fail-open shadow work under the service task tracker."""
    if not SHADOW_CONFIG.enabled:
        return
    scheduled_at = time.monotonic()
    METRICS.counter(
        "groktocrawl_pgvector_shadow_scheduled_total",
        "Pgvector shadow operations scheduled after authoritative completion",
        ["operation"],
    ).inc({"operation": operation})
    METRICS.gauge(
        "groktocrawl_pgvector_shadow_inflight",
        "Pgvector shadow operations waiting or running",
        ["operation"],
    ).inc({"operation": operation})
    app.state.task_tracker.create_background_task(
        _run_scheduled_shadow_operation(operation, function, scheduled_at)
    )


async def _run_scheduled_shadow_operation(
    operation: str, function: Callable[[], Any], scheduled_at: float
) -> Any:
    """Measure authority-to-shadow completion lag without retaining content."""
    try:
        return await _run_shadow_operation(operation, function)
    finally:
        elapsed = time.monotonic() - scheduled_at
        METRICS.histogram(
            "groktocrawl_pgvector_shadow_completion_lag_seconds",
            "Time from authoritative completion until a shadow attempt finishes",
            ["operation"],
        ).observe({"operation": operation}, elapsed)
        METRICS.counter(
            "groktocrawl_pgvector_shadow_completed_total",
            "Pgvector shadow attempts completed after scheduling",
            ["operation"],
        ).inc({"operation": operation})
        METRICS.gauge(
            "groktocrawl_pgvector_shadow_inflight",
            "Pgvector shadow operations waiting or running",
            ["operation"],
        ).dec({"operation": operation})


def _create_background_task(coro) -> asyncio.Task:
    """Track route background work when the application lifespan is active."""
    tracker = getattr(app.state, "task_tracker", None)
    if tracker is not None:
        return tracker.create_background_task(coro)
    return asyncio.create_task(coro)


# ── Migration state (in-memory, lost on restart) ──────────────────
# For restart-surviving state, store in Valkey or a known Qdrant point.
_migration = {
    "status": "idle",  # idle | backfilling | dual_write | cutover | complete
    "source_model": EMBED_MODEL_NAME,
    "source_dim": EMBED_DIM,
    "target_model": "",
    "target_dim": 0,
    "docs_processed": 0,
    "docs_total": 0,
    "started_at": "",
    "completed_at": "",
}
_migration_task: asyncio.Task | None = None

# In-memory override for active model (set by /migrate/cutover;
# resets to ACTIVE_EMBED_MODEL env var on restart).
_active_override: str | None = None


def _get_active_model() -> str:
    """Return the effective active named vector.

    Prefers the in-memory override (set by cutover), falling back
    to the ACTIVE_EMBED_MODEL env var.
    """
    return _active_override if _active_override is not None else ACTIVE_EMBED_MODEL


def _set_active_override(val: str | None) -> None:
    """Set the in-memory active model override (used by migration cutover)."""
    global _active_override
    _active_override = val


# ── Target model cache for migration dual-write ────────────────────
# Cached globally so that batch dual-write does not instantiate a new
# SentenceTransformer per page (see fix-semantic-hardening).

_target_embed_model: SentenceTransformer | None = None
_target_embed_model_name: str = ""


async def _get_target_embed_model(target_name: str) -> SentenceTransformer:
    """Get or load the target embedding model for migration dual-write.

    Caches the model globally so that batch dual-write (which can process
    hundreds of pages in a single request) does not re-instantiate the
    SentenceTransformer for each page.
    """
    global _target_embed_model, _target_embed_model_name
    if _target_embed_model is None or _target_embed_model_name != target_name:
        _, sentence_transformer_cls = _load_sentence_transformers()
        loop = asyncio.get_event_loop()
        _target_embed_model = await loop.run_in_executor(
            None, lambda: sentence_transformer_cls(target_name)
        )
        _target_embed_model_name = target_name
    return _target_embed_model


def _set_migration_task(task: asyncio.Task | None) -> None:
    """Set the migration background task reference."""
    global _migration_task
    _migration_task = task


# ── Model helpers ─────────────────────────────────────────────────


def _get_embed_model() -> SentenceTransformer:
    return _embed_model


def _get_rerank_model() -> CrossEncoder:
    return _rerank_model


def _url_hash(url: str) -> int:
    """Deterministic point ID from URL — first 64 bits of SHA-256 as uint64."""
    h = hashlib.sha256(url.encode()).hexdigest()
    return int(h[:16], 16)


def _is_qdrant_ready() -> bool:
    """Return whether Qdrant is reachable without initializing the collection."""
    client = _qdrant
    temporary_client = client is None
    if temporary_client:
        client = QdrantClient(url=QDRANT_URL, timeout=QDRANT_CLIENT_TIMEOUT)
    assert client is not None
    try:
        client.get_collections()
    except Exception:
        return False
    finally:
        if temporary_client:
            with contextlib.suppress(Exception):
                client.close()
    return True


def _is_pgvector_ready() -> bool:
    """Return whether the configured pgvector store is reachable."""
    try:
        _shadow_store.ensure_schema()
        _shadow_store.count(model=_get_active_model())
    except Exception:
        return False
    return True


def _named_vector_name(model_name: str) -> str:
    """Short name for a named vector (e.g., 'BAAI/bge-m3' -> 'v_bge-m3')."""
    short = model_name.split("/")[-1].lower()
    # Replace non-alphanumeric chars with hyphens for Qdrant compatibility
    short = "".join(c if c.isalnum() else "-" for c in short).strip("-")
    return f"v_{short}"


def _now_iso() -> str:
    """Return current UTC timestamp as ISO 8601 string."""
    return datetime.datetime.now(datetime.UTC).isoformat()


async def _ensure_qdrant() -> QdrantClient:
    """Lazy-init Qdrant client and collection with named vector support."""
    global _qdrant, _qdrant_ready
    if _qdrant is None:
        _qdrant = QdrantClient(url=QDRANT_URL, timeout=QDRANT_CLIENT_TIMEOUT)
    if not _qdrant_ready:
        try:
            collections = _qdrant.get_collections()
            if COLLECTION_NAME not in [c.name for c in collections.collections]:
                # Create collection with a single named vector for the active model
                nv_name = _named_vector_name(EMBED_MODEL_NAME)
                _qdrant.create_collection(
                    collection_name=COLLECTION_NAME,
                    vectors_config={
                        nv_name: models.VectorParams(
                            size=EMBED_DIM,
                            distance=models.Distance.COSINE,
                        ),
                    },
                )
                logger.info(
                    "Created Qdrant collection '%s' with named vector '%s' (dim=%d)",
                    COLLECTION_NAME,
                    nv_name,
                    EMBED_DIM,
                )
            else:
                # Validate that the active named vector exists in the collection
                info = _qdrant.get_collection(COLLECTION_NAME)
                configured_vectors = info.config.params.vectors
                if isinstance(configured_vectors, dict):
                    nv_name = _named_vector_name(EMBED_MODEL_NAME)
                    if nv_name not in configured_vectors:
                        # Legacy collection (no named vectors) — migrate
                        logger.info(
                            "Legacy collection detected (no named vectors). "
                            "Migrating... deleting and recreating '%s' with named vector '%s'.",
                            COLLECTION_NAME,
                            nv_name,
                        )
                        # Qdrant cannot add named vectors post-creation. Delete and recreate.
                        _qdrant.delete_collection(COLLECTION_NAME)
                        _qdrant.create_collection(
                            collection_name=COLLECTION_NAME,
                            vectors_config={
                                nv_name: models.VectorParams(
                                    size=EMBED_DIM,
                                    distance=models.Distance.COSINE,
                                ),
                            },
                        )
                        logger.info(
                            "Recreated '%s' with named vector '%s'",
                            COLLECTION_NAME,
                            nv_name,
                        )
                elif not isinstance(configured_vectors, dict):
                    # Flat vector config — migrate to named vectors
                    nv_name = _named_vector_name(EMBED_MODEL_NAME)
                    logger.info(
                        "Legacy flat-vector collection detected. "
                        "Migrating to named vector '%s'...",
                        nv_name,
                    )
                    _qdrant.delete_collection(COLLECTION_NAME)
                    _qdrant.create_collection(
                        collection_name=COLLECTION_NAME,
                        vectors_config={
                            nv_name: models.VectorParams(
                                size=EMBED_DIM,
                                distance=models.Distance.COSINE,
                            ),
                        },
                    )
                    logger.info(
                        "Recreated '%s' with named vector '%s'",
                        COLLECTION_NAME,
                        nv_name,
                    )
            _qdrant_ready = True
        except Exception as e:
            logger.error("Qdrant init failed: %s", e)
            raise HTTPException(503, "Vector index unavailable")
    return _qdrant


# ── Metrics middleware ──────────────────────────────────────────────


@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    """Record request count and duration for all endpoints except /metrics."""
    path = request.url.path
    if path == "/metrics":
        return await call_next(request)

    start = time.time()
    try:
        response = await call_next(request)
        return response
    finally:
        duration = time.time() - start
        METRICS.counter(
            "groktocrawl_search_requests_total",
            "Total requests by endpoint",
            ["endpoint"],
        ).inc({"endpoint": path})
        METRICS.histogram(
            "groktocrawl_index_query_duration_seconds",
            "Request latency by endpoint",
            ["endpoint"],
        ).observe({"endpoint": path}, duration)


# ── Core endpoints ──────────────────────────────────────────────────


@app.get("/health")
async def health() -> dict[str, str]:
    """Report liveness and component detail without failing legacy probes."""
    return await _readiness_payload()


async def _readiness_payload() -> dict[str, str]:
    if not _models_ready:
        return {"status": "starting", "models": "loading"}

    loop = asyncio.get_running_loop()
    if SHADOW_CONFIG.serving:
        pgvector_ready, qdrant_ready = await asyncio.gather(
            loop.run_in_executor(None, _is_pgvector_ready),
            loop.run_in_executor(None, _is_qdrant_ready),
        )
        return {
            "status": "ok" if pgvector_ready else "starting",
            "models": "loaded",
            "vector_store": "pgvector",
            "pgvector": "ready" if pgvector_ready else "unavailable",
            "qdrant": "rollback_ready" if qdrant_ready else "unavailable",
        }
    qdrant_ready = await loop.run_in_executor(None, _is_qdrant_ready)
    return {
        "status": "ok" if qdrant_ready else "starting",
        "models": "loaded",
        "qdrant": "ready" if qdrant_ready else "unavailable",
    }


@app.get("/ready")
async def readiness() -> JSONResponse:
    """Return a failing HTTP status while the active serving path is unavailable."""
    payload = await _readiness_payload()
    return JSONResponse(payload, status_code=200 if payload["status"] == "ok" else 503)


@app.post("/embed", response_model=EmbedResponse)
async def embed(body: EmbedRequest):
    """Embed one or more texts into normalized vectors."""
    if not _models_ready:
        raise HTTPException(
            503, "Models are still loading — please retry in a few seconds"
        )
    model = _get_embed_model()
    embed_start = time.time()
    embeddings = await run_inference(
        "embed",
        lambda: model.encode(body.input, normalize_embeddings=True),
    )
    embeddings_list = embeddings.tolist()
    embed_duration = time.time() - embed_start
    METRICS.histogram(
        "groktocrawl_index_embeddings_duration_seconds",
        "Embedding model inference latency",
    ).observe({}, embed_duration)
    return EmbedResponse(embeddings=embeddings_list)


@app.post("/rerank", response_model=RerankResponse)
async def rerank(body: RerankRequest):
    """Cross-encode a query against documents, returning top-k."""
    if not _models_ready:
        raise HTTPException(
            503, "Models are still loading — please retry in a few seconds"
        )
    model = _get_rerank_model()
    pairs = [[body.query, doc] for doc in body.documents]
    scores = await run_inference("rerank", lambda: model.predict(pairs))
    indices = np.argsort(scores)[::-1][: body.top_k]
    results = [RerankResult(index=int(i), score=float(scores[i])) for i in indices]
    return RerankResponse(results=results)


# ── Metrics endpoint ──────────────────────────────────────────────


@app.get("/metrics")
async def metrics():
    """Expose OpenMetrics-formatted metrics for Prometheus scraping.

    Uses the same stdlib-based metrics collector from agent-svc (see
    ADR-0018 / ADR-0029) — no external metrics library required.
    """
    text = METRICS.generate_openmetrics()
    return Response(
        content=text,
        media_type="application/openmetrics-text; version=1.0.0",
    )


# ── Router includes ─────────────────────────────────────────────────
# Import routers at module bottom to avoid circular imports.
# All module-level symbols needed by routers are defined above.

from router_index import router_index
from router_migration import router_migration
from router_search import router_search

app.include_router(router_index, prefix="/index")
app.include_router(router_search, prefix="/search")
app.include_router(
    router_migration,
    prefix="/index/migrate",
    dependencies=[Depends(verify_api_key)],
)
