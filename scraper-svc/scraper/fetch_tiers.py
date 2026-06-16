"""Tier fetch implementations for the scraper service.

Contains the three-tier fetch strategy functions plus anti-bot fallbacks:

- Tier 1: ``fetch_via_llms_txt()`` — GET /llms.txt at site root
- Tier 2: ``fetch_via_content_negotiation()`` — Accept: text/markdown header
- Tier 3: ``fetch_via_playwright()`` — stealth Playwright render + readability
- Tier 3.5: ``fetch_via_flaresolverr()`` — FlareSolverr anti-bot bypass
- Fallback: ``_fetch_via_browser_svc()`` — browser-svc API for Substack redirects

Also includes internal helpers for Playwright proxy management and
browser service interaction.
"""

import logging

import httpx

from common.url import extract_domain

from .barrier import (
    _classify_barrier,
    _is_bot_challenge,
    _is_substack_redirect,
    _looks_like_markdown,
)
from .cache import _is_binary_content_type, _make_download_payload
from .fetch_quality import html_to_markdown
from .proxy import _get_playwright_proxy
from .settings import load_settings

logger = logging.getLogger(__name__)

_settings = load_settings()
FLARE_SOLVERR_URL = _settings.flare_solverr_url


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
