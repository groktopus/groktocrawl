"""Three-tier fetch strategy for turning URLs into clean markdown.

Tier 1: /llms.txt — entire site as markdown, one GET.
Tier 2: Accept: text/markdown — per-page markdown via content negotiation.
Tier 3: Playwright render + readability extraction (heavyweight).
"""

import asyncio
import hashlib
import json
import logging
import os
import re
import socket
import time
from dataclasses import dataclass
from ipaddress import ip_address, ip_network

import httpx

from common.url import extract_domain, normalize_url

from .extract import assess_quality
from .metadata import extract_all_metadata
from .settings import load_settings

logger = logging.getLogger(__name__)

_settings = load_settings()
FLARE_SOLVERR_URL = _settings.flare_solverr_url

# ── Intelligent scrape cache (ADR-0019) ─────────────────────────
# Cache freshness revalidation settings
SCRAPE_CACHE_TTL = _settings.scrape_cache_ttl
SCRAPE_CACHE_MIN_TTL = _settings.scrape_cache_min_ttl
SCRAPE_CACHE_MAX_TTL = _settings.scrape_cache_max_ttl
SCRAPE_CACHE_STABLE_MULTIPLIER = _settings.scrape_cache_stable_multiplier
SCRAPE_CACHE_VOLATILE_CAP = _settings.scrape_cache_volatile_cap

# ── Proxy configuration ─────────────────────────────────────────
# SCRAPER_PROXY_URL is an opt-in env var for residential/mobile IP rotation.
# When set, httpx requests (Tiers 1-2) and Playwright browser contexts (Tier 3)
# route through the proxy. Playwright uses context-level proxy assignment
# (browser.new_context(proxy=...)) for job isolation.
# If the proxy is unreachable, the scrape retries without proxy and logs a WARN.
# Format: http://user:pass@host:port
# Unset or empty = no proxy (default).
SCRAPER_PROXY_URL = _settings.scraper_proxy_url


def _get_httpx_proxies() -> str | None:
    """Get httpx-compatible proxy URL from env var.

    httpx 'proxy' parameter expects a single URL string.
    """
    return SCRAPER_PROXY_URL or None


def _get_playwright_proxy() -> dict | None:
    """Get Playwright-compatible proxy config from env var.

    Parses http://user:pass@host:port into Playwright's format.
    Handles URLs with no explicit port and IPv6 addresses (restores brackets).
    Uses context-level proxy (browser.new_context(proxy=...)) for job isolation.
    """
    if not SCRAPER_PROXY_URL:
        return None
    from urllib.parse import urlparse

    parsed = urlparse(SCRAPER_PROXY_URL)

    # Re-bracket IPv6 hostnames (urlparse strips them)
    hostname = parsed.hostname or "localhost"
    host = f"[{hostname}]" if ":" in hostname else hostname

    # Only include port when explicitly present
    if parsed.port is not None:
        server = f"{parsed.scheme}://{host}:{parsed.port}"
    else:
        server = f"{parsed.scheme}://{host}"

    config = {"server": server}
    if parsed.username:
        config["username"] = parsed.username
    if parsed.password:
        config["password"] = parsed.password
    return config


def _redact_proxy_url(url: str) -> str:
    """Redact password from a proxy URL for safe logging.

    Uses urlparse to extract the username, preserving the host:port structure
    while masking the password.
    """
    if not url:
        return url
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if parsed.username:
        hostname = parsed.hostname or ""
        port = f":{parsed.port}" if parsed.port is not None else ""
        return f"{parsed.username}:***@{hostname}{port}"
    return url


# ── Valkey scrape result cache ──────────────────────────────────

_cache_client = None  # Module-level lazy singleton


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
    client = await _get_cache_client()
    if not client:
        return None
    try:
        key = _scrape_cache_key(url)
        cached_raw = await client.get(key)
        if not cached_raw:
            return None

        cached = json.loads(cached_raw)
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
                    "Cache revalidated (304) for %s, extended TTL to %ds", url, new_ttl
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
                logger.warning("Revalidation failed for %s, serving stale cache", url)
                return cached

        # Fast tiers or no ETag/LM — return cached, caller handles revalidation
        return cached
    except Exception as e:
        logger.debug("Cache read failed for %s: %s", url, e)
    return None


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
        async with httpx.AsyncClient(
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


from .adapters.base import AdapterContext, get_registry

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
    from urllib.parse import urlparse

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


# ── Bot challenge detection (title/URL level) ──────────────────
CLOUDFLARE_INDICATORS = [
    "Just a moment",
    "Checking your browser",
    "DDoS protection by",
    "cf-browser-verification",
    "challenge-platform",
]

DDOS_GUARD_INDICATORS = [
    "DDoS-Guard",
    "DDOS-GUARD",
    "ddos-guard",
    "Checking your browser before accessing",
    ".well-known/ddos-guard",
]

# ── Substack session/channel frame redirect detection ──────────
SUBSTACK_REDIRECT_PATTERNS = [
    "substack.com/session-attribution-frame",
    "substack.com/channel-frame",
    "substack.com/iframe",
    "googletagmanager.com/ns.html",
]

# ── Bot challenge and redirect detection (title/URL level) ─────


def _is_bot_challenge(title: str, url: str) -> bool:
    """Check if the page title or URL indicates a bot challenge page.

    Mirrors browser-svc's _is_bot_challenge() logic.
    """
    for indicator in CLOUDFLARE_INDICATORS:
        if indicator.lower() in title.lower():
            return True
    if "cf_chl" in url.lower() or "challenge-platform" in url.lower():
        return True
    for indicator in DDOS_GUARD_INDICATORS:
        if indicator.lower() in title.lower():
            return True
    return "ddos-guard" in url.lower() or "/.well-known/ddos-guard" in url.lower()


def _is_substack_redirect(url: str) -> bool:
    """Check if the URL indicates a Substack session/channel frame redirect."""
    return any(pattern in url.lower() for pattern in SUBSTACK_REDIRECT_PATTERNS)


# ── Barrier classification (replaces _looks_suspicious) ──────────


@dataclass
class BarrierInfo:
    """Structured result of barrier classification on a scraped page."""

    detected: bool
    barrier_type: (
        str | None
    )  # "cloudflare", "ddos-guard", "captcha", "rate-limit", "substack-redirect", "empty", "suspicious", None
    confidence: float
    detail: str = ""
    title: str = ""


def _classify_barrier(
    title: str, url: str, content: str, html: str | None = None
) -> BarrierInfo:
    """Classify whether a scraped page is a barrier/challenge page.

    Replaces the old boolean _looks_suspicious() with structured,
    multi-signal classification. Returns a BarrierInfo dataclass
    with detected flag, barrier type, confidence score, and detail.

    Confidence is derived from the number of distinct matched signals:
      1 signal  → 0.70
      2 signals → 0.85
      3+ signals → 0.95
    """
    if not content and not html:
        return BarrierInfo(
            detected=True,
            barrier_type="empty",
            confidence=0.95,
            detail="No content returned",
            title=title,
        )

    signals: list[str] = []
    content_lower = content.lower() if content else ""
    title_lower = title.lower() if title else ""
    url_lower = url.lower() if url else ""
    html_lower = html.lower() if html else ""

    # ── Signal: Empty content ─────────────────────────────────
    if len(content) < 100:
        signals.append("empty")

    # ── Signal: Title-based Cloudflare detection ──────────────
    for indicator in CLOUDFLARE_INDICATORS:
        if indicator.lower() in title_lower:
            signals.append("cloudflare-title")
            break

    # ── Signal: Explicit title match ──────────────────────────
    if (
        "attention required" in title_lower or "403 forbidden" in title_lower
    ) and "cloudflare" not in signals:
        signals.append("cloudflare-title")

    # ── Signal: URL-based Cloudflare detection ────────────────
    if "cf_chl" in url_lower or "challenge-platform" in url_lower:
        signals.append("cloudflare-url")

    # ── Signal: DDoS-Guard title detection ────────────────────
    for indicator in DDOS_GUARD_INDICATORS:
        if indicator.lower() in title_lower:
            signals.append("ddos-guard-title")
            break

    # ── Signal: DDoS-Guard URL detection ──────────────────────
    if "ddos-guard" in url_lower or "/.well-known/ddos-guard" in url_lower:
        signals.append("ddos-guard-url")

    # ── Signal: Captcha detection in content ──────────────────
    if "hcaptcha" in content_lower or "recaptcha" in content_lower:
        signals.append("captcha")

    # ── Signal: Rate-limit detection in content ───────────────
    if "rate limit" in content_lower or "too many requests" in content_lower:
        signals.append("rate-limit")

    # ── Signal: Substack redirect ─────────────────────────────
    for pattern in SUBSTACK_REDIRECT_PATTERNS:
        if pattern in url_lower or (html and pattern in html_lower):
            signals.append("substack-redirect")
            break

    # ── Signal: Indicator words in content (fallback) ─────────
    if not signals:
        for indicator in (
            CLOUDFLARE_INDICATORS + DDOS_GUARD_INDICATORS + SUBSTACK_REDIRECT_PATTERNS
        ):
            if indicator.lower() in content_lower:
                signals.append("content-match")
                break

    # ── Confidence scoring ────────────────────────────────────
    signal_count = len(set(signals))
    if signal_count == 0:
        return BarrierInfo(
            detected=False,
            barrier_type=None,
            confidence=0.0,
            detail="No barrier signals detected",
            title=title,
        )

    confidence = min(0.50 + (signal_count * 0.20), 0.95)

    # ── Determine the primary barrier type ────────────────────
    barrier_type: str | None = None
    for keyword, btype in [
        ("cloudflare", "cloudflare"),
        ("ddos-guard", "ddos-guard"),
        ("captcha", "captcha"),
        ("rate-limit", "rate-limit"),
        ("substack-redirect", "substack-redirect"),
        ("empty", "empty"),
        ("content-match", "suspicious"),
    ]:
        if any(keyword in s for s in signals):
            barrier_type = btype
            break

    detail_parts = []
    for s in sorted(set(signals)):
        detail_parts.append(s)
    detail = f"Matched signals: {', '.join(detail_parts)}"

    return BarrierInfo(
        detected=True,
        barrier_type=barrier_type,
        confidence=confidence,
        detail=detail,
        title=title,
    )


# ── Embedded content detection ─────────────────────────────────
# Extensions and domain patterns that suggest an iframe/embed points
# to downloadable document content rather than another web page.
EMBEDDED_CONTENT_EXTENSIONS = {
    ".pdf",
    ".epub",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
    ".zip",
    ".tar",
    ".gz",
}
EMBEDDED_CONTENT_DOMAINS = {
    "sci-hub",
    "sci.bban",
    "docdrop",
    "academia",
    "researchgate",
    "arxiv.org",
    "cdn.",
}


def _has_embedded_content(html: str) -> bool:
    """Check if page HTML contains iframe/embed/object pointing to document content.

    Uses lightweight string matching — no HTML parser needed.
    Returns True if the page appears to be a portal to document content elsewhere.
    """
    if not html:
        return False
    html_lower = html.lower()
    # Quick reject: no iframe, embed, or object tags at all
    if not any(tag in html_lower for tag in ("<iframe", "<embed", "<object")):
        return False
    # Check for document extensions in src/data attributes
    for ext in EMBEDDED_CONTENT_EXTENSIONS:
        if ext in html_lower:
            return True
    # Check for known document-serving domains
    for domain in EMBEDDED_CONTENT_DOMAINS:
        if domain in html_lower:
            return True
    # Check for common document URL patterns
    return "/pdf/" in html_lower or "/download/" in html_lower


def _looks_like_markdown(text: str) -> bool:
    """Heuristic: does the response look like markdown vs HTML?"""
    if not text:
        return False
    # If the first non-whitespace character isn't '<', it's probably not HTML
    stripped = text.strip()
    if not stripped:
        return False
    # Check for markdown indicators: headings, lists, code fences, links
    md_indicators = 0
    for line in stripped[:2000].split("\n"):
        line = line.strip()
        if line.startswith("# ") or line.startswith("## ") or line.startswith("### "):
            md_indicators += 1
        if line.startswith("- ") or line.startswith("* "):
            md_indicators += 1
        if line.startswith("```"):
            md_indicators += 1
        if re.match(r"^\[.+\]\(.+\)", line):
            md_indicators += 1
    return md_indicators >= 3


async def fetch_via_llms_txt(url: str, client: httpx.AsyncClient) -> dict | None:
    """Tier 1: Check for /llms.txt at the site root."""
    llms_url = f"{extract_domain(url, include_scheme=True)}/llms.txt"
    try:
        resp = await client.get(llms_url, follow_redirects=True, timeout=10)
        if (
            resp.status_code == 200
            and resp.text.strip()
            and (_looks_like_markdown(resp.text) or resp.text.strip().startswith("#"))
        ):
            logger.info("Tier 1 hit: /llms.txt at %s", llms_url)
            result = {"markdown": resp.text, "source": "llms.txt", "url": llms_url}
            # Pass through ETag/Last-Modified for intelligent caching
            etag = resp.headers.get("etag")
            lm = resp.headers.get("last-modified")
            if etag:
                result["etag"] = etag
            if lm:
                result["last_modified"] = lm
            return result
    except Exception as e:
        logger.debug("Tier 1 miss for %s: %s", llms_url, e)
    return None


async def fetch_via_content_negotiation(
    url: str, client: httpx.AsyncClient
) -> dict | None:
    """Tier 2: Request with Accept: text/markdown header.

    Also checks for binary content types and short-circuits to a download payload.
    """
    try:
        resp = await client.get(
            url,
            headers={"Accept": "text/markdown, text/plain;q=0.9, */*;q=0.8"},
            follow_redirects=True,
            timeout=15,
        )
        if resp.status_code == 200:
            # Check for binary content first
            ct = resp.headers.get("content-type", "")
            if _is_binary_content_type(ct):
                logger.info("Tier 2 binary hit: %s (%s)", url, ct)
                return _make_download_payload(url, resp.content, ct)
            # Standard markdown detection
            if _looks_like_markdown(resp.text):
                logger.info("Tier 2 hit: content negotiation for %s", url)
                result = {
                    "markdown": resp.text,
                    "source": "content-negotiation",
                    "url": url,
                }
                # Pass through ETag/Last-Modified for intelligent caching
                etag = resp.headers.get("etag")
                lm = resp.headers.get("last-modified")
                if etag:
                    result["etag"] = etag
                if lm:
                    result["last_modified"] = lm
                return result
    except Exception as e:
        logger.debug("Tier 2 miss for %s: %s", url, e)
    return None


# ── Private IP / SSRF protection ─────────────────────────────────

_PRIVATE_NETWORKS = [
    ip_network("10.0.0.0/8"),
    ip_network("172.16.0.0/12"),
    ip_network("192.168.0.0/16"),
    ip_network("127.0.0.0/8"),
    ip_network("::1/128"),
    ip_network("169.254.0.0/16"),
    ip_network("0.0.0.0/8"),
    ip_network("100.64.0.0/10"),
    ip_network("198.18.0.0/15"),
    ip_network("240.0.0.0/4"),
]

_METADATA_IPS = {
    ip_address("169.254.169.254"),
    ip_address("fd00:ec2::254"),
}

_PRIVATE_HOSTNAME_SUFFIXES = [
    ".docker.internal",
]


def _resolve_to_ips(hostname: str) -> list:
    try:
        addrinfo = socket.getaddrinfo(hostname, None)
        ips = set()
        for _family, _, _, _, sockaddr in addrinfo:
            try:
                ips.add(ip_address(sockaddr[0]))
            except ValueError:
                continue
        return list(ips)
    except socket.gaierror:
        return []


def _is_private_url(url: str) -> tuple[bool, str]:
    """Check if a URL targets a private/internal IP or hostname.

    Returns (is_private, reason) tuple. Shared logic with browser-svc.

    Delegates to the shared ``common.url.is_private_host``
    for the actual check, then maps the boolean result back to the
    ``(bool, str)`` tuple format.
    """
    from common.url import is_private_host as _shared_is_private

    return (True, "Private or internal URL") if _shared_is_private(url) else (False, "")


async def _playwright_fetch_with_proxy(
    url: str,
    proxy: dict | None,
) -> dict | None:
    """Inner playwright fetch, called with or without proxy.

    Returns the scrape result dict or None. Does NOT wrap in try/except
    for the outer browser lifecycle — callers handle that.
    """
    from playwright.async_api import async_playwright

    from .cookie_store import inject_cookies, store_cookies
    from .stealth import create_stealth_browser, create_stealth_context

    proxy_label = proxy.get("server", "none") if proxy else "none"
    logger.info("Playwright proxy: %s", proxy_label)

    context_kwargs = {}
    if proxy:
        context_kwargs["proxy"] = proxy  # context-level, not launch-level

    async with async_playwright() as p:
        browser = await create_stealth_browser(p)
        context = await create_stealth_context(browser, **context_kwargs)
        page = await context.new_page()
        try:
            # Security: reject private/internal destination URLs
            is_private, reason = _is_private_url(url)
            if is_private:
                logger.warning("Blocked navigation to private URL %s: %s", url, reason)
                return None

            # Inject cached Cloudflare clearance cookies before navigation
            await inject_cookies(url, context)

            # Navigate with networkidle — same strategy as browser-svc
            await page.goto(url, wait_until="networkidle", timeout=45000)

            # Check for bot challenges (Cloudflare / DDoS-Guard)
            title = await page.title()
            current_url = page.url
            if _is_bot_challenge(title, current_url):
                logger.info(
                    "Bot challenge detected on %s, waiting for resolution...", url
                )
                await page.wait_for_timeout(8000)
                title = await page.title()
                current_url = page.url
                if _is_bot_challenge(title, current_url):
                    logger.warning("Bot challenge persisted after wait for %s", url)

            # Check for Substack session/channel frame redirect
            if _is_substack_redirect(current_url):
                logger.info(
                    "Substack redirect detected on %s (-> %s), waiting for content...",
                    url,
                    current_url,
                )
                await page.wait_for_timeout(5000)
                current_url = page.url
                if _is_substack_redirect(current_url):
                    logger.warning("Substack redirect persisted for %s", url)

            # SPA content retry
            html = await page.content()
            markdown = html_to_markdown(html) if html else ""

            if (
                not markdown
                or len(markdown) < 500
                or _classify_barrier(title, url, markdown, html).detected
            ):
                for attempt in range(2):
                    logger.info(
                        "SPA retry %d for %s (markdown: %d chars)",
                        attempt + 1,
                        url,
                        len(markdown),
                    )
                    await page.evaluate(
                        "window.scrollTo(0, document.body.scrollHeight)"
                    )
                    await page.wait_for_timeout(3000)

                    html = await page.content()
                    markdown = html_to_markdown(html) if html else ""
                    if (
                        markdown
                        and len(markdown) >= 500
                        and not _classify_barrier(title, url, markdown, html).detected
                    ):
                        logger.info(
                            "SPA retry %d succeeded for %s (%d chars)",
                            attempt + 1,
                            url,
                            len(markdown),
                        )
                        break

        finally:
            await browser.close()

    if html:
        markdown = html_to_markdown(html)
        if markdown and len(markdown) > 50:
            barrier = _classify_barrier(title, url, markdown, html)
            if barrier.detected and barrier.confidence > 0.7:
                logger.warning(
                    "Barrier detected in playwright result for %s: %s (confidence: %.2f)",
                    url,
                    barrier.barrier_type,
                    barrier.confidence,
                )
                return {
                    "error": f"Barrier detected: {barrier.barrier_type} (confidence: {barrier.confidence:.2f})",
                    "barrier": {
                        "detected": True,
                        "type": barrier.barrier_type,
                        "confidence": barrier.confidence,
                        "detail": barrier.detail,
                    },
                    "markdown": "",
                    "source": "barrier-detection",
                    "url": url,
                }

            logger.info("Tier 3 hit: playwright render for %s", url)
            await store_cookies(url, context)
            return {
                "markdown": markdown,
                "source": "playwright",
                "url": url,
                "raw_html_start": html,
            }
    return None


async def fetch_via_playwright(url: str) -> dict | None:
    """Tier 3: Render with stealth Playwright, then extract main content.

    Uses the same stealth configuration as browser-svc to avoid headless
    detection by Substack, Cloudflare JS challenges, and similar mechanisms.

    Implements fail-open proxy retry: if proxy is configured and unreachable,
    retries without proxy and logs a WARN. Proxy identity is logged per-scrape
    so operators can distinguish "proxy returned garbage" from "site changed".

    This requires playwright and chromium to be installed.
    Falls back gracefully if playwright is not available.
    """
    try:
        pw_proxy = _get_playwright_proxy()

        # Try with proxy first — wrap in its own try/except so exceptions
        # from unreachable proxies trigger the fail-open retry rather than
        # falling through to the generic "Tier 3 miss" handler
        try:
            result = await _playwright_fetch_with_proxy(url, pw_proxy)
        except Exception as e:
            logger.warning(
                "Proxy (%s) failed for %s: %s",
                pw_proxy.get("server", "unknown") if pw_proxy else "none",
                url,
                e,
            )
            result = None

        if result is not None:
            return result

        # Fail-open: if proxy was configured, retry without it
        if pw_proxy:
            proxy_identity = pw_proxy.get("server", "unknown")
            logger.warning(
                "Proxy (%s) unreachable or failed for %s — retrying without proxy (fail-open)",
                proxy_identity,
                url,
            )
            result = await _playwright_fetch_with_proxy(url, None)
            if result is not None:
                result["_proxy_failover"] = True
                result["_proxy_identity"] = proxy_identity
                return result
    except ImportError:
        logger.warning("Playwright not installed; skipping Tier 3")
    except Exception as e:
        logger.warning("Tier 3 miss for %s: %s", url, e)
    return None


async def fetch_via_flaresolverr(url: str) -> dict | None:
    """Tier 3.5: Route through FlareSolverr for hard Cloudflare challenges.

    Requires the flare-solverr service to be running (profile-gated in
    docker-compose.yml). Gracefully falls back if unavailable.
    """
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{FLARE_SOLVERR_URL}/v1",
                json={
                    "cmd": "request.get",
                    "url": url,
                    "maxTimeout": 60000,
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                solution = data.get("solution", {})
                if solution.get("status") == 200:
                    html = solution.get("response", "")
                    if html:
                        markdown = html_to_markdown(html)
                        if markdown and len(markdown) > 50:
                            # ── Barrier check ─────────────────
                            barrier = _classify_barrier("", url, markdown, html)
                            if barrier.detected and barrier.confidence > 0.7:
                                logger.warning(
                                    "Barrier detected in flare-solverr result for %s: %s (confidence: %.2f)",
                                    url,
                                    barrier.barrier_type,
                                    barrier.confidence,
                                )
                                return {
                                    "error": f"Barrier detected: {barrier.barrier_type} (confidence: {barrier.confidence:.2f})",
                                    "barrier": {
                                        "detected": True,
                                        "type": barrier.barrier_type,
                                        "confidence": barrier.confidence,
                                        "detail": barrier.detail,
                                    },
                                    "markdown": "",
                                    "source": "barrier-detection",
                                    "url": url,
                                }

                            logger.info("Tier 3.5 hit: flare-solverr for %s", url)
                            return {
                                "markdown": markdown,
                                "source": "flare-solverr",
                                "url": url,
                            }
    except (httpx.ConnectError, httpx.TimeoutException):
        logger.debug("FlareSolverr not available for %s", url)
    except Exception as e:
        logger.warning("FlareSolverr failed for %s: %s", url, e)
    return None


def html_to_markdown(html: str) -> str:
    """Convert HTML to clean markdown using readability + markdownify."""
    try:
        from markdownify import markdownify as md
        from readability import Document

        doc = Document(html)
        summary = doc.summary()
        # Clean up readability's artifacts
        markdown = md(summary, heading_style="ATX", strip=["script", "style"])
        # Collapse multiple blank lines
        markdown = re.sub(r"\n{3,}", "\n\n", markdown)
        return markdown.strip()
    except Exception as e:
        logger.error("HTML-to-markdown conversion failed: %s", e)
        # Fallback: try BeautifulSoup for text extraction
        try:
            from bs4 import BeautifulSoup

            soup = BeautifulSoup(html, "html.parser")
            # Remove script/style
            for tag in soup(["script", "style", "nav", "footer", "header"]):
                tag.decompose()
            text = soup.get_text(separator="\n", strip=True)
            return text[:10000]  # Limit to 10K chars as fallback
        except Exception:
            return html[:5000]  # Last resort raw truncation


async def _fetch_via_browser_svc(url: str) -> dict | None:
    """Fallback: use the browser-svc API to navigate and extract content.

    The browser-svc's Playwright configuration is able to handle sites
    that the scraper-svc's Tier 3 cannot (e.g., Substack redirect chains).

    Browser-svc is available at http://browser-svc:8012.
    """
    browser_svc_url = _settings.browser_svc_url
    session_id = None
    try:
        # Create a browser session
        async with httpx.AsyncClient(timeout=30) as client:
            create_resp = await client.post(
                f"{browser_svc_url}/browsers",
                json={"ttl": 60},  # Short TTL, we only need one page load
            )
            if create_resp.status_code != 200:
                logger.warning(
                    "Browser-svc session creation failed: %d", create_resp.status_code
                )
                return None
            session_id = create_resp.json().get("id")
            if not session_id:
                return None

            # Navigate to the URL
            nav_resp = await client.post(
                f"{browser_svc_url}/browsers/{session_id}/execute",
                json={"action": "navigate", "url": url, "timeout": 45000},
            )
            if not nav_resp.json().get("success"):
                logger.warning("Browser-svc navigation failed for %s", url)
                return None

            # Get page content (HTML)
            content_resp = await client.post(
                f"{browser_svc_url}/browsers/{session_id}/execute",
                json={"action": "getContent"},
            )
            if not content_resp.json().get("success"):
                return None

            result = content_resp.json()["result"]
            html = None

            # Try to extract article text via executeScript first
            text_resp = await client.post(
                f"{browser_svc_url}/browsers/{session_id}/execute",
                json={
                    "action": "executeScript",
                    "script": (
                        "document.querySelector('article') "
                        "? document.querySelector('article').innerText "
                        ": document.body.innerText"
                    ),
                },
            )
            if text_resp.json().get("success"):
                text = text_resp.json()["result"].get("script_result", "")
                if text and len(text) > 200:
                    # ── Barrier check ─────────────────────────
                    barrier = _classify_barrier("", url, text, None)
                    if barrier.detected and barrier.confidence > 0.7:
                        logger.warning(
                            "Barrier detected in browser-svc result for %s: %s (confidence: %.2f)",
                            url,
                            barrier.barrier_type,
                            barrier.confidence,
                        )
                        return {
                            "error": f"Barrier detected: {barrier.barrier_type} (confidence: {barrier.confidence:.2f})",
                            "barrier": {
                                "detected": True,
                                "type": barrier.barrier_type,
                                "confidence": barrier.confidence,
                                "detail": barrier.detail,
                            },
                            "markdown": "",
                            "source": "barrier-detection",
                            "url": url,
                        }

                    logger.info(
                        "Browser-svc fallback hit for %s (article text: %d chars)",
                        url,
                        len(text),
                    )
                    return {
                        "markdown": text,
                        "source": "browser-svc",
                        "url": url,
                    }

            # Fallback: get HTML and convert to markdown
            html = result.get("html_length") and (
                await _get_browser_page_content(browser_svc_url, session_id)
            )
            if html:
                markdown = html_to_markdown(html)
                if markdown and len(markdown) > 50:
                    # ── Barrier check ─────────────────────────
                    barrier = _classify_barrier("", url, markdown, html)
                    if barrier.detected and barrier.confidence > 0.7:
                        logger.warning(
                            "Barrier detected in browser-svc HTML result for %s: %s (confidence: %.2f)",
                            url,
                            barrier.barrier_type,
                            barrier.confidence,
                        )
                        return {
                            "error": f"Barrier detected: {barrier.barrier_type} (confidence: {barrier.confidence:.2f})",
                            "barrier": {
                                "detected": True,
                                "type": barrier.barrier_type,
                                "confidence": barrier.confidence,
                                "detail": barrier.detail,
                            },
                            "markdown": "",
                            "source": "barrier-detection",
                            "url": url,
                        }

                    logger.info(
                        "Browser-svc fallback hit for %s (HTML: %d chars)",
                        url,
                        len(html),
                    )
                    return {
                        "markdown": markdown,
                        "source": "browser-svc",
                        "url": url,
                    }

    except Exception as e:
        logger.warning("Browser-svc fallback failed for %s: %s", url, e)
    finally:
        # Clean up the browser session
        if session_id:
            try:
                async with httpx.AsyncClient(timeout=5) as c:
                    await c.delete(f"{browser_svc_url}/browsers/{session_id}")
            except Exception:
                pass

    return None


async def _get_browser_page_content(
    browser_svc_url: str, session_id: str
) -> str | None:
    """Get the full page HTML from a browser-svc session via executeScript."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{browser_svc_url}/browsers/{session_id}/execute",
                json={
                    "action": "executeScript",
                    "script": "document.documentElement.outerHTML",
                },
            )
            if resp.json().get("success"):
                return resp.json()["result"].get("script_result", "")
    except Exception:
        pass
    return None


def _add_quality(result: dict, html: str = "", title: str = "") -> dict:
    """Assess content quality and add quality metadata to a scrape result dict.

    Lightweight post-extraction quality check — runs after each successful tier.
    Quality score is non-blocking; consumers set their own tolerance.
    """
    markdown = result.get("markdown", "")
    url = result.get("url", "")
    quality = assess_quality(markdown, html=html, url=url, title=title)
    result["quality"] = quality
    return result


def _enrich_with_metadata(result: dict, html: str = "") -> dict:
    """Extract structured metadata (JSON-LD, OG, Twitter, meta) from raw HTML.

    Pure parsing — no additional fetches. Runs after each tier that produces
    raw HTML. Results without available HTML get empty metadata fields.

    Metadata is best-effort: JSON-LD may be absent, OG tags may be minimal.
    Consumers should treat all fields as optional.
    """
    if not html and not result.get("raw_html_start"):
        result["metadata"] = {"json_ld": [], "og": {}, "twitter": {}, "meta": {}}
        return result

    source_html = html or result.get("raw_html_start", "")
    metadata = extract_all_metadata(source_html)

    # If the full HTML is not available, raw_html_start may be truncated.
    # That's fine — JSON-LD blocks and meta tags are usually in <head>.
    result["metadata"] = metadata
    return result


QA_MIN_QUALITY_THRESHOLD = _settings.qa_min_quality_threshold


def _quality_acceptable(result: dict) -> bool:
    """Check if a scrape result's quality is above the degradation threshold.

    Results without a quality field (e.g., barrier detections) are returned
    as-is without degradation.
    """
    quality = result.get("quality")
    if quality is None:
        return True  # No quality assessment available — return as-is
    score = quality.get("score", 1.0)
    return score >= QA_MIN_QUALITY_THRESHOLD


async def _maybe_degrade(
    result: dict, tier_label: str, best_effort: list
) -> dict | None:
    """Check quality and decide whether to return or degrade to next tier.

    If quality is acceptable, returns the result dict (caller should return it).
    If quality is below threshold, adds to best_effort list and returns None
    (caller should fall through to next tier).

    Returns:
        The result dict if acceptable, None if degraded.
    """
    result = _add_quality(result)
    if _quality_acceptable(result):
        return result
    bq = result.get("quality", {})
    bs = bq.get("score", 0.0)
    logger.info(
        "Degrading from %s: quality=%.2f < %.2f",
        tier_label,
        bs,
        QA_MIN_QUALITY_THRESHOLD,
    )
    result["_degraded_from"] = tier_label
    best_effort.append(result)
    return None


async def _politeness_check_and_delay(url: str) -> tuple[bool, dict | None]:
    """Check politeness policy for a URL.

    Returns (proceed, error_dict):
        (True, None) — proceed with the request
        (False, error_dict) — blocked by robots.txt, caller should return error_dict

    When action is "delay", sleeps the required time before returning.
    No-op when politeness is disabled.
    """
    from .politeness import get_manager

    manager = get_manager()
    if not manager.enabled:
        return True, None

    result = await manager.check(url)
    if result.action == "blocked":
        logger.info("Politeness blocked %s: %s", url, result.reason)
        metadata = manager.get_politeness_metadata(url)
        return False, {
            "error": f"Blocked by politeness: {result.reason}",
            "markdown": "",
            "source": "politeness",
            "url": url,
            "politeness": metadata,
        }

    if result.action == "delay" and result.delay_seconds > 0:
        logger.info(
            "Politeness delaying %s: sleeping %.1fs (domain=%s)",
            url,
            result.delay_seconds,
            result.domain,
        )
        await asyncio.sleep(result.delay_seconds)

    return True, None


async def _enrich_with_politeness(result: dict, url: str) -> dict:
    """Add politeness metadata to a scrape result if politeness is enabled.

    Also records the request for rate-limiting purposes and enriches
    with structured metadata (JSON-LD, OG, meta tags) when raw HTML
    is available in the result.
    """
    from .politeness import get_manager

    manager = get_manager()
    if manager.enabled:
        manager.record_request(url)
        result["politeness"] = manager.get_politeness_metadata(url)

    # Structured metadata enrichment — runs when raw_html_start exists
    _enrich_with_metadata(result)

    return result


async def _politeness_check_for_tier(url: str, tier_label: str) -> dict | None:
    """Check politeness before a tier. Returns None to proceed, error dict to return."""
    _proceed, blocked = await _politeness_check_and_delay(url)
    if blocked:
        logger.info("Politeness blocked %s at %s", url, tier_label)
        return blocked
    return None


async def smart_scrape(url: str) -> dict:
    """Try each tier in order. Return the first successful result with acceptable quality.

    Degrades through tiers when quality is below QA_MIN_QUALITY_THRESHOLD.
    Returns the best-effort result if all tiers produce low quality.

    When SCRAPER_POLITENESS_ENABLED=true, checks robots.txt and enforces
    per-domain rate limits before each tier.

    Returns a dict with keys: markdown, source, url, quality, error (optional).
    """
    best_effort: list[dict] = []

    # Log proxy status for debugging (per-scrape proxy identity logging)
    proxy_url = SCRAPER_PROXY_URL
    if proxy_url:
        logger.info("Proxy configured: %s", _redact_proxy_url(proxy_url))
    else:
        logger.info("No proxy configured")

    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=30,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
        },
        proxy=_get_httpx_proxies(),
    ) as client:
        # Adapter registry check (pre-pipeline, before any HTTP)
        registry = get_registry()
        if registry._entries:
            ctx = AdapterContext(
                browser_svc_url=_settings.browser_svc_url,
                config=dict(os.environ),
            )
            adapter_result = await registry.dispatch(url, ctx)
            if adapter_result:
                logger.info("Adapter hit: %s for %s", adapter_result.source, url)
                return adapter_result.to_dict()

        # Politeness check: robots.txt + rate limit (before any HTTP)
        _proceed, blocked = await _politeness_check_and_delay(url)
        if blocked:
            return blocked

        # Cache check (after adapter, before tier pipeline)
        cached = await _check_cache(url)
        if cached:
            cached = _add_quality(cached)
            _enrich_with_metadata(cached)
            if _quality_acceptable(cached):
                return cached
            logger.info("Cache hit below quality threshold, re-fetching %s", url)

        # Tier 1: /llms.txt
        _proceed, blocked = await _politeness_check_and_delay(url)
        if blocked:
            return blocked
        result = await fetch_via_llms_txt(url, client)
        if result:
            accepted = await _maybe_degrade(result, "tier1-llms-txt", best_effort)
            if accepted:
                accepted = await _enrich_with_politeness(accepted, url)
                await _set_cache(url, accepted, prior_entry=cached)
                return accepted

        # Tier 2: Accept: text/markdown
        _proceed, blocked = await _politeness_check_and_delay(url)
        if blocked:
            return blocked
        result = await fetch_via_content_negotiation(url, client)
        if result:
            accepted = await _maybe_degrade(
                result, "tier2-content-negotiation", best_effort
            )
            if accepted:
                accepted = await _enrich_with_politeness(accepted, url)
                await _set_cache(url, accepted, prior_entry=cached)
                return accepted

    # Tier 3: Playwright render + readability (no shared client needed)
    _proceed, blocked = await _politeness_check_and_delay(url)
    if blocked:
        return blocked
    result = await fetch_via_playwright(url)
    if result:
        # Barrier detection — if page IS a challenge/error, skip remaining tiers
        if "barrier" in result:
            logger.warning(
                "Barrier detected at Tier 3 for %s, skipping remaining tiers", url
            )
            return result

        markdown_text = result.get("markdown", "")
        raw_html = result.get("raw_html_start", "")
        barrier = _classify_barrier("", url, markdown_text, raw_html)
        content_good = not barrier.detected or barrier.confidence <= 0.7
        content_embedded = _has_embedded_content(raw_html)

        if content_good:
            accepted = await _maybe_degrade(result, "tier3-playwright", best_effort)
            if accepted:
                accepted = await _enrich_with_politeness(accepted, url)
                await _set_cache(url, accepted, prior_entry=cached)
                return accepted
            # Low quality — degrade through remaining tiers
            logger.info("Tier 3 content quality below threshold, degrading for %s", url)
        else:
            logger.info(
                "Tier 3 content flagged: barrier=%s (conf=%.2f), embedded=%s",
                barrier.barrier_type or "none",
                barrier.confidence,
                content_embedded,
            )

    # Tier 3.5: FlareSolverr for hard Cloudflare challenges
    if result:
        _proceed, blocked = await _politeness_check_and_delay(url)
        if blocked:
            return blocked
        fs_result = await fetch_via_flaresolverr(url)
        if fs_result:
            if "barrier" in fs_result:
                logger.warning(
                    "Barrier detected at Tier 3.5 for %s, skipping remaining tiers", url
                )
                return fs_result
            accepted = await _maybe_degrade(
                fs_result, "tier35-flaresolverr", best_effort
            )
            if accepted:
                accepted = await _enrich_with_politeness(accepted, url)
                await _set_cache(url, accepted, prior_entry=cached)
                return accepted

    # Tier 4: LLM-assisted recovery when content looks suspicious
    if result:
        logger.info("Tier 4: attempting LLM recovery for %s", url)
        from .recovery import attempt_llm_recovery

        page_content = result.get("raw_html_start") or result.get("markdown", "")
        recovery_result = await attempt_llm_recovery(url, page_content)
        if recovery_result:
            accepted = await _maybe_degrade(
                recovery_result, "tier4-llm-recovery", best_effort
            )
            if accepted:
                accepted = await _enrich_with_politeness(accepted, url)
                await _set_cache(url, accepted, prior_entry=cached)
                return accepted

    # Browser-svc fallback for Substack (last resort before error)
    if result:
        raw_html = result.get("raw_html_start", "")
        redirected_url = ""
        import re as _re

        substack_match = _re.search(r'substack\.com/[^"\'\\s]+', raw_html)
        if substack_match:
            redirected_url = f" (redirected to {substack_match.group()})"

        if _is_substack_redirect(raw_html):
            logger.info(
                "Substack redirect detected, trying browser-svc fallback for %s", url
            )
            browser_result = await _fetch_via_browser_svc(url)
            if browser_result:
                accepted = await _maybe_degrade(
                    browser_result, "browser-svc", best_effort
                )
                if accepted:
                    accepted = await _enrich_with_politeness(accepted, url)
                    await _set_cache(url, accepted, prior_entry=cached)
                    return accepted
                return await _enrich_with_politeness(
                    {
                        "error": (
                            f"Could not extract content from {url}{redirected_url}. "
                            f"Substack blocked the headless browser. "
                            f"Try: groktocrawl browser exec <id> navigate --url <url> "
                            f"then browser exec <id> executeScript "
                            f"--script \"document.querySelector('article').innerText\""
                        ),
                        "markdown": "",
                        "source": "none",
                        "url": url,
                    },
                    url,
                )

    # All tiers exhausted — return best effort if any tier produced content
    if best_effort:
        best = max(best_effort, key=lambda r: r.get("quality", {}).get("score", 0.0))
        bq = best.get("quality", {})
        bs = bq.get("score", 0.0)
        logger.warning(
            "All tiers exhausted for %s, returning best effort (quality=%.2f, source=%s)",
            url,
            bs,
            best.get("source", "unknown"),
        )
        best["warning"] = (
            f"Suboptimal content — quality ({bs:.2f}) below threshold ({QA_MIN_QUALITY_THRESHOLD:.2f})"
        )
        await _set_cache(url, best, prior_entry=cached)
        return await _enrich_with_politeness(best, url)

    return await _enrich_with_politeness(
        {
            "error": f"Could not extract content from {url}",
            "markdown": "",
            "source": "none",
            "url": url,
        },
        url,
    )
