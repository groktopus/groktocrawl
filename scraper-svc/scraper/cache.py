"""Valkey scrape result cache with intelligent freshness revalidation.

See ADR-0019 for the design rationale behind the freshness-aware cache
revalidation system.
"""

import hashlib
import json
import logging
import os
import time
from urllib.parse import urlparse

from common.stage_metrics import inc_counter, observe_elapsed
from common.url import normalize_url

from .settings import load_settings
from .source_http import source_httpx_client

logger = logging.getLogger(__name__)

_CACHE_LOOKUP_SECONDS = "groktocrawl_scrape_cache_lookup_seconds"
_CACHE_LOOKUP_SECONDS_HELP = "Scrape result cache lookup latency"
_CACHE_LOOKUP_TOTAL = "groktocrawl_scrape_cache_lookup_total"
_CACHE_LOOKUP_TOTAL_HELP = "Scrape result cache lookups by outcome"

_settings = load_settings()
SCRAPE_CACHE_TTL = _settings.scrape_cache_ttl
SCRAPE_CACHE_MIN_TTL = _settings.scrape_cache_min_ttl
SCRAPE_CACHE_MAX_TTL = _settings.scrape_cache_max_ttl
SCRAPE_CACHE_STABLE_MULTIPLIER = _settings.scrape_cache_stable_multiplier
SCRAPE_CACHE_VOLATILE_CAP = _settings.scrape_cache_volatile_cap

_cache_client = None  # Module-level lazy singleton

# ── Binary content-type detection ──────────────────────────────
BINARY_TYPE_PREFIXES = ("image/", "audio/", "video/")
BINARY_TYPE_EXACT = {
    "application/pdf",
    "application/epub+zip",
    "application/zip",
    "application/gzip",
    "application/x-tar",
    "application/x-rar-compressed",
    "application/x-7z-compressed",
    "application/vnd.android.package-archive",
    "application/vnd.openxmlformats-officedocument",
}


def _is_binary_content_type(content_type: str) -> bool:
    """Check if a Content-Type indicates binary content that shouldn't be parsed as HTML."""
    if not content_type:
        return False
    ct = content_type.lower().split(";")[0].strip()
    if ct in BINARY_TYPE_EXACT:
        return True
    return any(ct.startswith(p) for p in BINARY_TYPE_PREFIXES)


def _derive_filename(url: str, content_type: str) -> str:
    """Derive a sensible filename from URL path + Content-Type."""
    parsed = urlparse(url)
    path = parsed.path or parsed.query or "download"
    basename = path.rstrip("/").split("/")[-1]
    if basename and "." in basename:
        return basename
    ext_map = {
        "application/pdf": ".pdf",
        "application/epub+zip": ".epub",
        "application/zip": ".zip",
        "application/gzip": ".gz",
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/gif": ".gif",
        "image/webp": ".webp",
        "image/svg+xml": ".svg",
        "text/csv": ".csv",
        "application/json": ".json",
    }
    ext = ext_map.get(content_type.split(";")[0].strip(), "")
    return f"{basename}{ext}" if basename else f"download{ext}"


def _make_download_payload(url: str, content: bytes, content_type: str) -> dict:
    """Build a download payload dict for binary content."""
    return {
        "markdown": "",
        "source": "binary",
        "url": url,
        "download": {
            "filename": _derive_filename(url, content_type),
            "content_type": content_type,
            "size": len(content),
            "data_url": None,
        },
    }


# ── Valkey scrape result cache ─────────────────────────────────


def _normalize_url_for_cache(url: str) -> str:
    """Normalize a URL for consistent cache keying.

    Lowercases scheme and hostname, strips trailing slash from path
    (preserving root '/'), and sorts query parameters.

    Delegates to the shared ``common.url.normalize_url``.
    """
    return normalize_url(url)


def _scrape_cache_key(url: str) -> str:
    """Build the Valkey key for a cached scrape result.

    Key: scrape_cache:{sha256_hex_of_normalized_url}
    """
    normalized = _normalize_url_for_cache(url)
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return f"scrape_cache:{digest}"


async def _get_cache_client():
    """Get or create the Valkey cache client singleton.

    Returns the client if connected, or None if Valkey is unavailable
    (graceful degradation — cache is a performance optimization, not
    a requirement).
    """
    global _cache_client
    if _cache_client is not None:
        return _cache_client

    redis_url = (
        f"redis://{_settings.valkey_host}:{_settings.valkey_port}/{_settings.valkey_db}"
    )

    try:
        rpc_socket = os.environ.get("VALKEY_RPC_SOCKET")
        if rpc_socket:
            from .valkey_control import ValkeyControlClient

            _cache_client = ValkeyControlClient(rpc_socket)
            await _cache_client.ping()
            logger.info("Connected to restricted Valkey control socket")
            return _cache_client

        import redis.asyncio as aioredis

        _cache_client = aioredis.from_url(
            redis_url,
            decode_responses=True,
        )
        await _cache_client.ping()
        logger.info("Connected to Valkey for scrape result cache at %s", redis_url)
        return _cache_client
    except Exception as e:
        logger.warning(
            "Valkey unavailable for scrape cache at %s — caching disabled (%s)",
            redis_url,
            e,
        )
        _cache_client = None
        return None


def _migrate_legacy_cached_entry(cached: dict) -> None:
    """Normalize fields of cached entries written by older scraper versions.

    ``source_html_size`` is stored natively as int since the #587 yield
    hardening; entries persisted before that upgrade carry the legacy
    string form, which would break the quality-assessment volume gate
    (TypeError) on every cache hit. Normalizing here — the single choke
    point all cache reads flow through — keeps the migration invisible to
    consumers. Idempotent: native ints pass through untouched.
    """
    size = cached.get("source_html_size")
    if isinstance(size, str):
        try:
            cached["source_html_size"] = int(size)
        except ValueError:
            logger.warning("Dropping unparseable cached source_html_size %r", size)
            cached.pop("source_html_size", None)


async def _check_cache(url: str) -> dict | None:
    """Check Valkey for a cached scrape result with freshness revalidation.

    For content from slow-scraping tiers (playwright, flare-solverr, browser-svc)
    that has ETag or Last-Modified headers stored, performs a blocking conditional
    revalidation (HEAD/GET with If-None-Match / If-Modified-Since). On 304 Not
    Modified, extends the cache TTL and returns the cached content. On 200,
    updates the cache and returns fresh content.

    For fast-tier content (llms.txt, content-negotiation), returns cached content
    immediately — background revalidation is handled by the caller.

    Returns the cached/validated result dict, or None on cache miss.
    """
    started = time.monotonic()
    outcome = "miss"
    try:
        client = await _get_cache_client()
        if not client:
            return None
        try:
            key = _scrape_cache_key(url)
            cached_raw = await client.get(key)
            if not cached_raw:
                return None

            cached = json.loads(cached_raw)
            _migrate_legacy_cached_entry(cached)
            outcome = "hit"
            logger.info("Cache hit for %s (key=%s)", url, key)

            # Determine source tier for revalidation strategy
            source_tier = cached.get("source_tier") or cached.get("source", "")
            etag = cached.get("etag")
            last_modified = cached.get("last_modified")

            # Slow tiers with ETag/LM → blocking revalidation
            if source_tier in ("playwright", "flare-solverr", "browser-svc") and (
                etag or last_modified
            ):
                fresh, result = await _conditional_revalidate(url, etag, last_modified)
                if fresh:
                    # 304 — extend TTL and return cached content
                    new_ttl = _resolve_cache_ttl(url)
                    cached["last_checked_at"] = time.time()
                    await _set_cache_raw(key, cached, new_ttl)
                    logger.info(
                        "Cache revalidated (304) for %s, extended TTL to %ds",
                        url,
                        new_ttl,
                    )
                    return cached
                elif result:
                    # 200 — content changed, return fresh and update cache
                    logger.info("Cache stale (200) for %s, fetching fresh content", url)
                    result = _merge_cache_metadata(result, cached)
                    await _set_cache_raw(key, result, _resolve_cache_ttl(url))
                    return result
                else:
                    # Connection error — serve stale with warning
                    logger.warning(
                        "Revalidation failed for %s, serving stale cache", url
                    )
                    return cached

            # Fast tiers or no ETag/LM — return cached, caller handles revalidation
            return cached
        except Exception as e:
            outcome = "error"
            logger.debug("Cache read failed for %s: %s", url, e)
        return None
    finally:
        observe_elapsed(_CACHE_LOOKUP_SECONDS, _CACHE_LOOKUP_SECONDS_HELP, {}, started)
        inc_counter(_CACHE_LOOKUP_TOTAL, _CACHE_LOOKUP_TOTAL_HELP, {"outcome": outcome})


def _compute_content_hash(text: str) -> str:
    """Compute SHA-256 of content for change detection.

    Uses the markdown content if available, falling back to the raw text.
    Returns a hex digest suitable for comparison.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _parse_domain_ttls() -> dict[str, int]:
    """Parse the SCRAPE_CACHE_DOMAIN_TTLS env var.

    Format: JSON dict mapping domain suffixes to TTLs in seconds.
    Example: {"news.ycombinator.com": 300, "docs.python.org": 86400}
    Returns empty dict on parse failure.
    """
    raw = _settings.scrape_cache_domain_ttls
    if not raw or raw == "{}":
        return {}
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        logger.warning("Invalid SCRAPE_CACHE_DOMAIN_TTLS value, using empty")
        return {}


def _resolve_cache_ttl(url: str, stability_multiplier: float = 1.0) -> int:
    """Resolve the cache TTL for a URL based on per-domain policies.

    Matches the URL's hostname against domain suffix patterns
    (longest match wins). Falls back to the global SCRAPE_CACHE_TTL.
    Applies the stability multiplier and clamps to min/max bounds.
    """
    domain_ttls = _parse_domain_ttls()
    from common.url import extract_domain

    hostname = extract_domain(url)

    # Longest suffix match
    matched_ttl: int | None = None
    matched_len = 0
    for pattern, ttl in domain_ttls.items():
        if (hostname == pattern or hostname.endswith("." + pattern)) and len(
            pattern
        ) > matched_len:
            matched_ttl = ttl
            matched_len = len(pattern)

    base_ttl = matched_ttl if matched_ttl is not None else SCRAPE_CACHE_TTL
    adjusted = int(base_ttl * stability_multiplier)
    return max(SCRAPE_CACHE_MIN_TTL, min(adjusted, SCRAPE_CACHE_MAX_TTL))


def _merge_cache_metadata(fresh_result: dict, cached: dict) -> dict:
    """Merge metadata from a previous cache entry into a fresh result.

    Preserves cross-session tracking fields (fetch_count, first_fetched_at,
    change_count) across cache generations.
    """
    fresh_result["fetch_count"] = cached.get("fetch_count", 0) + 1
    fresh_result["first_fetched_at"] = cached.get("first_fetched_at", time.time())
    fresh_result["last_checked_at"] = time.time()
    fresh_result["change_count"] = cached.get("change_count", 0)
    return fresh_result


def _enrich_cache_entry(
    result: dict,
    url: str,
    etag: str | None = None,
    last_modified: str | None = None,
    prior_entry: dict | None = None,
) -> dict:
    """Add freshness metadata to a result dict before caching.

    Enriches the result with content_hash, ETag, Last-Modified, source_tier,
    and tracking fields (fetch_count, first_fetched_at, change_count).

    When a prior_entry is provided, computes content-change detection by
    comparing content hashes and updates change_count accordingly.
    """
    # Copy through http headers if provided
    if etag:
        result["etag"] = etag
    if last_modified:
        result["last_modified"] = last_modified

    # Source tier for revalidation strategy resolution
    result["source_tier"] = result.get("source", "")

    # Content hash for change detection
    markdown = result.get("markdown", "")
    new_hash = _compute_content_hash(markdown)
    result["content_hash"] = new_hash

    # Tracking fields
    now = time.time()
    result["last_checked_at"] = now

    if prior_entry:
        result["fetch_count"] = prior_entry.get("fetch_count", 0) + 1
        result["first_fetched_at"] = prior_entry.get("first_fetched_at", now)

        # Content-change detection
        old_hash = prior_entry.get("content_hash", "")
        if old_hash and new_hash != old_hash:
            result["change_count"] = prior_entry.get("change_count", 0) + 1
        else:
            result["change_count"] = prior_entry.get("change_count", 0)
    else:
        result["fetch_count"] = 1
        result["first_fetched_at"] = now
        result["change_count"] = 0

    return result


async def _set_cache_raw(key: str, payload: dict, ttl: int) -> None:
    """Low-level Valkey cache write. Assumes client is connected."""
    client = await _get_cache_client()
    if not client:
        return
    try:
        await client.setex(key, ttl, json.dumps(payload))
    except Exception as e:
        logger.debug("Cache write failed for key=%s: %s", key, e)


async def _set_cache(url: str, result: dict, prior_entry: dict | None = None) -> None:
    """Store a scrape result with intelligent cache metadata.

    Enriches the result with freshness tracking fields (content_hash, ETag,
    Last-Modified, source_tier, fetch_count, change_count) and applies
    per-domain TTL resolution. Adapter results are excluded from caching.

    Extracts ETag and Last-Modified from the result dict itself (set by
    tier functions from HTTP response headers).

    Safe to call even if Valkey is unavailable — silently no-ops.
    """
    client = await _get_cache_client()
    if not client:
        return

    # Skip caching adapter results (they use external APIs with their own state)
    source = result.get("source", "")
    if source == "adapter":
        return

    try:
        key = _scrape_cache_key(url)

        # Extract ETag/Last-Modified from result dict (set by tier functions)
        etag = result.pop("etag", None)
        last_modified = result.pop("last_modified", None)

        # Enrich with freshness metadata
        enriched = _enrich_cache_entry(
            result,
            url,
            etag=etag,
            last_modified=last_modified,
            prior_entry=prior_entry,
        )

        # Determine stability multiplier for TTL
        change_count = enriched.get("change_count", 0)
        if change_count == 0 and prior_entry is not None:
            # Content unchanged since prior fetch → stable bonus
            stability = SCRAPE_CACHE_STABLE_MULTIPLIER
        elif change_count >= 3:
            # Volatile content → cap TTL
            stability = 0.5
        else:
            stability = 1.0

        ttl = _resolve_cache_ttl(url, stability_multiplier=stability)

        # Apply volatile cap if content is frequently changing
        if change_count >= 5:
            ttl = min(ttl, SCRAPE_CACHE_VOLATILE_CAP)

        await client.setex(key, ttl, json.dumps(enriched))
        logger.info(
            "Cached scrape result for %s (key=%s, ttl=%ds, fetch_count=%d, change_count=%d)",
            url,
            key,
            ttl,
            enriched.get("fetch_count", 0),
            enriched.get("change_count", 0),
        )
    except Exception as e:
        logger.debug("Cache write failed for %s: %s", url, e)


async def _conditional_revalidate(
    url: str, etag: str | None, last_modified: str | None
) -> tuple[bool, dict | None]:
    """Send a conditional GET to check whether cached content is still fresh.

    Uses If-None-Match and If-Modified-Since headers. Returns a (fresh, result)
    tuple:
        (True, None)      — 304 Not Modified, cache is fresh
        (False, result)   — 200 OK, content changed, result carries new content
        (False, None)     — connection/timeout error, can't determine freshness
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
    }
    if etag:
        headers["If-None-Match"] = etag if etag.startswith('"') else f'"{etag}"'
    if last_modified:
        headers["If-Modified-Since"] = last_modified

    try:
        async with source_httpx_client(
            follow_redirects=True,
            timeout=15,
            headers=headers,
        ) as client:
            resp = await client.get(url)
            if resp.status_code == 304:
                return (True, None)
            elif resp.status_code == 200:
                # Extract content, prefer markdown-like output
                ct = resp.headers.get("content-type", "")
                if _is_binary_content_type(ct):
                    result = _make_download_payload(url, resp.content, ct)
                else:
                    result = {
                        "markdown": resp.text,
                        "source": "revalidation",
                        "url": url,
                    }
                # Store response headers for next revalidation round
                new_etag = resp.headers.get("etag")
                new_lm = resp.headers.get("last-modified")
                if new_etag:
                    result["etag"] = new_etag
                if new_lm:
                    result["last_modified"] = new_lm
                return (False, result)
            else:
                logger.debug(
                    "Unexpected revalidation status %d for %s",
                    resp.status_code,
                    url,
                )
                return (False, None)
    except Exception as e:
        logger.debug("Revalidation failed for %s: %s", url, e)
        return (False, None)
