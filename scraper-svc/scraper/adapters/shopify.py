"""
Shopify adapter — extracts blog/article content from Shopify-hosted stores.

Bypasses UCP content-negotiation trap by fetching the page HTML directly
with a standard browser User-Agent instead of Accept: text/markdown.

Fallback chain:
  1. Readability-lxml — fetch HTML, extract main content via scrape_page()
  2. Playwright render — headless browser for JS-heavy pages
  3. Generic tier — last resort (will hit UCP trap but that's intended)
"""

from __future__ import annotations

import logging
import re

from ..source_http import (
    trusted_control_base_url,
    trusted_control_httpx_client,
)
from ._helpers import scrape_page
from .base import AdapterContext, AdapterError, AdapterResult, SiteAdapter, adapter

logger = logging.getLogger(__name__)

_SHOPIFY_URL_PATTERNS = [
    re.compile(r"^https?://[^/]+/blogs/[^/]+/"),
    re.compile(r"^https?://[^/]+/products/[^/]+"),
    re.compile(r"^https?://[^/]+/collections/[^/]+"),
    re.compile(r"^https?://[^/]+/pages/[^/]+"),
]


async def _fetch_via_browser(url: str, ctx: AdapterContext) -> str | None:
    """Fallback: render the page via browser-svc Playwright pipeline.

    Creates a temporary browser session, navigates to the URL,
    extracts article/main content, and tears down the session.
    """
    browser_svc_url = trusted_control_base_url(
        ctx.config.get("BROWSER_SVC_URL", "http://browser-svc:8012"), "browser"
    )
    session_id = None
    try:
        async with trusted_control_httpx_client(service="browser", timeout=45) as client:
            # Create a short-lived browser session
            create_resp = await client.post(
                f"{browser_svc_url}/browsers",
                json={"ttl": 60},
            )
            if create_resp.status_code != 200:
                logger.debug(
                    "Shopify browser session creation failed: %d",
                    create_resp.status_code,
                )
                return None
            session_id = create_resp.json().get("id")
            if not session_id:
                return None

            # Navigate to the target URL
            nav_resp = await client.post(
                f"{browser_svc_url}/browsers/{session_id}/execute",
                json={"action": "navigate", "url": url, "timeout": 45000},
            )
            if not nav_resp.json().get("success"):
                logger.debug("Shopify browser navigation failed for %s", url)
                return None

            # Extract page content — try article first, then main, then body
            text_resp = await client.post(
                f"{browser_svc_url}/browsers/{session_id}/execute",
                json={
                    "action": "executeScript",
                    "script": (
                        "document.querySelector('article')?.innerText "
                        "|| document.querySelector('main')?.innerText "
                        "|| document.body.innerText"
                    ),
                },
            )
            if text_resp.json().get("success"):
                text = text_resp.json().get("result", {}).get("script_result", "") or ""
                if text and len(text) > 200:
                    logger.info(
                        "Shopify browser fallback hit for %s (%d chars)",
                        url,
                        len(text),
                    )
                    return text

    except Exception as exc:
        logger.debug("Shopify browser fallback failed for %s: %s", url, exc)
        return None
    finally:
        if session_id:
            try:
                async with trusted_control_httpx_client(service="browser", timeout=5) as c:
                    await c.delete(f"{browser_svc_url}/browsers/{session_id}")
            except Exception as e:
                logger.debug("Shopify browser session cleanup failed: %s", e)
    return None


@adapter
class ShopifyAdapter(SiteAdapter):
    """Extract content from Shopify-hosted stores.

    Primary path: readability-lxml extraction with browser User-Agent.
    Bypasses the UCP content-negotiation trap by avoiding Accept: text/markdown.
    Fallback: browser-svc Playwright render for JS-heavy pages.
    """

    name = "shopify"

    patterns = _SHOPIFY_URL_PATTERNS

    priority = 200

    async def scrape(self, url: str, ctx: AdapterContext) -> AdapterResult:
        logger.info("Shopify adapter: url=%s", url)

        # ── Tier 1: readability-lxml with browser User-Agent ────
        try:
            result = await ctx.with_timeout(scrape_page(url), timeout=12)
            if result and len(result) > 200:
                logger.info(
                    "Shopify adapter: readability hit for %s (%d chars)",
                    url,
                    len(result),
                )
                return AdapterResult(
                    success=True,
                    markdown=result,
                    metadata={"source": "shopify-readability"},
                    source="shopify-readability",
                    url=url,
                )
        except Exception as exc:
            logger.debug("Shopify readability failed for %s: %s", url, exc)

        # ── Tier 2: browser render ──────────────────────────────
        logger.info("Shopify adapter: trying browser for %s", url)
        browser_text = await ctx.with_timeout(_fetch_via_browser(url, ctx), timeout=35)
        if browser_text:
            return AdapterResult(
                success=True,
                markdown=browser_text,
                metadata={"source": "shopify-browser"},
                source="shopify-browser",
                url=url,
            )

        raise AdapterError(f"All Shopify extraction paths exhausted for {url}")
