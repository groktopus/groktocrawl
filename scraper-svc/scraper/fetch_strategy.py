"""Three-tier fetch strategy for turning URLs into clean markdown.

Tier 1: /llms.txt — entire site as markdown, one GET.
Tier 2: Accept: text/markdown — per-page markdown via content negotiation.
Tier 3: Playwright render + readability extraction (heavyweight).
"""

import asyncio
import logging
import os
import re

import httpx

from common.url import extract_domain

from .adapters.base import AdapterContext, get_registry
from .barrier import (
    _classify_barrier,
    _has_embedded_content,
    _is_bot_challenge,
    _is_substack_redirect,
    _looks_like_markdown,
)
from .cache import (
    _check_cache,
    _is_binary_content_type,
    _make_download_payload,
    _set_cache,
)
from common.url import is_private_host
from .extract import assess_quality
from .metadata import extract_all_metadata
from .proxy import (
    SCRAPER_PROXY_URL,
    _get_httpx_proxies,
    _get_playwright_proxy,
    _redact_proxy_url,
)
from .settings import load_settings

logger = logging.getLogger(__name__)

_settings = load_settings()
FLARE_SOLVERR_URL = _settings.flare_solverr_url
QA_MIN_QUALITY_THRESHOLD = _settings.qa_min_quality_threshold


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
            if is_private_host(url):
                logger.warning("Blocked navigation to private URL %s", url)
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
            except Exception as e:
                logger.debug("Session cleanup failed for %s: %s", url, e)

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
    except Exception as e:
        logger.debug("Browser page content fetch failed for session %s: %s", session_id, e)
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

        substack_match = re.search(r'substack\.com/[^"\'\\s]+', raw_html)
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
