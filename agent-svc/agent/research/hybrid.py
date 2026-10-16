"""Concurrent, cache-assisted hybrid retrieval planner (ADR-0052).

``plan_hybrid_retrieval`` is the single implementation of ``hybrid_vector``
retrieval. It runs independent SlopSearX (web) and Qdrant (vector) discovery
concurrently under the global admission budget, blends the two ranked result
sets deterministically (rather than concatenating web-first), and — when a
scraper is supplied — acquires candidate Markdown from the Valkey scrape cache
or via live scrape.

Blend policy (deterministic, no web-first starvation)
-----------------------------------------------------

1. Every URL is normalised (lowercased scheme/host, default ports dropped,
   fragment stripped, trailing slash stripped) and deduplicated by that
   normalised form. Provenance per URL is ``web``, ``vector``, or ``both``.
2. A floor guarantee prevents either source from being starved by a full
   budget from the other::

       floor_web    = max(0, limit - len(vector_unique))
       floor_vector = max(0, limit - len(web_unique))

   In addition, a diversity floor reserves one slot for each source that has
   at least one exclusive candidate (provenance ``web`` / ``vector``) while
   the other source also contributes candidates::

       if has_web_only and vector_unique:    floor_web    = max(floor_web, 1)
       if has_vector_only and web_unique:    floor_vector = max(floor_vector, 1)

   The web floor and vector floor are emitted first (each in its source's
   rank order), then the remaining slots are filled by round-robin
   interleaving of the two rank orders (web first). Rank order within a
   source is preserved, so each source's own relevance signal is retained;
   the round-robin interleave is the deterministic cross-source tie-break.
   Overlapping URLs (provenance ``both``) are emitted once, keeping web's
   richer metadata (title/description) and the vector score when present.
3. An exclusive (``web``-only or ``vector``-only) candidate therefore always
   has a chance to enter the final candidate set even when the other source
   returns a full result budget.

Acquisition
-----------

For each surviving candidate the planner consults ``CrawlCache.check_cache``
with ``cache_max_age_ms``. A fresh, compatible cached entry (non-empty
Markdown) is reused and marked ``cache_state="from_cache"`` with
``cache_age_ms``; everything else is live-scraped via ``_scrape_urls`` and
marked ``cache_state="live"``. Cache lookups are best-effort: a cache failure
degrades to a live scrape and never raises.

Graceful degradation
--------------------

- Semantic service disabled/unavailable → web-only (equivalent to keyword
  retrieval: the web results are returned in their original rank order).
- Web (SearXNG) failure/empty/timeout → vector-only.

Transport failures never propagate to callers; each discovery branch is
independently timeout-bounded and exceptions are logged and treated as an
empty result set for that branch.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from ..admission import get_admission
from ..crawl_cache import CrawlCache
from ..scraper_client import ScraperClient
from ..search_metadata import search_metadata
from ..searxng_client import SearchHealth, SearXNGClient
from ..semantic_client import SemanticClient
from ..settings import load_settings
from .discovery import _scrape_urls
from .sources import SourceArtifact

logger = logging.getLogger(__name__)

WEB_TIMEOUT_SECONDS = 15.0
VECTOR_TIMEOUT_SECONDS = 8.0

# Default freshness window for reusing the Valkey scrape cache during hybrid
# acquisition. Entries older than this are treated as stale and live-scraped.
# Stale-serving behaviour is governed by the cache-contract issue (#529), not
# this module — only fresh, compatible entries are ever reused here.
DEFAULT_CACHE_MAX_AGE_MS = 3_600_000  # 1 hour

_crawl_cache: CrawlCache | None = None


def get_crawl_cache() -> CrawlCache:
    """Return a process-wide :class:`CrawlCache` singleton, created on demand.

    Mirrors :func:`agent.admission.get_admission`: the Valkey connection is
    lazy (no socket until the first command), so creating the singleton has no
    startup cost even when Valkey is down.
    """
    global _crawl_cache
    if _crawl_cache is None:
        settings = load_settings()
        _crawl_cache = CrawlCache(
            f"redis://{settings.valkey_host}:{settings.valkey_port}/{settings.valkey_db}"
        )
    return _crawl_cache


@dataclass
class HybridRetrievalResult:
    """Output of a hybrid retrieval plan."""

    results: list[dict[str, Any]]
    artifacts: list[SourceArtifact]
    web_health: SearchHealth | None = None
    web_count: int = 0


def _is_default_port(scheme: str, port: int) -> bool:
    """Return True when *port* is the scheme's default port."""
    return (scheme == "http" and port == 80) or (scheme == "https" and port == 443)


def _normalize_url_for_blend(url: str) -> str:
    """Normalize a URL for deduplication during hybrid blending.

    Lowercases the scheme and hostname, drops the default port, strips the
    fragment, and strips a trailing slash from the path. The query string and
    path case are preserved (both are significant to URL identity).

    Malformed URLs (an invalid IPv6 literal, a non-numeric port) raise
    ``ValueError`` during parsing; those fall back to the raw URL so a single
    bad result cannot abort the whole blend. The raw text is still
    deduplicated exactly by :func:`_collect_candidates`.
    """
    try:
        parsed = urlparse(url)
        scheme = parsed.scheme.lower()
        host = (parsed.hostname or "").lower()
        port = parsed.port
    except ValueError:
        return url
    if not host:
        # Relative or otherwise unparseable URL — normalize what we can.
        return url
    display_host = f"[{host}]" if ":" in host else host
    if port is not None and not _is_default_port(scheme, port):
        display_host = f"{display_host}:{port}"
    path = parsed.path.rstrip("/")
    if not path:
        path = "/"
    normalized = f"{scheme}://{display_host}{path}"
    if parsed.query:
        normalized += f"?{parsed.query}"
    return normalized


def _collect_candidates(
    web_results: list[dict[str, Any]],
    vector_results: list[dict[str, Any]],
) -> tuple[list[str], list[str], dict[str, dict[str, Any]]]:
    """Normalize, deduplicate, and record provenance for both result sets.

    Returns ``(web_order, vector_order, by_norm)`` where the two order lists
    contain normalised URLs in rank order and ``by_norm`` maps a normalised
    URL to its candidate dict (``url``, ``title``, ``description``, ``score``,
    ``retrieval``). Web metadata (title/description) is preferred for URLs
    present in both sources; the vector score is retained when available.
    """
    by_norm: dict[str, dict[str, Any]] = {}
    web_order: list[str] = []
    vector_order: list[str] = []
    seen_web: set[str] = set()
    seen_vector: set[str] = set()

    for rank, result in enumerate(web_results):
        url = str(result.get("url") or "").strip()
        if not url:
            continue
        norm = _normalize_url_for_blend(url)
        if norm in seen_web:
            continue
        seen_web.add(norm)
        by_norm[norm] = {
            "url": url,
            "title": result.get("title", ""),
            "description": result.get("description", ""),
            **search_metadata(result),
            "score": None,
            "retrieval": "web",
            "web_rank": rank,
            "vector_rank": None,
        }
        web_order.append(norm)

    for rank, result in enumerate(vector_results):
        url = str(result.get("url") or "").strip()
        if not url:
            continue
        norm = _normalize_url_for_blend(url)
        if norm in seen_vector:
            continue
        seen_vector.add(norm)
        score = result.get("score")
        candidate = by_norm.get(norm)
        if candidate is None:
            by_norm[norm] = {
                "url": url,
                "title": result.get("title", ""),
                "description": "",
                "score": score,
                "retrieval": "vector",
                "web_rank": None,
                "vector_rank": rank,
            }
        else:
            candidate["retrieval"] = "both"
            candidate["vector_rank"] = rank
            if candidate["score"] is None:
                candidate["score"] = score
        # Overlapping URLs remain in ``vector_order`` so that the floor
        # formula sees the vector source's true unique count.
        vector_order.append(norm)

    return web_order, vector_order, by_norm


def _blend(
    web_order: list[str],
    vector_order: list[str],
    by_norm: dict[str, dict[str, Any]],
    limit: int,
) -> list[dict[str, Any]]:
    """Blend two rank-ordered candidate lists into at most ``limit`` results.

    See the module docstring for the full policy. The floor guarantee ensures
    neither source can be fully starved by a full budget from the other; the
    diversity floor reserves a slot for each source that has an exclusive
    candidate while the other source also contributes. The remaining slots are
    filled by round-robin interleaving (web first), which is deterministic.
    """
    has_web_only = any(by_norm[norm]["retrieval"] == "web" for norm in web_order)
    has_vector_only = any(
        by_norm[norm]["retrieval"] == "vector" for norm in vector_order
    )

    floor_web = max(0, limit - len(vector_order))
    floor_vector = max(0, limit - len(web_order))

    # Diversity floor: an exclusive candidate from a source that also faces
    # candidates from the other source must always have a chance to enter.
    if has_web_only and vector_order:
        floor_web = max(floor_web, 1)
    if has_vector_only and web_order:
        floor_vector = max(floor_vector, 1)

    result: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _take_from(order: list[str], count: int) -> None:
        taken = 0
        for norm in order:
            if taken >= count or len(result) >= limit:
                return
            if norm in seen:
                continue
            result.append(by_norm[norm])
            seen.add(norm)
            taken += 1

    _take_from(web_order, floor_web)
    _take_from(vector_order, floor_vector)

    # Round-robin interleave of the remaining rank slots (web first).
    web_idx = 0
    vector_idx = 0
    while len(result) < limit and (
        web_idx < len(web_order) or vector_idx < len(vector_order)
    ):
        while web_idx < len(web_order) and web_order[web_idx] in seen:
            web_idx += 1

        advanced = False
        if web_idx < len(web_order):
            result.append(by_norm[web_order[web_idx]])
            seen.add(web_order[web_idx])
            web_idx += 1
            advanced = True

        # Re-check ``seen`` after the web append so an overlapping URL at the
        # current vector pointer is not emitted twice in the same iteration.
        while vector_idx < len(vector_order) and vector_order[vector_idx] in seen:
            vector_idx += 1
        if len(result) < limit and vector_idx < len(vector_order):
            result.append(by_norm[vector_order[vector_idx]])
            seen.add(vector_order[vector_idx])
            vector_idx += 1
            advanced = True

        if not advanced:
            break

    return result


def _make_artifact(
    candidate: dict[str, Any],
    markdown: str,
    source: str,
    cache_state: str,
    cache_age_ms: int | None,
) -> SourceArtifact:
    """Build a :class:`SourceArtifact` carrying full hybrid provenance."""
    return SourceArtifact(
        url=candidate["url"],
        title=candidate.get("title", ""),
        relevance=candidate.get("description", ""),
        markdown=markdown,
        source=source,
        char_count=len(markdown),
        cache_state=cache_state,
        cache_age_ms=cache_age_ms,
        retrieval=candidate.get("retrieval", "web"),
        score=candidate.get("score"),
        fetched_at=time.time(),
    )


async def _acquire_candidates(
    candidates: list[dict[str, Any]],
    scraper: ScraperClient,
    cache: CrawlCache,
    cache_max_age_ms: int | None,
    limit: int,
) -> list[SourceArtifact]:
    """Acquire Markdown for surviving candidates via cache then live scrape.

    Cache hits that pass the freshness policy are reused immediately
    (``cache_state="from_cache"``); misses and stale entries are live-scraped
    in one batch via ``_scrape_urls`` (``cache_state="live"``). Artifacts are
    returned in candidate order. Cache lookup failures degrade to a live
    scrape and never raise.
    """
    cached: dict[str, SourceArtifact] = {}
    missing: list[dict[str, Any]] = []

    for candidate in candidates:
        artifact: SourceArtifact | None = None
        if cache_max_age_ms:
            try:
                use_cached, cached_data, _err = cache.check_cache(
                    candidate["url"], max_age_ms=cache_max_age_ms
                )
            except Exception as exc:
                logger.warning(
                    "Hybrid cache check failed for %s: %s", candidate["url"], exc
                )
                use_cached, cached_data = False, None
            if use_cached and isinstance(cached_data, dict):
                data = cached_data.get("data") or {}
                markdown = data.get("markdown")
                if isinstance(markdown, str) and markdown.strip():
                    age_ms: int | None = None
                    with contextlib.suppress(Exception):
                        age_ms = cache.get_age_ms(candidate["url"])
                    artifact = _make_artifact(
                        candidate,
                        markdown,
                        data.get("source") or "cache",
                        "from_cache",
                        age_ms,
                    )
        if artifact is not None:
            cached[candidate["url"]] = artifact
        else:
            missing.append(candidate)

    live_by_url: dict[str, SourceArtifact] = {}
    if missing:
        live = await _scrape_urls(
            [candidate["url"] for candidate in missing],
            scraper,
            min_sources=max(0, limit - len(cached)),
            max_attempts=len(missing),
        )
        for artifact in live:
            if artifact.markdown:
                live_by_url[artifact.url] = artifact

    artifacts: list[SourceArtifact] = []
    for candidate in candidates:
        if candidate["url"] in cached:
            artifacts.append(cached[candidate["url"]])
        elif candidate["url"] in live_by_url:
            live_artifact = live_by_url[candidate["url"]]
            artifacts.append(
                _make_artifact(
                    candidate,
                    live_artifact.markdown or "",
                    live_artifact.source,
                    "live",
                    None,
                )
            )
    return artifacts


async def plan_hybrid_retrieval(
    query: str,
    limit: int,
    *,
    searxng: SearXNGClient | None = None,
    semantic: SemanticClient | None = None,
    web_results: list[dict[str, Any]] | None = None,
    categories: list[str] | None = None,
    sources: list[str] | None = None,
    admission: Any = None,
    scraper: ScraperClient | None = None,
    cache: CrawlCache | None = None,
    cache_max_age_ms: int | None = DEFAULT_CACHE_MAX_AGE_MS,
    web_timeout: float = WEB_TIMEOUT_SECONDS,
    vector_timeout: float = VECTOR_TIMEOUT_SECONDS,
    raise_on_rate_limit: bool = False,
) -> HybridRetrievalResult:
    """Plan a ``hybrid_vector`` retrieval: discover, blend, and acquire.

    Args:
        query: The search query.
        limit: The maximum number of candidates to return.
        searxng: Web-search client. When omitted and ``web_results`` is not
            supplied, web discovery is skipped.
        semantic: Vector-search client. When omitted, vector discovery is
            skipped (semantic disabled).
        web_results: Pre-fetched web results. When supplied, web discovery is
            not performed (the caller has already searched).
        categories: SearXNG categories for web discovery.
        sources: SearXNG sources for web discovery.
        admission: Admission controller; defaults to the process singleton.
        scraper: When supplied, enables cache-assisted content acquisition and
            populates ``artifacts``. Otherwise only discovery + blend run.
        cache: Scrape cache; defaults to the process-wide singleton.
        cache_max_age_ms: Freshness window for cache reuse (``None`` disables
            the cache lookup).
        web_timeout: Per-branch timeout for web discovery (seconds).
        vector_timeout: Per-branch timeout for vector discovery (seconds).
        raise_on_rate_limit: Opt into the retryable downstream 429
            classification (ADR-0053): when the web branch answers HTTP 429
            the classified ``RetryableRateLimitError`` propagates to the
            caller (answer pipeline) instead of degrading to vector-only
            results. Degrading call sites (``/v2/search`` hybrid_vector)
            leave it ``False``.

    Returns:
        A :class:`HybridRetrievalResult` with blended ``results``, acquired
        ``artifacts`` (empty unless ``scraper`` is set), and ``web_health`` /
        ``web_count`` for callers that need the raw web-search outcome.
    """
    admission = admission if admission is not None else get_admission()

    web_list: list[dict[str, Any]] = (
        list(web_results) if web_results is not None else []
    )
    vector_list: list[dict[str, Any]] = []
    web_health: SearchHealth | None = None

    web_needed = web_results is None and searxng is not None
    vector_needed = semantic is not None

    async def _discover_web() -> None:
        nonlocal web_health
        assert searxng is not None  # nosec — guarded by web_needed
        async with admission.resource("lightweight_fetch", weight=1):
            results, health = await searxng.search(
                query,
                limit=limit,
                categories=categories,
                sources=sources,
                raise_on_rate_limit=raise_on_rate_limit,
            )
        web_list.extend(results)
        web_health = health

    async def _discover_vector() -> None:
        assert semantic is not None  # nosec — guarded by vector_needed
        async with admission.resource("lightweight_fetch", weight=1):
            results = await semantic.search_vector(query, limit=limit)
        vector_list.extend(results)

    coros: list[Any] = []
    labels: list[str] = []
    if web_needed:
        coros.append(asyncio.wait_for(_discover_web(), timeout=web_timeout))
        labels.append("web")
    if vector_needed:
        coros.append(asyncio.wait_for(_discover_vector(), timeout=vector_timeout))
        labels.append("vector")

    if coros:
        outcomes = await asyncio.gather(*coros, return_exceptions=True)
        for label, outcome in zip(labels, outcomes, strict=True):
            if isinstance(outcome, BaseException):
                from ..exceptions import RetryableRateLimitError

                if (
                    label == "web"
                    and raise_on_rate_limit
                    and isinstance(outcome, RetryableRateLimitError)
                ):
                    # Whole-operation retry semantics (ADR-0053): a
                    # downstream capacity condition must reach the caller so
                    # sync answer renders a retryable 429 and the worker
                    # schedules a bounded retry — never degrade to
                    # vector-only results.
                    raise outcome
                logger.warning("Hybrid %s discovery failed: %s", label, outcome)

    web_order, vector_order, by_norm = _collect_candidates(web_list, vector_list)
    results = _blend(web_order, vector_order, by_norm, limit)

    artifacts: list[SourceArtifact] = []
    if scraper is not None and results:
        effective_cache = cache if cache is not None else get_crawl_cache()
        artifacts = await _acquire_candidates(
            results, scraper, effective_cache, cache_max_age_ms, limit
        )

    return HybridRetrievalResult(
        results=results,
        artifacts=artifacts,
        web_health=web_health,
        web_count=len(web_list),
    )
