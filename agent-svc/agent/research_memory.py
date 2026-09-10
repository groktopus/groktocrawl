"""Research Memory — Hybrid semantic cache using Valkey + Qdrant.

Stores research artifacts in Valkey with Qdrant-backed semantic similarity
search for cache retrieval.  Uses semantic-svc for embedding generation and
Qdrant directly for point CRUD operations.

Valkey key schema (ADR-0041):
    memory:{memory_id}:data  → JSON {query, artifact, sources, model,
                                      created_at, expires_at, user_id}
    memory:index             → SET of all active memory_ids

Qdrant collection: research_memory
    Point payload: {query, memory_id, user_id, timestamp, expires_at}

Config via env:
    RESEARCH_MEMORY_TTL (default 604800 = 7 days)
    RESEARCH_MEMORY_MAX_ARTIFACT_BYTES (default 5_242_880 = 5MB)
"""

import asyncio
import hashlib
import json
import logging
import os
import time
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from redis import Redis

from common.metrics import METRICS
from common.stage_metrics import inc_counter, observe_elapsed, set_gauge

logger = logging.getLogger(__name__)

_MEMORY_LOOKUP_SECONDS = "groktocrawl_research_memory_lookup_seconds"
_MEMORY_LOOKUP_SECONDS_HELP = "Research memory lookup latency"
_MEMORY_LOOKUP_TOTAL = "groktocrawl_research_memory_lookup_total"
_MEMORY_LOOKUP_TOTAL_HELP = "Research memory lookups by outcome"

# ── Constants ──────────────────────────────────────────────────
QDRANT_COLLECTION: str = "research_memory"
EMBED_DIM: int = 1024  # BAAI/bge-m3
DEFAULT_TTL: int = 604800  # 7 days
DEFAULT_MAX_ARTIFACT_BYTES: int = 5_242_880  # 5 MB
DEFAULT_SIMILARITY_THRESHOLD: float = 0.85
DEFAULT_SWEEP_INTERVAL_SECONDS: float = 300.0

_MEMORY_SWEEP_RUNS = "groktocrawl_research_memory_sweep_runs_total"
_MEMORY_SWEEP_RUNS_HELP = "Research memory sweep invocations"
_MEMORY_ORPHANS_SWEPT = "groktocrawl_research_memory_orphans_swept_total"
_MEMORY_ORPHANS_SWEPT_HELP = "Orphaned Qdrant points removed by research memory sweep"
_MEMORY_ORPHANS_GAUGE = "groktocrawl_research_memory_orphans"
_MEMORY_ORPHANS_GAUGE_HELP = "Orphaned Qdrant points removed in the most recent sweep"


def _get_ttl() -> int:
    """TTL in seconds from RESEARCH_MEMORY_TTL env var, or default 7 days."""
    val = os.environ.get("RESEARCH_MEMORY_TTL", "")
    if val:
        try:
            return int(val)
        except ValueError:
            logger.warning(
                "Invalid RESEARCH_MEMORY_TTL=%s, using default %d",
                val,
                DEFAULT_TTL,
            )
    return DEFAULT_TTL


def _get_max_artifact_bytes() -> int:
    """Max artifact bytes from RESEARCH_MEMORY_MAX_ARTIFACT_BYTES, or 5 MB."""
    val = os.environ.get("RESEARCH_MEMORY_MAX_ARTIFACT_BYTES", "")
    if val:
        try:
            return int(val)
        except ValueError:
            logger.warning(
                "Invalid RESEARCH_MEMORY_MAX_ARTIFACT_BYTES=%s, using default %d",
                val,
                DEFAULT_MAX_ARTIFACT_BYTES,
            )
    return DEFAULT_MAX_ARTIFACT_BYTES


def _qdrant_url() -> str:
    """Qdrant URL from env or Docker default."""
    return os.environ.get("QDRANT_URL", "http://qdrant:6333").rstrip("/")


def _semantic_url() -> str:
    """Semantic service URL from env or Docker default."""
    return os.environ.get("SEMANTIC_URL", "http://semantic-svc:8003").rstrip("/")


def _canonical_json(value: Any) -> Any:
    """Recursively sort dict keys so JSON schemas hash deterministically."""
    if isinstance(value, dict):
        return {
            str(k): _canonical_json(v)
            for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))
        }
    if isinstance(value, list):
        return [_canonical_json(v) for v in value]
    return value


def compute_fingerprint(
    *,
    prompt: str = "",
    urls: list[str] | None = None,
    schema: dict | None = None,
    model: str | None = None,
    search_type: str = "deep",
    include_images: bool = False,
    include_source_content: bool = False,
    citation_style: str = "inline",
    strict_constrain_to_urls: bool = False,
    force_fresh: bool = False,
) -> str:
    """Return a canonical SHA-256 fingerprint of response-affecting fields.

    Only request fields that change source selection, synthesis shape, or
    response semantics participate.  Dispatch/accounting fields (``mode``,
    ``stream``, ``webhook``, ``max_credits``) are intentionally excluded.
    """
    normalized_prompt = " ".join((prompt or "").split())
    sorted_urls = sorted(urls) if urls else []
    canonical_schema = _canonical_json(schema) if schema else None
    canonical_model = "" if model in (None, "", "default") else model
    canonical = {
        "prompt": normalized_prompt,
        "urls": sorted_urls,
        "schema": canonical_schema,
        "model": canonical_model,
        "search_type": search_type or "deep",
        "include_images": bool(include_images),
        "include_source_content": bool(include_source_content),
        "citation_style": citation_style or "inline",
        "strict_constrain_to_urls": bool(strict_constrain_to_urls),
        "force_fresh": bool(force_fresh),
    }
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _parse_iso(value: Any) -> datetime | None:
    """Parse an ISO timestamp; returns an aware UTC datetime or ``None``."""
    if not isinstance(value, str) or not value:
        return None
    s = value.strip()
    if s.endswith(("Z", "z")):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s).astimezone(UTC)
    except (ValueError, TypeError):
        return None


def _entry_freshness(
    artifact: dict[str, Any], now: datetime | None = None
) -> tuple[float, str, float, str] | None:
    """Derive ``(age_hours, freshness, ttl_hours, expires_at_iso)``.

    Uses the artifact's stored ``created_at``/``expires_at`` contract and
    fails closed (``None``) on malformed, missing, or already-expired
    timestamps — never a ``datetime.now(UTC)`` fallback.
    """
    created_at = _parse_iso(artifact.get("created_at"))
    expires_at = _parse_iso(artifact.get("expires_at"))
    if created_at is None or expires_at is None:
        return None
    now = now or datetime.now(UTC)
    if now >= expires_at:
        return None
    ttl_seconds = (expires_at - created_at).total_seconds()
    if ttl_seconds <= 0:
        return None
    ttl_hours = ttl_seconds / 3600.0
    age_hours = (now - created_at).total_seconds() / 3600.0
    if age_hours < ttl_hours / 4:
        freshness = "fresh"
    elif age_hours < ttl_hours / 2:
        freshness = "aging"
    else:
        freshness = "stale"
    return age_hours, freshness, ttl_hours, expires_at.isoformat()


def _record_sweep_metrics(removed: int) -> None:
    """Emit sweep counters/gauges for automatic and manual sweeps."""
    inc_counter(_MEMORY_SWEEP_RUNS, _MEMORY_SWEEP_RUNS_HELP, {})
    METRICS.counter(_MEMORY_ORPHANS_SWEPT, _MEMORY_ORPHANS_SWEPT_HELP).inc(
        value=float(removed)
    )
    set_gauge(_MEMORY_ORPHANS_GAUGE, _MEMORY_ORPHANS_GAUGE_HELP, {}, float(removed))


async def run_research_memory_sweep_loop(
    memory: Any,
    interval: float,
    shutdown_event: asyncio.Event,
) -> None:
    """Periodically sweep dangling Qdrant references until shutdown.

    Runs as a tracked background task; the first sweep happens after one
    ``interval`` and each iteration checks the shutdown event so the task
    exits promptly during graceful shutdown.
    """
    while not shutdown_event.is_set():
        try:
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            return
        if shutdown_event.is_set():
            return
        try:
            await memory.sweep()
        except Exception:
            logger.warning("Research memory background sweep failed", exc_info=True)


class ResearchMemory:
    """Hybrid semantic cache for research artifacts.

    Valkey stores the full artifact payload (JSON blob with TTL).
    Qdrant stores the embedding vector + metadata for similarity search.
    Embedding generation is delegated to semantic-svc.

    Usage::

        memory = ResearchMemory(
            redis_url="redis://valkey:6379/0",
            semantic_url="http://semantic-svc:8003",
            qdrant_url="http://qdrant:6333",
        )
        # Store
        mid = await memory.store(prompt="...", artifact="...", sources=[...])
        # Query
        result = await memory.query(prompt="...")
        if result["hit"]:
            print(result["artifact"]["result"])
    """

    def __init__(
        self,
        redis_url: str,
        semantic_url: str | None = None,
        qdrant_url: str | None = None,
        threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
    ):
        """Initialise the memory store.

        Args:
            redis_url: Valkey connection string.
            semantic_url: semantic-svc base URL (for embeddings).  Falls
                back to ``SEMANTIC_URL`` env var then Docker default.
            qdrant_url: Qdrant base URL (for point CRUD).  Falls back
                to ``QDRANT_URL`` env var then Docker default.
            threshold: Minimum cosine similarity for a cache hit
                (default 0.85).
        """
        self.redis = Redis.from_url(redis_url, decode_responses=True)
        self._semantic_url = semantic_url or _semantic_url()
        self._qdrant_url = qdrant_url or _qdrant_url()
        self.threshold = threshold
        self.ttl = _get_ttl()
        self.max_artifact_bytes = _get_max_artifact_bytes()
        self._qdrant_client: httpx.AsyncClient | None = None
        self._semantic_client: httpx.AsyncClient | None = None
        self._refresh_tasks: dict[str, asyncio.Task[Any]] = {}

    # ── Internal HTTP clients ───────────────────────────────────

    async def _get_qdrant(self) -> httpx.AsyncClient:
        if self._qdrant_client is None:
            self._qdrant_client = httpx.AsyncClient(
                timeout=30,
                headers={"User-Agent": "GroktoCrawl/ResearchMemory"},
            )
        return self._qdrant_client

    async def _get_semantic(self) -> httpx.AsyncClient:
        if self._semantic_client is None:
            self._semantic_client = httpx.AsyncClient(
                timeout=30,
                headers={"User-Agent": "GroktoCrawl/ResearchMemory"},
            )
        return self._semantic_client

    async def close(self) -> None:
        """Close underlying HTTP clients."""
        if self._qdrant_client:
            await self._qdrant_client.aclose()
            self._qdrant_client = None
        if self._semantic_client:
            await self._semantic_client.aclose()
            self._semantic_client = None

    # ── Qdrant collection management ────────────────────────────

    async def _ensure_collection(self) -> None:
        """Ensure the *research_memory* collection exists in Qdrant."""
        qdrant = await self._get_qdrant()
        resp = await qdrant.get(f"{self._qdrant_url}/collections/{QDRANT_COLLECTION}")
        if resp.status_code == 200:
            return
        create_resp = await qdrant.put(
            f"{self._qdrant_url}/collections/{QDRANT_COLLECTION}",
            json={
                "vectors": {
                    "size": EMBED_DIM,
                    "distance": "Cosine",
                }
            },
        )
        if create_resp.status_code not in (200, 201):
            logger.warning(
                "Failed to create Qdrant collection %s: %s",
                QDRANT_COLLECTION,
                create_resp.text,
            )
        else:
            logger.info("Created Qdrant collection %s", QDRANT_COLLECTION)

    # ── Embedding via semantic-svc ──────────────────────────────

    async def _embed(self, text: str) -> list[float]:
        """Get an embedding vector for *text* via semantic-svc."""
        client = await self._get_semantic()
        resp = await client.post(
            f"{self._semantic_url}/embed",
            json={"input": [text]},
        )
        resp.raise_for_status()
        data = resp.json()
        return data["embeddings"][0]  # type: ignore[no-any-return]

    # ── Single-flight refresh ───────────────────────────────────

    def start_refresh(
        self, key: str, coro_factory: Callable[[], Coroutine[Any, Any, Any]]
    ) -> asyncio.Task[Any]:
        """Return the single in-flight refresh task for *key*.

        If no task is running for *key*, a new background task is created
        from ``coro_factory()`` and tracked until completion.  Concurrent
        callers receive the same task, so a compatible cache key is
        refreshed exactly once.
        """
        task = self._refresh_tasks.get(key)
        if task is None or task.done():
            task = asyncio.create_task(coro_factory())
            self._refresh_tasks[key] = task

            def _discard(_future: asyncio.Future[Any]) -> None:
                if self._refresh_tasks.get(key) is _future:
                    self._refresh_tasks.pop(key, None)

            task.add_done_callback(_discard)
        return task

    # ── Public API ──────────────────────────────────────────────

    async def query(
        self,
        prompt: str,
        user_id: str | None = None,
        max_age_hours: float | None = None,
        fingerprint: str | None = None,
        max_stale_hours: float | None = None,
    ) -> dict[str, Any]:
        """Search for a semantically similar cached artifact.

        Embeds *prompt* via semantic-svc, searches the Qdrant
        ``research_memory`` collection, fetches matching artifacts from
        Valkey, and returns the best match above the similarity
        threshold.

        Args:
            prompt: The research question to search for.
            user_id: Optional user scope for filtering.
            max_age_hours: Optional hard age cap; artifacts older than
                this are treated as misses.
            fingerprint: Optional compatibility fingerprint; a stored
                entry whose fingerprint differs is treated as a miss.
            max_stale_hours: Optional stale-while-revalidate window used
                to compute ``swr_eligible`` for ``stale`` hits.

        Returns:
            A dict with:
            - ``hit`` (bool)
            - ``artifact`` (dict | None)
            - ``similarity`` (float) — cosine similarity score
            - ``freshness`` (str | None) — ``fresh``, ``aging``, ``stale``
            - ``memory_id`` (str | None)
            - ``age_hours`` (float | None)
            - ``expires_at`` (str | None)
            - ``compatibility`` (str | None)
            - ``swr_eligible`` (bool)
        """
        started = time.monotonic()
        outcome = "miss"
        try:
            try:
                embedding = await self._embed(prompt)
            except Exception:
                outcome = "error"
                logger.warning(
                    "Failed to embed query for research memory", exc_info=True
                )
                return {"hit": False}

            try:
                await self._ensure_collection()

                qdrant = await self._get_qdrant()
                search_payload: dict[str, Any] = {
                    "vector": embedding,
                    "limit": 5,
                    "with_payload": True,
                }
                if user_id:
                    search_payload["filter"] = {
                        "must": [{"key": "user_id", "match": {"value": user_id}}]
                    }

                resp = await qdrant.post(
                    f"{self._qdrant_url}/collections/{QDRANT_COLLECTION}/points/search",
                    json=search_payload,
                )
                resp.raise_for_status()
                results = resp.json().get("result", [])
            except Exception:
                outcome = "error"
                logger.warning(
                    "Qdrant search failed for research memory", exc_info=True
                )
                return {"hit": False}

            if not results:
                return {"hit": False}

            # Walk results in descending score order; first compatible
            # Valkey hit wins.
            incompatible_seen = False
            for result in results:
                score = float(result.get("score", 0))
                if score < self.threshold:
                    logger.debug(
                        "Best Qdrant match below threshold: %.3f < %.2f",
                        score,
                        self.threshold,
                    )
                    return {"hit": False}

                payload = result.get("payload", {})
                memory_id = payload.get("memory_id", "")
                if not memory_id:
                    continue

                try:
                    artifact_raw = self.redis.get(f"memory:{memory_id}:data")
                except Exception:
                    outcome = "error"
                    logger.warning(
                        "Valkey lookup failed for research memory", exc_info=True
                    )
                    return {"hit": False}
                if artifact_raw is None:
                    # Valkey key expired — skip; sweep will clean it up later
                    logger.debug(
                        "Valkey key missing for memory_id=%s, skipping", memory_id
                    )
                    continue

                try:
                    artifact = json.loads(artifact_raw)
                except (json.JSONDecodeError, TypeError):
                    logger.debug("Unparseable artifact for memory_id=%s", memory_id)
                    continue

                # ── Compatibility fingerprint ────────────────────────
                if fingerprint is not None:
                    stored_fingerprint = artifact.get("fingerprint", "")
                    if not stored_fingerprint or stored_fingerprint != fingerprint:
                        incompatible_seen = True
                        logger.debug(
                            "Fingerprint mismatch for memory_id=%s, skipping",
                            memory_id,
                        )
                        continue

                # ── Freshness from stored timestamps ─────────────────
                freshness_meta = _entry_freshness(artifact)
                if freshness_meta is None:
                    logger.debug(
                        "Invalid or expired freshness metadata for memory_id=%s, "
                        "skipping",
                        memory_id,
                    )
                    continue
                age_hours, freshness, ttl_hours, expires_at = freshness_meta

                # ── Caller age gate ──────────────────────────────────
                if max_age_hours is not None and age_hours > max_age_hours:
                    logger.debug(
                        "memory_id=%s too old (age=%.2fh > max=%.2fh), skipping",
                        memory_id,
                        age_hours,
                        max_age_hours,
                    )
                    continue

                swr_eligible = (
                    max_stale_hours is not None
                    and freshness == "stale"
                    and age_hours <= (ttl_hours / 2) + max_stale_hours
                )

                outcome = freshness
                logger.info(
                    "Research memory HIT (memory_id=%s, similarity=%.3f, freshness=%s)",
                    memory_id,
                    score,
                    freshness,
                )
                return {
                    "hit": True,
                    "artifact": artifact,
                    "similarity": score,
                    "freshness": freshness,
                    "memory_id": memory_id,
                    "age_hours": age_hours,
                    "expires_at": expires_at,
                    "compatibility": "compatible" if fingerprint is not None else None,
                    "swr_eligible": swr_eligible,
                }

            if incompatible_seen:
                return {"hit": False, "compatibility": "incompatible"}
            return {"hit": False}
        finally:
            observe_elapsed(
                _MEMORY_LOOKUP_SECONDS, _MEMORY_LOOKUP_SECONDS_HELP, {}, started
            )
            inc_counter(
                _MEMORY_LOOKUP_TOTAL,
                _MEMORY_LOOKUP_TOTAL_HELP,
                {"outcome": outcome},
            )

    async def store(
        self,
        prompt: str,
        artifact: str,
        sources: list[dict],
        model: str = "",
        user_id: str | None = None,
        metadata: dict | None = None,
        fingerprint: str | None = None,
    ) -> str:
        """Store a research artifact in the semantic cache.

        Writes to Valkey (``memory:{id}:data``, adds to ``memory:index``)
        and upserts a point in Qdrant with the embedded query vector.

        Args:
            prompt: The original research question.
            artifact: The LLM-synthesised answer (markdown).
            sources: List of source dicts (each with at minimum ``url``
                and ``title``).
            model: The LLM model that produced the artifact.
            user_id: Optional user scope.
            metadata: Optional extra context dict.
            fingerprint: Optional compatibility fingerprint recorded on
                the entry and Qdrant payload.

        Returns:
            The ``memory_id`` (UUID v4 string).
        """
        import uuid as _uuid

        memory_id = str(_uuid.uuid4())
        now = datetime.now(UTC)
        expires_at = now + timedelta(seconds=self.ttl)

        # Log warning if artifact exceeds size threshold
        artifact_bytes = len(artifact.encode("utf-8"))
        if artifact_bytes > self.max_artifact_bytes:
            logger.warning(
                "ResearchMemory artifact exceeds max size (%d > %d bytes) "
                "for memory_id=%s — storing anyway",
                artifact_bytes,
                self.max_artifact_bytes,
                memory_id,
            )

        # Build cache entry
        entry: dict[str, Any] = {
            "query": prompt,
            "artifact": artifact,
            "sources": sources,
            "model": model,
            "created_at": now.isoformat(),
            "expires_at": expires_at.isoformat(),
            "user_id": user_id,
        }
        if metadata:
            entry["metadata"] = metadata
        if fingerprint:
            entry["fingerprint"] = fingerprint

        # Store in Valkey
        data_key = f"memory:{memory_id}:data"
        self.redis.set(data_key, json.dumps(entry), ex=self.ttl)
        self.redis.sadd("memory:index", memory_id)

        # Embed and store in Qdrant
        try:
            embedding = await self._embed(prompt)
            await self._ensure_collection()

            qdrant = await self._get_qdrant()
            point_payload: dict[str, Any] = {
                "query": prompt,
                "memory_id": memory_id,
                "user_id": user_id,
                "timestamp": now.isoformat(),
                "expires_at": expires_at.isoformat(),
            }
            if fingerprint:
                point_payload["fingerprint"] = fingerprint
            point = {
                "id": memory_id,
                "vector": embedding,
                "payload": point_payload,
            }
            resp = await qdrant.put(
                f"{self._qdrant_url}/collections/{QDRANT_COLLECTION}/points",
                json={"points": [point]},
            )
            if resp.status_code not in (200, 201):
                logger.warning(
                    "Qdrant upsert returned %d for memory_id=%s: %s",
                    resp.status_code,
                    memory_id,
                    resp.text,
                )
            else:
                logger.info(
                    "Stored research memory %s (emb_dim=%d, sources=%d, "
                    "artifact_chars=%d)",
                    memory_id,
                    len(embedding),
                    len(sources),
                    len(artifact),
                )
        except Exception:
            logger.warning(
                "Failed to store Qdrant point for memory_id=%s "
                "(artifact stored in Valkey)",
                memory_id,
                exc_info=True,
            )

        return memory_id

    async def get(self, memory_id: str) -> dict | None:
        """Retrieve a research memory entry from Valkey by ID.

        Args:
            memory_id: The artifact ID returned by ``store()``.

        Returns:
            The stored dict or ``None`` if not found / expired.
        """
        raw = self.redis.get(f"memory:{memory_id}:data")
        if raw is None:
            return None
        try:
            return json.loads(raw)  # type: ignore[no-any-return]
        except (json.JSONDecodeError, TypeError):
            return None

    async def delete(self, memory_id: str) -> bool:
        """Delete from both Valkey **and** Qdrant atomically.

        Args:
            memory_id: The artifact ID to delete.

        Returns:
            ``True`` if the Valkey key existed and was deleted.
        """
        data_key = f"memory:{memory_id}:data"
        existed = self.redis.delete(data_key) > 0
        self.redis.srem("memory:index", memory_id)

        # Remove Qdrant point (best-effort)
        try:
            qdrant = await self._get_qdrant()
            resp = await qdrant.post(
                f"{self._qdrant_url}/collections/{QDRANT_COLLECTION}/points/delete",
                json={
                    "filter": {
                        "must": [{"key": "memory_id", "match": {"value": memory_id}}]
                    }
                },
            )
            if resp.status_code == 404:
                pass  # collection doesn't exist — nothing to do
            elif resp.status_code >= 400:
                logger.warning(
                    "Failed to delete Qdrant point for memory_id=%s: %s",
                    memory_id,
                    resp.text,
                )
            else:
                logger.info(
                    "Deleted research memory %s from Valkey + Qdrant", memory_id
                )
        except Exception:
            logger.warning(
                "Qdrant delete failed for memory_id=%s (Valkey already deleted)",
                memory_id,
                exc_info=True,
            )

        return existed

    async def sweep(self) -> int:
        """Remove Qdrant points whose Valkey keys have expired.

        Scrolls through all points in the ``research_memory`` Qdrant
        collection and deletes those with no corresponding
        ``memory:{id}:data`` Valkey key.

        Returns:
            Number of Qdrant points removed.
        """
        removed = 0

        try:
            qdrant = await self._get_qdrant()

            offset: str | None = None
            while True:
                scroll_payload: dict[str, Any] = {
                    "limit": 100,
                    "with_payload": True,
                }
                if offset:
                    scroll_payload["offset"] = offset

                resp = await qdrant.post(
                    f"{self._qdrant_url}/collections/{QDRANT_COLLECTION}/points/scroll",
                    json=scroll_payload,
                )

                if resp.status_code == 404:
                    # Collection doesn't exist — nothing to sweep
                    return 0

                resp.raise_for_status()
                data = resp.json()
                result_data = data.get("result", {})
                points = result_data.get("points", [])

                if not points:
                    break

                # Collect orphan IDs
                orphans: list[str] = []
                for point in points:
                    mid = point.get("payload", {}).get("memory_id", "")
                    if not mid:
                        continue
                    if not self.redis.exists(f"memory:{mid}:data"):
                        orphans.append(mid)

                # Delete each orphan
                for mid in orphans:
                    try:
                        del_resp = await qdrant.post(
                            f"{self._qdrant_url}/collections/{QDRANT_COLLECTION}"
                            "/points/delete",
                            json={
                                "filter": {
                                    "must": [
                                        {
                                            "key": "memory_id",
                                            "match": {"value": mid},
                                        }
                                    ]
                                }
                            },
                        )
                        if del_resp.status_code < 400:
                            removed += 1
                    except Exception:
                        logger.debug("Failed to delete orphan Qdrant point %s", mid)

                next_offset = result_data.get("next_page_offset")
                if next_offset:
                    offset = next_offset
                else:
                    break

            if removed:
                logger.info(
                    "Swept %d orphaned Qdrant points from research_memory",
                    removed,
                )
        except httpx.ConnectError as e:
            # Qdrant/semantic-svc run under the `indexing` profile and are
            # absent from the default stack.  Distinguish intentional absence
            # (default URL, not configured) from a real outage (explicit URL).
            import os as _os
            explicitly_configured = _os.environ.get("QDRANT_URL") is not None
            if explicitly_configured:
                logger.warning(
                    "Research memory sweep failed — Qdrant unreachable at configured URL: %s",
                    e,
                )
            else:
                logger.debug(
                    "Research memory sweep skipped (Qdrant not deployed): %s", e
                )
        except Exception:
            logger.warning("Research memory sweep failed", exc_info=True)
        finally:
            _record_sweep_metrics(removed)

        return removed

    # ── Batch operations ────────────────────────────────────────

    async def batch_query(
        self,
        queries: list[str],
        user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Search for cached artifacts for multiple queries.

        Each query is independently embedded and searched.  Results are
        returned in the same order as the input queries.

        Args:
            queries: List of research question strings.
            user_id: Optional user scope for filtering.

        Returns:
            List of result dicts, one per input query.  Each dict has:
            - ``hit`` (bool)
            - ``artifact`` (dict | None)
            - ``similarity`` (float | None)
            - ``freshness`` (str | None)
            - ``memory_id`` (str | None)
        """
        import asyncio as _asyncio

        async def _query_one(q: str) -> dict[str, Any]:
            try:
                return await self.query(prompt=q, user_id=user_id)
            except Exception:
                logger.warning(
                    "Batch query failed for %r",
                    q[:80],
                    exc_info=True,
                )
                return {"hit": False}

        # Run queries concurrently
        tasks = [_query_one(q) for q in queries]
        results: Any = await _asyncio.gather(*tasks)
        return list(results)  # type: ignore[arg-type]

    async def batch_store(
        self,
        entries: list[dict[str, Any]],
        user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Store multiple research artifacts independently.

        Each entry is stored independently.  If one fails (e.g. embedding
        failure), the others still succeed.  Per-entry status is returned.

        Args:
            entries: List of dicts, each with:
                - ``query`` (str) — the research question
                - ``artifact`` (str) — the LLM answer
                - ``sources`` (list[dict]) — source documents
                - ``model`` (str, optional) — LLM model name
            user_id: Optional user scope.

        Returns:
            List of result dicts, one per input entry.  Each dict has:
            - ``success`` (bool)
            - ``memory_id`` (str | None)
            - ``error`` (str | None) — present only on failure
        """
        import asyncio as _asyncio

        async def _store_one(entry: dict[str, Any]) -> dict[str, Any]:
            try:
                mid = await self.store(
                    prompt=entry["query"],
                    artifact=entry["artifact"],
                    sources=entry.get("sources", []),
                    model=entry.get("model", ""),
                    user_id=user_id,
                )
                return {"success": True, "memory_id": mid}
            except Exception as exc:
                logger.warning(
                    "Batch store failed for %r: %s",
                    entry.get("query", "")[:80],
                    exc,
                    exc_info=True,
                )
                return {"success": False, "memory_id": None, "error": str(exc)}

        tasks = [_store_one(e) for e in entries]
        results: Any = await _asyncio.gather(*tasks)
        return list(results)  # type: ignore[arg-type]
