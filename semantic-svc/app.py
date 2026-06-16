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

import asyncio
import datetime
import hashlib
import json
import logging
import math
import os
import time
import urllib.parse
from contextlib import asynccontextmanager

import numpy as np
from fastapi import FastAPI, HTTPException, Request, Response
from metrics import METRICS
from pydantic import BaseModel
from qdrant_client import QdrantClient, models
from sentence_transformers import CrossEncoder, SentenceTransformer

logger = logging.getLogger(__name__)


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
    global _embed_model, _rerank_model, _models_ready
    app.state.task_tracker = TaskTracker()
    logger.info("Loading semantic models (~2.2GB, 2-5s)...")
    loop = asyncio.get_event_loop()
    try:
        _embed_model = await loop.run_in_executor(
            None, lambda: SentenceTransformer(EMBED_MODEL_NAME)
        )
        _rerank_model = await loop.run_in_executor(
            None, lambda: CrossEncoder(RERANK_MODEL_NAME)
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


app = FastAPI(title="semantic-svc", lifespan=lifespan)

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


# ── Domain classification ─────────────────────────────────────────

# Domain patterns for retention categories (unchanged from Phase 3)
_DOCS_DOMAINS = {
    "readthedocs.io",
    "readthedocs.org",
}
_REFERENCE_DOMAINS = {
    "wikipedia.org",
    "stackoverflow.com",
    "stackexchange.com",
    "github.com",
    "gitlab.com",
}
_NEWS_DOMAINS = {
    "reuters.com",
    "nytimes.com",
    "cnn.com",
    "bbc.com",
    "bbc.co.uk",
    "bloomberg.com",
    "apnews.com",
    "npr.org",
    "theguardian.com",
    "wsj.com",
    "washingtonpost.com",
    "economist.com",
    "cnbc.com",
    "abcnews.go.com",
    "cbsnews.com",
    "nbcnews.com",
    "usatoday.com",
    "politico.com",
    "axios.com",
    "thehill.com",
}
_SOCIAL_DOMAINS = {
    "reddit.com",
    "twitter.com",
    "x.com",
    "youtube.com",
    "instagram.com",
    "tiktok.com",
    "threads.net",
    "bluesky",
    "bsky.app",
}
_BLOG_DOMAINS = {
    "medium.com",
    "substack.com",
    "ghost.io",
    "wordpress.com",
    "blogger.com",
    "blogspot.com",
}


def _compute_domain_category(url: str) -> str:
    """Classify a URL's domain into a retention category.

    Categories (in eviction-priority order):
        news (0.3), social (0.4), blog (0.6), api (0.7),
        unknown (0.8), reference (1.0), docs (1.2)
    """
    netloc = urllib.parse.urlparse(url).netloc.lower()

    if not netloc:
        return "unknown"

    # Strip leading www. for consistent matching
    netloc = netloc.removeprefix("www.")

    # Prefix-based: docs./learn./help./api./developer.
    if netloc.startswith(("docs.", "learn.", "help.")):
        return "docs"
    if netloc.startswith(("api.", "developer.")):
        return "api"
    if netloc.startswith("blog."):
        return "blog"

    # Substring-based matches
    for d in _DOCS_DOMAINS:
        if d in netloc:
            return "docs"
    for d in _REFERENCE_DOMAINS:
        if d in netloc:
            return "reference"
    for d in _NEWS_DOMAINS:
        if d in netloc:
            return "news"
    for d in _SOCIAL_DOMAINS:
        if d in netloc:
            return "social"
    for d in _BLOG_DOMAINS:
        if d in netloc:
            return "blog"

    return "unknown"


# ── Retention scoring ─────────────────────────────────────────────

_DOMAIN_MULTIPLIERS = {
    "news": 0.3,
    "social": 0.4,
    "blog": 0.6,
    "api": 0.7,
    "unknown": 0.8,
    "reference": 1.0,
    "docs": 1.2,
}

_RECENCY_HALF_LIFE_DAYS = 90.0


def _compute_retention_score(payload: dict) -> float:
    """Compute retention score from a Qdrant point's payload."""
    category = payload.get("domain_category", "unknown")
    domain_mult = _DOMAIN_MULTIPLIERS.get(category, 0.8)

    raw_date = payload.get("last_indexed_at", "")
    if raw_date:
        try:
            last_indexed = datetime.datetime.fromisoformat(raw_date)
            days_since = (datetime.datetime.now(datetime.UTC) - last_indexed).days
            days_since = max(0, days_since)
            recency_factor = math.exp(-days_since / _RECENCY_HALF_LIFE_DAYS)
            recency_factor = max(0.1, min(1.0, recency_factor))
        except (ValueError, TypeError):
            recency_factor = 0.5
    else:
        recency_factor = 0.5

    access_count = payload.get("access_count", 0)
    access_boost = min(int(access_count), 100) * 0.01

    crawl_count = payload.get("crawl_count", 0)
    crawl_boost = min(int(crawl_count), 20) * 0.05

    return round(domain_mult * recency_factor + access_boost + crawl_boost, 4)


# ── Model helpers ─────────────────────────────────────────────────


def _get_embed_model() -> SentenceTransformer:
    return _embed_model


def _get_rerank_model() -> CrossEncoder:
    return _rerank_model


def _url_hash(url: str) -> int:
    """Deterministic point ID from URL — first 64 bits of SHA-256 as uint64."""
    h = hashlib.sha256(url.encode()).hexdigest()
    return int(h[:16], 16)


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
        _qdrant = QdrantClient(url=QDRANT_URL)
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


# ── Scoring-based eviction (Phase 3) ──────────────────────────────


async def _evict_if_needed(qdrant: QdrantClient):
    """Scoring-based eviction if the index exceeds MAX_DOCS."""
    count = qdrant.count(COLLECTION_NAME).count
    if count <= MAX_DOCS:
        return

    excess = count - MAX_DOCS
    target_delete = excess + max(100, int(MAX_DOCS * 0.02))

    logger.info(
        "Index over capacity (%d / %d), scoring eviction candidates...",
        count,
        MAX_DOCS,
    )

    scored_points: list[tuple[float, int]] = []
    next_offset: int | None = None
    page_size = 2000

    while len(scored_points) < target_delete or next_offset is not None:
        page, next_offset = qdrant.scroll(
            COLLECTION_NAME,
            limit=page_size,
            offset=next_offset,
            with_payload=True,
            with_vectors=False,
        )
        if not page:
            break
        for point in page:
            if point.payload is None:
                continue
            score = _compute_retention_score(point.payload)
            scored_points.append((score, point.id))

        if next_offset is None:
            break

    scored_points.sort(key=lambda x: x[0])

    to_delete = [pid for _, pid in scored_points[:target_delete]]

    if to_delete:
        qdrant.delete(
            COLLECTION_NAME,
            points_selector=models.PointIdsList(points=to_delete),
        )
        new_count = qdrant.count(COLLECTION_NAME).count
        METRICS.counter(
            "groktocrawl_index_evictions_total",
            "Cumulative evictions from the vector index",
        ).inc(value=len(to_delete))
        logger.info(
            "Scored eviction: removed %d documents (score range: %.4f – %.4f). "
            "Index at %d / %d",
            len(to_delete),
            scored_points[0][0] if scored_points else 0,
            scored_points[min(len(scored_points), target_delete) - 1][0]
            if scored_points
            else 0,
            new_count,
            MAX_DOCS,
        )
    else:
        logger.info("No eviction candidates found")


# ── Migration logic ────────────────────────────────────────────────


async def _run_backfill(qdrant: QdrantClient, target_name: str, target_dim: int):
    """Background task: scroll all points, re-embed with new model, add named vector.

    This runs as an asyncio task and updates _migration state as it progresses.
    """
    global _migration
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
                existing_models = json.loads(
                    point.payload.get("embedding_models", "[]")
                )
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


# ── Request/Response models ──────────────────────────────────────


class EmbedRequest(BaseModel):
    model: str = "BGE-M3"
    input: list[str]


class EmbedResponse(BaseModel):
    embeddings: list[list[float]]


class RerankRequest(BaseModel):
    query: str
    documents: list[str]
    top_k: int = 5


class RerankResult(BaseModel):
    index: int
    score: float


class RerankResponse(BaseModel):
    results: list[RerankResult]


class IndexRequest(BaseModel):
    url: str
    title: str = ""
    content: str


class IndexResponse(BaseModel):
    status: str
    url_hash: int


class IndexBatchRequest(BaseModel):
    pages: list[IndexRequest]


class IndexBatchResponse(BaseModel):
    status: str
    count: int


class VectorSearchRequest(BaseModel):
    query: str
    limit: int = 5


class VectorSearchResult(BaseModel):
    url: str
    title: str
    score: float


class VectorSearchResponse(BaseModel):
    results: list[VectorSearchResult]


class IndexStatsResponse(BaseModel):
    total_docs: int
    max_docs: int


class ModelInfoResponse(BaseModel):
    current_model: str
    current_dim: int
    active_named_vector: str
    collection: str
    total_docs: int
    max_docs: int
    migration: dict


class MigrationStartRequest(BaseModel):
    target_model: str
    target_dim: int


class MigrationStatusResponse(BaseModel):
    status: str
    source_model: str
    source_dim: int
    target_model: str
    target_dim: int
    docs_processed: int
    docs_total: int
    started_at: str
    completed_at: str


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


# ── Endpoints ────────────────────────────────────────────────────


@app.get("/health")
async def health():
    return {
        "status": "ok" if _models_ready else "starting",
        "models": "loaded" if _models_ready else "loading",
    }


@app.post("/embed", response_model=EmbedResponse)
async def embed(body: EmbedRequest):
    """Embed one or more texts into normalized vectors."""
    if not _models_ready:
        raise HTTPException(
            503, "Models are still loading — please retry in a few seconds"
        )
    model = _get_embed_model()
    embed_start = time.time()
    loop = asyncio.get_event_loop()
    embeddings = await loop.run_in_executor(
        None,
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
    scores = model.predict(pairs)
    indices = np.argsort(scores)[::-1][: body.top_k]
    results = [RerankResult(index=int(i), score=float(scores[i])) for i in indices]
    return RerankResponse(results=results)


# ── Phase 2/3/4: Vector Index ────────────────────────────────────


@app.post("/index", response_model=IndexResponse, status_code=201)
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
        logger.warning(
            "Qdrant lookup failed during index for %s — proceeding as new page",
            body.url,
            exc_info=True,
        )

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


@app.post("/index/batch", response_model=IndexBatchResponse, status_code=201)
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


@app.post("/search/vector", response_model=VectorSearchResponse)
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


async def _track_access(qdrant: QdrantClient, hits: list) -> None:
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


@app.delete("/index/{url_hash}")
async def delete_index(url_hash: int):
    """Remove a page from the vector index by URL hash."""
    qdrant = await _ensure_qdrant()
    qdrant.delete(
        COLLECTION_NAME,
        points_selector=models.PointIdsList(points=[url_hash]),
    )
    return {"status": "deleted"}


@app.get("/index/stats", response_model=IndexStatsResponse)
async def index_stats():
    """Return index size and configuration."""
    qdrant = await _ensure_qdrant()
    count = qdrant.count(COLLECTION_NAME).count
    METRICS.gauge(
        "groktocrawl_index_docs_total", "Current document count in the vector index"
    ).set(value=float(count))
    return IndexStatsResponse(total_docs=count, max_docs=MAX_DOCS)


# ── Phase 4: Model info and migration endpoints ──────────────────


@app.get("/index/model", response_model=ModelInfoResponse)
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


@app.post("/index/migrate/start", status_code=202)
async def migrate_start(body: MigrationStartRequest):
    """Start an embedding model migration (backfill phase).

    Background task scrolls all points, re-embeds with the target
    model, and adds a named vector. Queries continue using the
    active model until cutover.
    """
    global _migration_task, _migration

    if _migration["status"] != "idle":
        raise HTTPException(
            409, f"Migration already in progress: {_migration['status']}"
        )

    qdrant = await _ensure_qdrant()

    target_model_id = body.target_model
    target_dim = body.target_dim

    _migration = {
        "status": "backfilling",
        "source_model": EMBED_MODEL_NAME,
        "source_dim": EMBED_DIM,
        "target_model": target_model_id,
        "target_dim": target_dim,
        "docs_processed": 0,
        "docs_total": 0,
        "started_at": _now_iso(),
        "completed_at": "",
    }

    _migration_task = app.state.task_tracker.create_background_task(
        _run_backfill(qdrant, target_model_id, target_dim)
    )

    return {
        "status": "accepted",
        "message": f"Backfill started: {EMBED_MODEL_NAME} -> {target_model_id}",
    }


@app.get("/index/migrate/status", response_model=MigrationStatusResponse)
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


@app.post("/index/migrate/cutover")
async def migrate_cutover():
    """Switch queries to the new model.

    The in-memory active model override flips to the target model's
    named vector. On container restart, reverts to ACTIVE_EMBED_MODEL
    env var. For permanent cutover, update the env var and restart.
    """
    global _active_override

    if _migration["status"] not in ("dual_write", "backfilling"):
        raise HTTPException(
            409, f"Cannot cutover: migration status is '{_migration['status']}'"
        )

    if not _migration["target_model"]:
        raise HTTPException(400, "No target model configured")

    target_nv = _named_vector_name(_migration["target_model"])
    _active_override = target_nv
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
