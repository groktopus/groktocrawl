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

import asyncio
import hashlib
import logging
import time
from contextlib import AsyncExitStack
from urllib.parse import urlparse

import httpx

from common.admission import AdmissionController
from common.metrics import METRICS
from common.stage_metrics import inc_counter, observe_elapsed
from common.url import extract_domain, is_private_host, is_private_host_literal

from .barrier import (
    _classify_barrier,
    _is_bot_challenge,
    _is_substack_redirect,
    _looks_like_markdown,
)
from .browser_recovery import schedule_container_recycle
from .cache import _is_binary_content_type, _make_download_payload
from .fetch_quality import html_to_markdown
from .playwright_retry import retry_transient
from .process_health import (
    MIN_TASK_HEADROOM,
    launch_with_process_headroom,
    process_capacity,
)
from .proxy import _get_playwright_proxy
from .settings import load_settings
from .source_http import (
    SourceDestinationPolicyUnavailableError,
    capture_gateway_hostname_is_public,
    protected_source_mode,
    trusted_control_base_url,
    trusted_control_httpx_client,
)

logger = logging.getLogger(__name__)

_settings = load_settings()
FLARE_SOLVERR_URL = _settings.flare_solverr_url
_browser_semaphore = asyncio.Semaphore(_settings.max_browser_concurrency)

# Outer weighted admission for the browser class (single-class controller);
# the semaphore above remains the inner per-request cap.
_browser_admission = AdmissionController(
    limits={"browser": _settings.max_browser_concurrency},
    weights={"browser": 1},
)
_BROWSER_LIFECYCLE_TIMEOUT_SECONDS = 120.0
_BROWSER_CLEANUP_GRACE_SECONDS = 5.0
_browser_quarantined = False
_browser_quarantine_reason: str | None = None


def _http_status_error(url: str, status: int, source: str) -> dict:
    """Build a terminal scrape result for an unsuccessful document response."""
    return {
        "error": f"Source navigation returned HTTP {status} for {url}",
        "error_code": "HTTP_STATUS_ERROR",
        "http_status": status,
        "markdown": "",
        "source": source,
        "url": url,
    }


class BrowserUnavailableError(RuntimeError):
    """The embedded browser tier cannot safely accept more work."""


class BrowserCleanupIncompleteError(RuntimeError):
    """A lifecycle completed but could not prove its native browser closed."""


def browser_readiness(
    capacity: dict[str, object] | None = None,
    *,
    additional_launches: int = 1,
) -> dict[str, object]:
    """Return browser-tier readiness while leaving lightweight scraper paths live."""
    capacity = capacity if capacity is not None else process_capacity()
    if _browser_quarantined:
        reason = _browser_quarantine_reason or "cleanup_incomplete"
        state = "degraded"
    elif capacity["state"] == "degraded":
        reason = "process_capacity_low"
        state = "degraded"
    elif capacity["state"] == "unknown":
        reason = "process_budget_unavailable"
        state = "unknown"
    else:
        reason = None
        state = "ok"
    available_tasks = capacity.get("available_tasks")
    required_tasks = MIN_TASK_HEADROOM * additional_launches
    if (
        state == "ok"
        and isinstance(available_tasks, int)
        and available_tasks < required_tasks
    ):
        reason = "process_capacity_low"
        state = "degraded"
    return {
        "status": state,
        "capacity": capacity,
        "admission": "unavailable" if state == "degraded" else "available",
        "reason": reason,
        "active": _browser_admission.active("browser"),
        "queued": _browser_admission.queue_depth("browser"),
        "limit": _browser_admission.budget_for("browser"),
        "required_task_headroom": required_tasks,
    }


def _quarantine_browser(reason: str) -> None:
    global _browser_quarantined, _browser_quarantine_reason
    if _browser_quarantined:
        return
    _browser_quarantined = True
    _browser_quarantine_reason = reason
    inc_counter(
        _BROWSER_CLEANUP_TOTAL,
        "Browser cleanup outcomes",
        {"outcome": "quarantined"},
    )
    logger.error(
        "Embedded browser cleanup could not be confirmed; browser admission quarantined; reason=%s",
        reason,
    )
    try:
        schedule_container_recycle()
    except RuntimeError:
        logger.exception("Scraper container recycle guard rejected configured recovery")


# ── Browser lifecycle capacity/latency metric names ──────────────
_BROWSER_ACTIVE = "groktocrawl_browser_semaphore_active"
_BROWSER_WAITERS = "groktocrawl_browser_semaphore_waiters"
_BROWSER_WAIT_SECONDS = "groktocrawl_browser_semaphore_wait_seconds"
_BROWSER_SETUP_SECONDS = "groktocrawl_browser_setup_seconds"
_BROWSER_NAVIGATION_SECONDS = "groktocrawl_browser_navigation_seconds"
_BROWSER_EXTRACTION_SECONDS = "groktocrawl_browser_extraction_seconds"
_BROWSER_CLEANUP_TOTAL = "groktocrawl_browser_cleanup_total"


def _browser_active_gauge():
    return METRICS.gauge(_BROWSER_ACTIVE, "Currently running Playwright lifecycles")


def _browser_waiters_gauge():
    return METRICS.gauge(
        _BROWSER_WAITERS, "Playwright lifecycles waiting for a semaphore slot"
    )


def _observe_extraction(started: float) -> None:
    observe_elapsed(
        _BROWSER_EXTRACTION_SECONDS,
        "Browser content extraction and markdown conversion latency",
        {},
        started,
    )


async def _is_private_url(url: str) -> tuple[bool, str]:
    """Check if a URL targets a private/internal IP or hostname.

    Returns (is_private, reason) tuple. Shared logic with browser-svc.

    Delegates to the shared ``common.url.is_private_host``
    for the actual check, then maps the boolean result back to the
    ``(bool, str)`` tuple format.
    """
    parsed = urlparse(url)
    hostname = parsed.hostname or ""
    literal_policy = is_private_host_literal(hostname)
    if literal_policy is not None:
        return (True, "Private or internal URL") if literal_policy else (False, "")

    if protected_source_mode():
        try:
            is_public = await capture_gateway_hostname_is_public(hostname)
        except SourceDestinationPolicyUnavailableError:
            # A missing/unavailable oracle never converts an unknown hostname
            # into an allowed destination.
            logger.warning("Capture gateway destination policy is unavailable")
            raise
        return (False, "") if is_public else (True, "Private or internal URL")

    return (True, "Private or internal URL") if is_private_host(url) else (False, "")


async def _playwright_fetch_with_proxy(
    url: str,
    proxy: dict | None,
) -> dict | None:
    """Run one bounded browser lifecycle, retaining capacity if cleanup is uncertain."""
    readiness = browser_readiness()
    if readiness["admission"] != "available":
        raise BrowserUnavailableError(str(readiness["reason"]))
    _browser_waiters_gauge().inc()
    wait_started = time.monotonic()
    admitted = False
    slotted = False
    retain_capacity = False
    operation: asyncio.Task[dict | None] | None = None
    try:
        await _browser_admission.acquire("browser")
        admitted = True
        await _browser_semaphore.acquire()
        slotted = True
        _browser_waiters_gauge().dec()
        observe_elapsed(
            _BROWSER_WAIT_SECONDS,
            "Time spent waiting for a browser semaphore slot",
            {},
            wait_started,
        )
        _browser_active_gauge().inc()
        # Recheck after queueing: a different lifecycle may have quarantined the
        # browser tier while this request waited for its permit.
        readiness = browser_readiness(additional_launches=0)
        if readiness["admission"] != "available":
            raise BrowserUnavailableError(str(readiness["reason"]))
        operation = asyncio.create_task(
            _playwright_fetch_unbounded(url, proxy), name="scraper-playwright-lifecycle"
        )
        done, _ = await asyncio.wait(
            {operation}, timeout=_BROWSER_LIFECYCLE_TIMEOUT_SECONDS
        )
        if done:
            try:
                return operation.result()
            except BrowserCleanupIncompleteError:
                retain_capacity = True
                raise

        operation.cancel()
        done, _ = await asyncio.wait(
            {operation}, timeout=_BROWSER_CLEANUP_GRACE_SECONDS
        )
        if not done:
            retain_capacity = True
            _quarantine_browser("lifecycle_timeout_cleanup_incomplete")
            raise BrowserUnavailableError("browser lifecycle cleanup incomplete")
        # Retrieve the cancellation to prevent an unobserved-task warning. The
        # bounded cleanup completed, so this permit can safely be released.
        try:
            operation.result()
        except asyncio.CancelledError:
            pass
        except BrowserCleanupIncompleteError:
            retain_capacity = True
            raise
        except BaseException:
            pass
        raise TimeoutError("Playwright lifecycle exceeded its deadline")
    except asyncio.CancelledError:
        if operation is not None and not operation.done():
            operation.cancel()
            # Keep the reservation unless cleanup is positively observed. If a
            # second cancellation interrupts this wait, fail closed.
            retain_capacity = True
            try:
                done, _ = await asyncio.wait(
                    {operation}, timeout=_BROWSER_CLEANUP_GRACE_SECONDS
                )
            except asyncio.CancelledError:
                _quarantine_browser("request_cancel_cleanup_interrupted")
                raise
            if operation not in done:
                _quarantine_browser("request_cancel_cleanup_incomplete")
            else:
                retain_capacity = False
                try:
                    operation.result()
                except BrowserCleanupIncompleteError:
                    retain_capacity = True
                except BaseException:
                    pass
        elif operation is not None and operation.done() and not operation.cancelled():
            try:
                operation.result()
            except BrowserCleanupIncompleteError:
                retain_capacity = True
            except BaseException:
                pass
        raise
    finally:
        if not slotted:
            _browser_waiters_gauge().dec()
        if slotted and not retain_capacity:
            _browser_active_gauge().dec()
            _browser_semaphore.release()
        if admitted and not retain_capacity:
            _browser_admission.release("browser")


async def _playwright_fetch_unbounded(
    url: str,
    proxy: dict | None,
) -> dict | None:
    """Inner playwright fetch, called with or without proxy.

    Returns the scrape result dict or None. Does NOT wrap in try/except
    for the outer browser lifecycle — callers handle that.
    """
    from playwright.async_api import async_playwright

    from .browser_pool import get_browser_pool
    from .cookie_store import inject_cookies, store_cookies
    from .stealth import create_stealth_browser, create_stealth_context

    proxy_label = proxy.get("server", "none") if proxy else "none"
    logger.info("Playwright proxy: %s", proxy_label)

    context_kwargs = {}
    if proxy:
        context_kwargs["proxy"] = proxy  # context-level, not launch-level

    pool = get_browser_pool()
    async with AsyncExitStack() as lifecycle:
        browser = None
        setup_started = time.monotonic()
        try:
            try:
                if pool.enabled:
                    lease = await pool.acquire(url, proxy)
                    browser = lease.entry.browser
                    cloakbrowser = lease.entry.cloakbrowser
                    context = lease.context

                    async def release_pool(_exc_type, _exc, _traceback):
                        await lease.release(healthy=_exc_type is None)
                        if pool.quarantined:
                            _quarantine_browser("browser_pool_cleanup_incomplete")
                            raise BrowserCleanupIncompleteError(
                                "browser pool cleanup incomplete"
                            )

                    lifecycle.push_async_exit(release_pool)
                else:
                    p = await lifecycle.enter_async_context(async_playwright())
                    browser, cloakbrowser = await launch_with_process_headroom(
                        lambda: create_stealth_browser(p, url)
                    )

                    async def close_legacy_browser():
                        try:
                            await browser.close()
                        except Exception:
                            _quarantine_browser("legacy_browser_close_failed")
                            inc_counter(
                                _BROWSER_CLEANUP_TOTAL,
                                "Browser cleanup outcomes",
                                {"outcome": "error"},
                            )
                            raise BrowserCleanupIncompleteError(
                                "legacy browser close failed"
                            ) from None
                        inc_counter(
                            _BROWSER_CLEANUP_TOTAL,
                            "Browser cleanup outcomes",
                            {"outcome": "success"},
                        )

                    lifecycle.push_async_callback(close_legacy_browser)
                    context = await create_stealth_context(
                        browser, cloakbrowser=cloakbrowser, **context_kwargs
                    )
                page = await context.new_page()
                # Inject cached Cloudflare clearance cookies before navigation
                await inject_cookies(url, context)
            except Exception:
                observe_elapsed(
                    _BROWSER_SETUP_SECONDS,
                    "Browser launch, context, page, and cookie-injection latency",
                    {},
                    setup_started,
                )
                raise
            observe_elapsed(
                _BROWSER_SETUP_SECONDS,
                "Browser launch, context, page, and cookie-injection latency",
                {},
                setup_started,
            )

            # Navigate with domcontentloaded — Cloudflare challenge pages never reach
            # networkidle because the challenge keeps the network busy. We load the
            # initial HTML fast, detect the challenge, then actively poll for resolution.
            navigation_started = time.monotonic()
            try:
                navigation_response = await page.goto(
                    url, wait_until="domcontentloaded", timeout=15000
                )
            except Exception:
                observe_elapsed(
                    _BROWSER_NAVIGATION_SECONDS,
                    "Browser goto and challenge-resolution latency",
                    {},
                    navigation_started,
                )
                raise

            # Check for bot challenges (Cloudflare / DDoS-Guard)
            title = await page.title()
            current_url = page.url
            challenge_resolved = False
            if _is_bot_challenge(title, current_url):
                logger.info(
                    "Bot challenge detected on %s, polling for resolution...", url
                )
                # Active polling: check every 2s for up to 30s for the challenge to clear.
                # The challenge resolves when either:
                #   a) The page navigates to the real target URL (CF issues 302)
                #   b) cf_clearance cookie appears in the browser context
                resolved = False
                for attempt in range(15):
                    await page.wait_for_timeout(2000)
                    title = await page.title()
                    current_url = page.url
                    if not _is_bot_challenge(title, current_url):
                        logger.info(
                            "Bot challenge resolved on attempt %d for %s (URL: %s)",
                            attempt + 1,
                            url,
                            current_url,
                        )
                        resolved = True
                        break
                    # Also check for cf_clearance cookie as secondary signal
                    cookies = await context.cookies()
                    if any(c.get("name") == "cf_clearance" for c in cookies):
                        logger.info(
                            "Bot challenge resolved (cf_clearance cookie) on attempt %d for %s",
                            attempt + 1,
                            url,
                        )
                        resolved = True
                        break
                    logger.debug(
                        "Bot challenge attempt %d/15 for %s (title=%s)",
                        attempt + 1,
                        url,
                        title,
                    )

                if not resolved:
                    observe_elapsed(
                        _BROWSER_NAVIGATION_SECONDS,
                        "Browser goto and challenge-resolution latency",
                        {},
                        navigation_started,
                    )
                    logger.warning(
                        "Bot challenge persisted after 30s for %s — skipping to FlareSolverr",
                        url,
                    )
                    # Don't return challenge-page content as a valid scrape.
                    # Return None so the pipeline falls through to Tier 3.5 (FlareSolverr).
                    return None

                challenge_resolved = True
                # Re-read title and URL after challenge (may have navigated)
                title = await page.title()
                current_url = page.url

            observe_elapsed(
                _BROWSER_NAVIGATION_SECONDS,
                "Browser goto and challenge-resolution latency",
                {},
                navigation_started,
            )

            # Resolve provider widgets before extraction while this page and
            # its cookie-bearing context are still alive. Every terminal path
            # (success, CAPTCHA-unresolved, barrier, or empty/falsy HTML) and
            # any raised exception must sample the extraction histogram exactly
            # once, so the whole phase is guarded by a single finally.
            extraction_started = time.monotonic()
            try:
                from .captcha import resolve_captcha

                unresolved_captcha, attempts = await resolve_captcha(page, url)
                if unresolved_captcha:
                    return {
                        "error": "CAPTCHA challenge could not be resolved",
                        "error_code": "CAPTCHA_UNRESOLVED",
                        "markdown": "",
                        "source": "captcha",
                        "url": url,
                        "barrier": {
                            "detected": True,
                            "type": "captcha",
                            "provider": unresolved_captcha.provider,
                            "confidence": unresolved_captcha.confidence,
                            "detail": unresolved_captcha.detail,
                            "attempted_strategies": attempts,
                        },
                    }
                captcha_resolved = bool(attempts)
                navigation_status = getattr(navigation_response, "status", None)
                if (
                    type(navigation_status) is int
                    and navigation_status >= 400
                    and not captcha_resolved
                    and not challenge_resolved
                ):
                    return _http_status_error(url, navigation_status, "playwright")
                if captcha_resolved:
                    # Remove solved widget DOM before extraction so challenge markup
                    # cannot be returned or cached as successful page content.
                    try:
                        await page.evaluate(
                            """document.querySelectorAll([
                                '.g-recaptcha', '.h-captcha', '.cf-turnstile',
                                'iframe[src*="recaptcha"]', 'iframe[src*="hcaptcha"]',
                                'iframe[src*="turnstile"]',
                                '[name="g-recaptcha-response"]',
                                '[name="h-captcha-response"]',
                                '[name="cf-turnstile-response"]'
                            ].join(',')).forEach((element) => element.remove())"""
                        )
                    except Exception as exc:
                        logger.debug(
                            "Could not remove solved CAPTCHA widget DOM: %s", exc
                        )
                    title = await page.title()
                    current_url = page.url

                # If the challenge caused a redirect to the real site, ensure the
                # real page's content is fully loaded before extracting.
                if current_url != url:
                    logger.info(
                        "Challenge redirected to %s, waiting for full page load...",
                        current_url,
                    )
                    try:
                        await page.wait_for_load_state("networkidle", timeout=30000)
                    except Exception:
                        logger.debug(
                            "networkidle timeout on redirected page %s, continuing with current content",
                            current_url,
                        )

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
                html = await retry_transient(page.content)
                markdown = html_to_markdown(html) if html else ""

                barrier = _classify_barrier(title, url, markdown, html)
                barrier_blocks = barrier.detected
                if not markdown or len(markdown) < 500 or barrier_blocks:
                    for attempt in range(2):
                        logger.info(
                            "SPA retry %d for %s (markdown: %d chars)",
                            attempt + 1,
                            url,
                            len(markdown),
                        )
                        await retry_transient(
                            page.evaluate,
                            "window.scrollTo(0, document.body.scrollHeight)",
                        )
                        await page.wait_for_timeout(3000)

                        html = await retry_transient(page.content)
                        markdown = html_to_markdown(html) if html else ""
                        barrier = _classify_barrier(title, url, markdown, html)
                        barrier_blocks = barrier.detected
                        if markdown and len(markdown) >= 500 and not barrier_blocks:
                            logger.info(
                                "SPA retry %d succeeded for %s (%d chars)",
                                attempt + 1,
                                url,
                                len(markdown),
                            )
                            break

                if html:
                    markdown = html_to_markdown(html)
                    if markdown and len(markdown) > 50:
                        barrier = _classify_barrier(title, url, markdown, html)
                        # Post-extraction block-page gate (#586): a challenge
                        # interstitial whose markdown matches >=2
                        # BLOCK_PAGE_PATTERNS scores blocking "fail" — refuse
                        # it here so it can never ship as healthy page content.
                        # The gate requires challenge corroboration (a barrier-
                        # provider hit or an explicit challenge marker in the
                        # text) so ordinary pages that happen to co-occur with
                        # two generic block patterns (cookie banner + paywall,
                        # say) are not refused (#586 review).
                        from .extract import BLOCK_PAGE_PATTERNS, _check_block_page

                        cleared_captcha = (
                            captcha_resolved and barrier.barrier_type != "captcha"
                        )
                        _, block_status = _check_block_page(
                            markdown, captcha_resolved=cleared_captcha
                        )
                        matched_patterns = [
                            pattern.pattern
                            for pattern in BLOCK_PAGE_PATTERNS
                            if pattern.search(markdown.lower())
                        ]
                        challenge_corroborated = (
                            barrier.detected or barrier.provider is not None
                        ) or any(
                            marker in markdown.lower()
                            for marker in (
                                "javascript is disabled",
                                "enable javascript",
                                "javascript is required",
                                "couldn't load",
                                "couldn’t load",
                                "/_fs-ch-",
                                "verify you are",
                            )
                        )
                        block_fail_refusal = (
                            block_status == "fail"
                            and len(matched_patterns) >= 2
                            and challenge_corroborated
                        )
                        barrier_hit = barrier.detected and barrier.confidence > 0.7
                        if barrier_hit or block_fail_refusal:
                            reason = (
                                f"barrier {barrier.barrier_type} "
                                f"(confidence: {barrier.confidence:.2f})"
                                if barrier.detected
                                else "blocking interstitial (block_detected: fail)"
                            )
                            return {
                                "error": f"Barrier detected: {reason}",
                                "barrier": {
                                    "detected": True,
                                    "type": barrier.barrier_type or "suspicious",
                                    "provider": barrier.provider,
                                    "confidence": barrier.confidence,
                                    "detail": barrier.detail,
                                },
                                "markdown": "",
                                "source": "barrier-detection",
                                "url": url,
                            }
                        await store_cookies(url, context)
                        return {
                            "markdown": markdown,
                            "source": "playwright",
                            "url": url,
                            "raw_html_start": html,
                            **(
                                {
                                    "_captcha_resolution": {
                                        "verified": True,
                                        "markdown_digest": hashlib.sha256(
                                            markdown.encode()
                                        ).hexdigest(),
                                        "html_digest": hashlib.sha256(
                                            html.encode()
                                        ).hexdigest(),
                                    }
                                }
                                if cleared_captcha
                                else {}
                            ),
                        }
            finally:
                _observe_extraction(extraction_started)
        except BaseException:
            # AsyncExitStack performs the resource cleanup; preserve the
            # original exception for callers and cancellation handling.
            if pool.quarantined:
                _quarantine_browser("browser_pool_cleanup_incomplete")
                raise BrowserCleanupIncompleteError(
                    "browser pool cleanup incomplete"
                ) from None
            raise

    return None


async def fetch_via_llms_txt(url: str, client: httpx.AsyncClient) -> dict | None:
    """Tier 1: Check for /llms.txt at the site root."""
    llms_url = f"{extract_domain(url, include_scheme=True)}/llms.txt"
    try:
        resp = await client.get(llms_url, allow_redirects=True, timeout=10)  # type: ignore[call-arg]
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
        resp = await client.get(  # type: ignore[call-arg]
            url,
            headers={"Accept": "text/markdown, text/plain;q=0.9, */*;q=0.8"},
            allow_redirects=True,
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
                result: dict = {
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

            # HTML fallback: if the content type is HTML, convert it to markdown.
            # This catches sites behind Akamai/Cloudflare that block Tier 3
            # (Playwright) but return HTML on a curl_cffi GET — we already have
            # the content, no need to fall through to a failing Tier 3.
            is_html = "text/html" in ct
            if is_html and resp.text and len(resp.text) > 100:
                markdown = html_to_markdown(resp.text)
                if markdown and len(markdown) > 50:
                    logger.info("Tier 2 hit: content negotiation (HTML→md) for %s", url)
                    result = {
                        "markdown": markdown,
                        "source": "content-negotiation",
                        "url": url,
                        # Volume gate input: lets the quality assessment flag
                        # anomalously thin output relative to the source (#587).
                        # Stored as a native int — consumers read it directly.
                        "source_html_size": len(resp.text),
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
    # In protected capture mode, never run page JavaScript in the scraper
    # process: it shares application/control connectivity. Delegate the same
    # navigation through the isolated browser renderer instead.
    if protected_source_mode():
        return await _fetch_via_browser_svc(url)

    try:
        pw_proxy = _get_playwright_proxy()

        # Try with proxy first — wrap in its own try/except so exceptions
        # from unreachable proxies trigger the fail-open retry rather than
        # falling through to the generic "Tier 3 miss" handler
        try:
            result = await _playwright_fetch_with_proxy(url, pw_proxy)
        except Exception as e:
            if pw_proxy is None:
                raise  # re-raise so outer handler can classify as browser_error
            logger.warning(
                "Proxy (%s) failed for %s: %s",
                pw_proxy.get("server", "unknown"),
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
        error_str = str(e)
        # Classify known Playwright crash signatures
        if "page is navigating" in error_str or "scrollheight" in error_str.lower():
            logger.warning("Tier 3 browser crash for %s: %s", url, e)
            return {
                "error": f"Browser error: {error_str}",
                "error_type": "browser_error",
                "markdown": "",
                "source": "playwright-error",
                "url": url,
            }
        logger.warning("Tier 3 miss for %s: %s", url, e)
    return None


async def fetch_via_flaresolverr(url: str) -> dict | None:
    """Tier 3.5: Route through FlareSolverr for hard Cloudflare challenges.

    Requires the flare-solverr service to be running (profile-gated in
    docker-compose.yml). Gracefully falls back if unavailable.
    """
    try:
        flare_url = trusted_control_base_url(FLARE_SOLVERR_URL, "flare")
        async with trusted_control_httpx_client(service="flare", timeout=60) as client:
            resp = await client.post(
                flare_url,
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


_BROWSER_NAVIGATION_TIMEOUT_MS = 45000
_BROWSER_HTTP_TIMEOUT_SECONDS = _BROWSER_NAVIGATION_TIMEOUT_MS / 1000 + 10
_BROWSER_CLEANUP_TIMEOUT_SECONDS = 20


async def _fetch_via_browser_svc(url: str) -> dict | None:
    """Fallback: use the browser-svc API to navigate and extract content.

    The browser-svc's Playwright configuration is able to handle sites
    that the scraper-svc's Tier 3 cannot (e.g., Substack redirect chains).

    Browser-svc is available at http://browser-svc:8012.
    """
    browser_svc_url = trusted_control_base_url(_settings.browser_svc_url, "browser")
    session_id = None
    try:
        # Create a browser session
        async with trusted_control_httpx_client(
            service="browser", timeout=_BROWSER_HTTP_TIMEOUT_SECONDS
        ) as client:
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
                json={
                    "action": "navigate",
                    "url": url,
                    "timeout": _BROWSER_NAVIGATION_TIMEOUT_MS,
                },
            )
            navigation = nav_resp.json()
            if not navigation.get("success"):
                logger.warning("Browser-svc navigation failed for %s", url)
                return None
            navigation_result = navigation.get("result") or {}
            navigation_status = navigation_result.get("http_status")
            if (
                type(navigation_status) is int
                and navigation_status >= 400
                and not _is_bot_challenge(
                    str(navigation_result.get("title") or ""),
                    str(navigation_result.get("url") or url),
                )
            ):
                return _http_status_error(url, navigation_status, "browser-svc")

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

                    if type(navigation_status) is int and navigation_status >= 400:
                        return _http_status_error(url, navigation_status, "browser-svc")

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

                    if type(navigation_status) is int and navigation_status >= 400:
                        return _http_status_error(url, navigation_status, "browser-svc")

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

            if type(navigation_status) is int and navigation_status >= 400:
                return _http_status_error(url, navigation_status, "browser-svc")

    except Exception as e:
        logger.warning("Browser-svc fallback failed for %s: %s", url, e)
    finally:
        # Clean up the browser session
        if session_id:
            try:
                async with trusted_control_httpx_client(
                    service="browser", timeout=_BROWSER_CLEANUP_TIMEOUT_SECONDS
                ) as c:
                    await c.delete(f"{browser_svc_url}/browsers/{session_id}")
            except Exception:
                pass

    return None


async def _get_browser_page_content(
    browser_svc_url: str, session_id: str
) -> str | None:
    """Get the full page HTML from a browser-svc session via executeScript."""
    try:
        async with trusted_control_httpx_client(
            service="browser", timeout=15
        ) as client:
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
