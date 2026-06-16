"""Multi-tier scrape orchestrator.

Coordinates the three-tier fetch strategy (llms.txt, content negotiation,
Playwright render) with cache revalidation, politeness enforcement, and
quality-based degradation.

The tier implementations live in ``fetch_tiers.py`` and quality assessment
lives in ``fetch_quality.py``. Cache functions live in ``cache.py``.
"""

import asyncio
import logging
import os

import httpx

from .adapters.base import AdapterContext, get_registry
from .cache import _check_cache, _set_cache
from .fetch_quality import (
    QA_MIN_QUALITY_THRESHOLD,
    _add_quality,
    _classify_barrier,
    _enrich_with_metadata,
    _has_embedded_content,
    _is_substack_redirect,
    _quality_acceptable,
)
from .fetch_tiers import (
    _fetch_via_browser_svc,
    fetch_via_content_negotiation,
    fetch_via_flaresolverr,
    fetch_via_llms_txt,
    fetch_via_playwright,
)
from .proxy import _get_httpx_proxies, _redact_proxy_url
from .settings import load_settings

logger = logging.getLogger(__name__)

_settings = load_settings()
FLARE_SOLVERR_URL = _settings.flare_solverr_url

# ── Proxy configuration ─────────────────────────────────────────
# SCRAPER_PROXY_URL is an opt-in env var for residential/mobile IP rotation.
# When set, httpx requests (Tiers 1-2) and Playwright browser contexts (Tier 3)
# route through the proxy. Playwright uses context-level proxy assignment
# (browser.new_context(proxy=...)) for job isolation.
# If the proxy is unreachable, the scrape retries without proxy and logs a WARN.
# Format: **************************
# Unset or empty = no proxy (default).
SCRAPER_PROXY_URL = _settings.scraper_proxy_url


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
