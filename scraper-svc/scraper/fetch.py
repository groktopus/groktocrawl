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
import re
from urllib.parse import urlparse

import httpx
from curl_cffi import requests as curl_requests

from .adapters.base import AdapterContext, get_registry
from .barrier_recovery import (
    attach_recovery,
    browser_service_applicable,
    flaresolverr_applicable,
    new_recovery_receipt,
    record_attempt,
)
from .cache import _check_cache, _is_binary_content_type, _set_cache
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
    _is_private_url,
    fetch_via_content_negotiation,
    fetch_via_flaresolverr,
    fetch_via_llms_txt,
    fetch_via_playwright,
)
from .proxy import _get_httpx_proxies, _redact_proxy_url
from .settings import load_settings
from .source_http import (
    SourceDestinationPolicyUnavailableError,
    capture_egress_proxy_url,
    source_curl_options,
)

logger = logging.getLogger(__name__)

_settings = load_settings()
FLARE_SOLVERR_URL = _settings.flare_solverr_url
_CVE_IDENTIFIER = re.compile(r"cve:CVE-\d{4}-\d{4,}\Z", re.IGNORECASE)

# ── Proxy configuration ─────────────────────────────────────────
# SCRAPER_PROXY_URL preserves the compatibility-mode curl and browser proxy.
# SCRAPER_CAPTURE_EGRESS_PROXY_URL instead forces source HTTPX and curl traffic
# through the guarded gateway; source_http.py disables environment proxy lookup
# and refuses nested browser/FlareSolverr source fetches in that mode.
SCRAPER_PROXY_URL = _settings.scraper_proxy_url


def _private_url_allowlisted(url: str) -> bool:
    """Return whether an exact hostname is explicitly trusted by the operator."""
    hostname = (urlparse(url).hostname or "").lower()
    allowed = {
        host.strip().lower()
        for host in _settings.scraper_private_url_allowlist.split(",")
        if host.strip()
    }
    return hostname in allowed


async def _private_destination_error(
    url: str, *, allow_adapter_identifier: bool = False
) -> dict | None:
    """Return the standard denial envelope for an unapproved private target.

    CVE identifiers are adapter inputs rather than network destinations. Their
    adapter patterns are intentionally allowed through; every URL-like target
    is checked before adapter dispatch or any fetch tier.
    """
    if allow_adapter_identifier and _CVE_IDENTIFIER.fullmatch(url):
        return None
    if _private_url_allowlisted(url):
        return None
    try:
        is_private, reason = await _is_private_url(url)
    except SourceDestinationPolicyUnavailableError:
        return {
            "markdown": "",
            "source": "blocked",
            "url": url,
            "error": "Destination validation is temporarily unavailable",
            "error_code": "SOURCE_DESTINATION_VALIDATION_UNAVAILABLE",
        }
    if not is_private:
        return None
    return {
        "markdown": "",
        "source": "blocked",
        "url": url,
        "error": reason,
        "error_code": "PRIVATE_URL_BLOCKED",
    }


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


async def _politeness_check_and_delay(
    url: str,
    ignore_robots_txt: bool = False,
    robots_user_agent: str | None = None,
    rate_limit: bool = True,
) -> tuple[bool, dict | None]:
    """Check politeness policy for a URL.

    Args:
        url: The URL to check.
        ignore_robots_txt: If True, skip robots.txt enforcement but still
            apply rate limiting.
        robots_user_agent: Custom User-Agent string to use for robots.txt
            evaluation. If None, uses the default bot UA.
        rate_limit: If False, only robots.txt enforcement runs (read-only for
            rate limiting). Per-tier checks pass this to avoid reserving a new
            slot and sleeping before every tier of a single scrape.

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

    result = await manager.check(
        url,
        ignore_robots_txt=ignore_robots_txt,
        robots_user_agent=robots_user_agent,
        rate_limit=rate_limit,
    )
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
        try:
            await asyncio.sleep(result.delay_seconds)
        except asyncio.CancelledError:
            # Roll back the in-memory future-slot reservation so an aborted
            # delayed request does not inflate the next request's delay.
            manager.rollback_reservation(url)
            raise

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
    """Check politeness before a tier. Returns None to proceed, error dict to return.

    Rate limiting is read-only here: the top-level politeness gate already
    reserved a slot and slept, so per-tier checks must not reserve another
    slot (which would compound per-scrape latency).
    """
    _proceed, blocked = await _politeness_check_and_delay(url, rate_limit=False)
    if blocked:
        logger.info("Politeness blocked %s at %s", url, tier_label)
        return blocked
    return None


async def _recover_from_barrier(
    url: str,
    initial_result: dict | None,
    best_effort: list[dict],
    *,
    prior_cache_entry: dict | None = None,
    cache_success: bool = False,
    initial_strategy: str = "playwright",
    allow_flaresolverr: bool = True,
) -> dict:
    """Exhaust applicable configured challenge countermeasures once.

    Playwright (including its in-page CAPTCHA resolver) has already run when
    this helper is called.  The remaining deterministic ladder is
    FlareSolverr for Cloudflare/Turnstile, followed by a fresh browser-svc
    session for interactive barriers.  Challenge content never enters the LLM
    recovery tier.
    """
    receipt = new_recovery_receipt(initial_result, initial_strategy=initial_strategy)
    terminal_result = initial_result

    if allow_flaresolverr and flaresolverr_applicable(receipt):
        blocked = await _politeness_check_for_tier(url, "barrier-flaresolverr")
        if blocked:
            return attach_recovery(blocked, receipt, recovered=False)
        flare_result = await fetch_via_flaresolverr(url)
        if flare_result is None:
            record_attempt(receipt, "flaresolverr", "unavailable")
        elif flare_result.get("barrier"):
            if terminal_result is None or not terminal_result.get("error_code"):
                terminal_result = flare_result
            record_attempt(receipt, "flaresolverr", "barrier")
        else:
            accepted = await _maybe_degrade(
                flare_result, "barrier-flaresolverr", best_effort
            )
            if accepted:
                record_attempt(receipt, "flaresolverr", "success")
                accepted = attach_recovery(accepted, receipt, recovered=True)
                accepted = await _enrich_with_politeness(accepted, url)
                if cache_success:
                    await _set_cache(url, accepted, prior_entry=prior_cache_entry)
                return accepted
            record_attempt(receipt, "flaresolverr", "low_quality")
    elif allow_flaresolverr:
        record_attempt(
            receipt,
            "flaresolverr",
            "skipped",
            "not_applicable_to_classified_barrier",
        )

    if browser_service_applicable(receipt):
        blocked = await _politeness_check_for_tier(url, "barrier-browser-svc")
        if blocked:
            return attach_recovery(blocked, receipt, recovered=False)
        browser_result = await _fetch_via_browser_svc(url)
        if browser_result is None:
            record_attempt(receipt, "browser-svc", "unavailable")
        elif browser_result.get("barrier"):
            if terminal_result is None or not terminal_result.get("error_code"):
                terminal_result = browser_result
            record_attempt(receipt, "browser-svc", "barrier")
        else:
            accepted = await _maybe_degrade(
                browser_result, "barrier-browser-svc", best_effort
            )
            if accepted:
                record_attempt(receipt, "browser-svc", "success")
                accepted = attach_recovery(accepted, receipt, recovered=True)
                accepted = await _enrich_with_politeness(accepted, url)
                if cache_success:
                    await _set_cache(url, accepted, prior_entry=prior_cache_entry)
                return accepted
            record_attempt(receipt, "browser-svc", "low_quality")
    else:
        record_attempt(
            receipt,
            "browser-svc",
            "skipped",
            "not_applicable_to_classified_barrier",
        )

    # Build a fresh terminal envelope. Never return the original challenge
    # markdown or HTML merely to preserve its classification.
    terminal_source = (
        (terminal_result or {}).get("source", "none")
        if not (terminal_result or {}).get("raw_html_start")
        else "none"
    )
    terminal_result = {
        "error": (terminal_result or {}).get("error")
        or f"Could not extract content from {url}",
        "error_code": (terminal_result or {}).get("error_code") or "BARRIER_DETECTED",
        "markdown": "",
        "source": terminal_source,
        "url": url,
        **(
            {"barrier": terminal_result["barrier"]}
            if terminal_result and terminal_result.get("barrier")
            else {}
        ),
    }

    terminal_result = attach_recovery(terminal_result, receipt, recovered=False)
    return await _enrich_with_politeness(terminal_result, url)


async def _head_probe(url: str, client: httpx.AsyncClient) -> dict:
    """Send a lightweight HEAD request to detect routing signals.

    Checks response headers for bot protection, redirects, binary content,
    and empty responses. The probe is an optimization — if it fails, the
    pipeline falls through to the normal tier flow.

    Returns a dict with routing hints:
        shielded: True if bot protection or error status detected
        redirect_url: Final URL after redirects
        is_binary: True if content-type indicates binary download
        is_empty: True if content-length < 1KB with 200 status
        status_code: HTTP status code
        content_type: Content-Type header value
    """
    try:
        resp = await client.head(url, allow_redirects=True, timeout=10)  # type: ignore[call-arg]
        status_code = resp.status_code
        content_type = resp.headers.get("content-type", "")
        content_length = resp.headers.get("content-length")
        final_url = str(resp.url)

        shielded = False
        if resp.headers.get("cf-mitigated", "").lower() == "challenge":
            shielded = True
        if status_code >= 400:
            shielded = True

        is_binary = _is_binary_content_type(content_type)

        is_empty = False
        if content_length is not None and status_code == 200:
            try:
                cl = int(content_length)
                if cl < 1024:
                    is_empty = True
            except (ValueError, TypeError):
                pass

        result = {
            "shielded": shielded,
            "redirect_url": final_url,
            "is_binary": is_binary,
            "is_empty": is_empty,
            "status_code": status_code,
            "content_type": content_type,
        }
        logger.info(
            "HEAD probe: %s -> shielded=%s, redirect=%s, binary=%s, status=%d",
            url,
            shielded,
            final_url != url,
            is_binary,
            status_code,
        )
        return result
    except Exception as e:
        logger.warning("HEAD probe failed for %s: %s", url, e)
        return {
            "shielded": False,
            "redirect_url": url,
            "is_binary": False,
            "is_empty": False,
            "status_code": 0,
            "content_type": "",
        }


async def smart_scrape(
    url: str,
    force_browser: bool = False,
    lightweight_only: bool = False,
    ignore_robots_txt: bool = False,
    robots_user_agent: str | None = None,
    scrape_options: dict | None = None,
) -> dict:
    """Try each tier in order. Return the first successful result with acceptable quality.

    Degrades through tiers when quality is below QA_MIN_QUALITY_THRESHOLD.
    Returns the best-effort result if all tiers produce low quality.

    When ``force_browser`` is True, skips the HEAD probe and Tiers 1-2,
    going straight to Tier 3 (Playwright render). Used for Cloudflare-
    protected pages where the lightweight tiers would fail or timeout.

    When ``lightweight_only`` is True, the pipeline runs only the lightweight
    tiers (adapter, cache, Tier 1 llms.txt, Tier 2 content negotiation) and
    short-circuits before Tier 3 (Playwright). Callers use this for the
    generic fallback stage so it can never silently enter the browser tier
    before a separate forced-browser retry.

    When SCRAPER_POLITENESS_ENABLED=true, checks robots.txt and enforces
    per-domain rate limits before each tier.

    When ``ignore_robots_txt`` is True, robots.txt disallow directives are
    skipped but per-domain rate limiting (crawl-delay) still applies.

    When ``robots_user_agent`` is set, it is used as the User-Agent for
    robots.txt evaluation instead of the default bot UA.

    Args:
        url: The URL to scrape.
        force_browser: If True, skip lightweight tiers.
        lightweight_only: If True, stop before the browser tier.
        ignore_robots_txt: If True, skip robots.txt enforcement.
        robots_user_agent: Custom UA for robots.txt evaluation.

    Returns a dict with keys: markdown, source, url, quality, error (optional).
    """
    # Validate source destinations before adapters, which may perform their
    # own HTTP requests and otherwise bypass the generic fetch guard. Opaque
    # adapter identifiers (for example cve:CVE-...) are not network targets.
    destination_error = await _private_destination_error(
        url, allow_adapter_identifier=not force_browser
    )
    if destination_error:
        logger.warning("Blocked private or internal scrape destination")
        return destination_error

    if not force_browser:
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
        if _CVE_IDENTIFIER.fullmatch(url):
            # Preserve the prior terminal behavior when no CVE adapter accepts
            # an identifier; it must never fall through to an HTTP fetch tier.
            return await _private_destination_error(url) or {
                "markdown": "",
                "source": "blocked",
                "url": url,
                "error": "Unsupported non-network scrape identifier",
                "error_code": "PRIVATE_URL_BLOCKED",
            }

    best_effort: list[dict] = []

    # ── force_browser fast path ─────────────────────────────────
    # Skip the adapter, cache, HEAD probe, and lightweight tiers.
    # Jump directly to Tier 3 (Playwright) for Cloudflare-protected
    # or JS-heavy pages.
    tier3_barrier: dict | None = None
    if force_browser:
        logger.info("force_browser=True, jumping to Tier 3 for %s", url)
        # Politeness check still applies
        _proceed, blocked = await _politeness_check_and_delay(
            url,
            ignore_robots_txt=ignore_robots_txt,
            robots_user_agent=robots_user_agent,
        )
        if blocked:
            return blocked

        result = await fetch_via_playwright(url)
        if result:
            # Barrier detection
            if "barrier" in result:
                logger.warning("Barrier detected at force_browser Tier 3 for %s", url)
                # Keep the envelope for the terminal error dict (#586): when
                # every later tier also yields nothing, the barrier provenance
                # must not be silently dropped.
                tier3_barrier = result
            markdown_text = result.get("markdown", "")
            raw_html = result.get("raw_html_start", "")
            barrier = _classify_barrier("", url, markdown_text, raw_html)
            if not barrier.detected or barrier.confidence <= 0.7:
                accepted = await _maybe_degrade(result, "tier3-playwright", best_effort)
                if accepted:
                    accepted = await _enrich_with_politeness(accepted, url)
                    return accepted
            else:
                logger.info(
                    "force_browser Tier 3 barrier for %s: %s (conf=%.2f)",
                    url,
                    barrier.barrier_type or "none",
                    barrier.confidence,
                )

        if result is None or result.get("barrier"):
            return await _recover_from_barrier(url, result, best_effort)

        # Return best effort or error
        if best_effort:
            best = max(
                best_effort, key=lambda r: r.get("quality", {}).get("score", 0.0)
            )
            bq = best.get("quality", {})
            bs = bq.get("score", 0.0)
            logger.warning(
                "force_browser all tiers exhausted for %s, returning best effort (quality=%.2f)",
                url,
                bs,
            )
            best["warning"] = (
                f"Suboptimal content — quality ({bs:.2f}) below threshold "
                f"({QA_MIN_QUALITY_THRESHOLD:.2f})"
            )
            return await _enrich_with_politeness(best, url)

        return await _enrich_with_politeness(
            {
                "error": f"Could not extract content from {url}",
                "markdown": "",
                "source": "none",
                "url": url,
                # Preserve the Tier 3 barrier provenance (#586): when the gate
                # refused the page here, the envelope was consumed internally —
                # without this the terminal dict is a bare "source: none"
                # failure and operators lose the barrier signal.
                **({"barrier": tier3_barrier["barrier"]} if tier3_barrier else {}),
            },
            url,
        )

    # Log proxy status for debugging (per-scrape proxy identity logging)
    proxy_url = capture_egress_proxy_url() or SCRAPER_PROXY_URL
    if proxy_url:
        logger.info("Proxy configured: %s", _redact_proxy_url(proxy_url))
    else:
        logger.info("No proxy configured")

    async with curl_requests.AsyncSession(
        impersonate="chrome131",
        timeout=30,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
        },
        **source_curl_options(compatibility_proxy=_get_httpx_proxies()),
    ) as client:
        # Politeness check: robots.txt + rate limit (before any HTTP)
        _proceed, blocked = await _politeness_check_and_delay(
            url,
            ignore_robots_txt=ignore_robots_txt,
            robots_user_agent=robots_user_agent,
        )
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

        # Phase 0: HEAD probe — detect bot protection, errors, or binary content
        probe = await _head_probe(url, client)
        if probe["redirect_url"] != url:
            logger.info("HEAD probe: %s redirected to %s", url, probe["redirect_url"])
            url = probe["redirect_url"]
            # Re-check cache with redirected URL
            cached = await _check_cache(url)
            if cached:
                cached = _add_quality(cached)
                _enrich_with_metadata(cached)
                if _quality_acceptable(cached):
                    return cached

        # Tier 1: /llms.txt — only for site root URLs
        is_root = urlparse(url).path in ("", "/")
        if is_root and not probe.get("shielded") and not probe.get("is_binary"):
            _proceed, blocked = await _politeness_check_and_delay(
                url,
                ignore_robots_txt=ignore_robots_txt,
                robots_user_agent=robots_user_agent,
                rate_limit=False,
            )
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
        if not probe.get("shielded") and not probe.get("is_binary"):
            _proceed, blocked = await _politeness_check_and_delay(
                url,
                ignore_robots_txt=ignore_robots_txt,
                robots_user_agent=robots_user_agent,
                rate_limit=False,
            )
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

    # ── Lightweight-only short-circuit ──────────────────────────
    # The generic fallback stage must not silently enter the browser tier.
    # Return the best effort produced by the lightweight tiers (or a failure)
    # and let the caller decide whether to force a separate browser retry.
    if lightweight_only:
        if best_effort:
            best = max(
                best_effort, key=lambda r: r.get("quality", {}).get("score", 0.0)
            )
            bq = best.get("quality", {})
            bs = bq.get("score", 0.0)
            logger.warning(
                "lightweight_only for %s, returning best effort (quality=%.2f)",
                url,
                bs,
            )
            best["warning"] = (
                f"Lightweight-only content — quality ({bs:.2f}) below threshold "
                f"({QA_MIN_QUALITY_THRESHOLD:.2f})"
            )
            return await _enrich_with_politeness(best, url)
        return await _enrich_with_politeness(
            {
                "error": f"Could not extract content from {url} using lightweight tiers",
                "markdown": "",
                "source": "none",
                "url": url,
            },
            url,
        )

    # Tier 3: Playwright render + readability (no shared client needed)
    _proceed, blocked = await _politeness_check_and_delay(
        url,
        ignore_robots_txt=ignore_robots_txt,
        robots_user_agent=robots_user_agent,
        rate_limit=False,
    )
    if blocked:
        return blocked
    result = await fetch_via_playwright(url)
    if result:
        # Barrier detection — if page IS a challenge/error, skip remaining tiers
        if "barrier" in result:
            logger.warning(
                "Barrier detected at Tier 3 for %s, falling through to FlareSolverr",
                url,
            )
            # Keep the envelope for the terminal error dict (#586): when every
            # later tier also yields nothing, the barrier provenance must not
            # be silently dropped (same as the force_browser fast path).
            tier3_barrier = result

        markdown_text = result.get("markdown", "")
        raw_html = result.get("raw_html_start", "")
        barrier = _classify_barrier("", url, markdown_text, raw_html)
        content_good = not barrier.detected or barrier.confidence <= 0.7
        content_embedded = _has_embedded_content(raw_html)

        if content_good or barrier.barrier_type == "empty":
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

    if result is None or result.get("barrier"):
        return await _recover_from_barrier(
            url,
            result,
            best_effort,
            prior_cache_entry=cached,
            cache_success=True,
        )

    # Preserve the historical quality-recovery path for non-barrier browser
    # output. Barrier recovery is centralized above and cannot reach the LLM.
    _proceed, blocked = await _politeness_check_and_delay(
        url,
        ignore_robots_txt=ignore_robots_txt,
        robots_user_agent=robots_user_agent,
        rate_limit=False,
    )
    if blocked:
        return blocked
    fs_result = await fetch_via_flaresolverr(url)
    if fs_result:
        if "barrier" in fs_result:
            return await _recover_from_barrier(
                url,
                fs_result,
                best_effort,
                prior_cache_entry=cached,
                cache_success=True,
                initial_strategy="flaresolverr",
                allow_flaresolverr=False,
            )
        accepted = await _maybe_degrade(fs_result, "tier35-flaresolverr", best_effort)
        if accepted:
            accepted = await _enrich_with_politeness(accepted, url)
            await _set_cache(url, accepted, prior_entry=cached)
            return accepted

    # Tier 4: LLM-assisted recovery when content looks suspicious
    if (
        result
        and result.get("error_code") != "CAPTCHA_UNRESOLVED"
        and ("barrier" in result or result.get("markdown"))
    ):
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
    if result and ("barrier" in result or result.get("raw_html_start")):
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
            # Preserve the Tier 3 barrier provenance (#586): when the gate
            # refused the page here, the envelope was consumed internally —
            # without this the terminal dict is a bare "source: none"
            # failure and operators lose the barrier signal. Non-barrier
            # exhaustion stays bare (mirrors the force_browser fast path).
            **({"barrier": tier3_barrier["barrier"]} if tier3_barrier else {}),
        },
        url,
    )
