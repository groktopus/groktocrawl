"""
Substack adapter — extracts articles via RSS feed, with page-render fallback.

Fallback chain:
  1. RSS/Atom feed — ``<pub>/feed`` returns structured XML with full
     article HTML in ``<content:encoded>``.  No auth, works for both
     ``*.substack.com`` and vanity domains.
  2. Readability — fetch the page HTML and extract via readability-lxml.
  3. Browser render — full browser-svc fallback for really tricky pages.

Vanity domain detection:
  The adapter fingerprints an origin by fetching ``/feed`` and checking
  for ``<generator>Substack</generator>`` in the RSS XML.  The result is
  cached per-domain for 1 hour so subsequent articles on the same origin
  skip the probe.

URL patterns:
  - ``*.substack.com/p/<slug>`` — direct match
  - ``*.substack.com/pub/<slug>`` — direct match
  - ``<vanity>/p/<slug>`` — detected via ``can_handle()`` probe
"""

from __future__ import annotations

import logging
import re
import time
import xml.etree.ElementTree as ET
from urllib.parse import urlparse

import httpx

from .base import AdapterContext, AdapterError, AdapterResult, SiteAdapter, adapter

logger = logging.getLogger(__name__)

# ── URL pattern matching ─────────────────────────────────────────

_SUBSTACK_URL_PATTERNS = [
    # *.substack.com/p/<slug>  — direct article URLs
    re.compile(r"^https?://[^.]+\.substack\.com/p/"),
    # *.substack.com/pub/<slug> — published-post URLs
    re.compile(r"^https?://[^.]+\.substack\.com/pub/"),
    # Vanity domains with /p/ in path — probed in can_handle()
    re.compile(r"^https?://[^/]+/p/"),
]

# ── Vanity-domain probe cache ────────────────────────────────────

# { origin -> (expires_at, is_substack) }
_VANITY_CACHE: dict[str, tuple[float, bool]] = {}
_VANITY_CACHE_TTL = 3600  # 1 hour


def _is_substack_origin(origin: str) -> bool:
    """Check whether *origin* hosts a Substack publication.

    Probes ``{origin}/feed`` and looks for the Substack generator tag.
    Results are cached for ``_VANITY_CACHE_TTL`` seconds.
    """
    now = time.time()
    cached = _VANITY_CACHE.get(origin)
    if cached and cached[0] > now:
        return cached[1]

    # Fast path: subdomains are always Substack
    parsed = urlparse(origin)
    hostname = parsed.hostname or ""
    if hostname.endswith(".substack.com"):
        _VANITY_CACHE[origin] = (now + _VANITY_CACHE_TTL, True)
        return True

    # Probe via RSS feed
    feed_url = f"{origin}/feed"
    try:
        resp = httpx.get(feed_url, timeout=8, follow_redirects=True)
        if resp.status_code == 200 and "Substack" in resp.text:
            # Quick check: look for <generator>Substack</generator>
            if re.search(r"<generator[^>]*>Substack<", resp.text):
                _VANITY_CACHE[origin] = (now + _VANITY_CACHE_TTL, True)
                return True
    except Exception:
        logger.debug("Substack probe failed for %s", feed_url)

    _VANITY_CACHE[origin] = (now + _VANITY_CACHE_TTL, False)
    return False


# ── Feed URL construction ────────────────────────────────────────


def _feed_url_for(origin: str) -> str:
    """Return the RSS feed URL for a Substack origin."""
    return f"{origin}/feed"


# ── RSS feed parsing ─────────────────────────────────────────────

_NAMESPACES = {
    "content": "http://purl.org/rss/1.0/modules/content/",
    "dc": "http://purl.org/dc/elements/1.1/",
    "atom": "http://www.w3.org/2005/Atom",
}


def _parse_rss_items(feed_xml: str) -> list[dict]:
    """Parse RSS feed XML and return a list of item dicts.

    Each item dict contains: title, link, description, creator,
    pub_date, content_encoded.
    """
    items = []
    try:
        root = ET.fromstring(feed_xml)
        # RSS 2.0: /rss/channel/item
        for item_elem in root.iter("item"):
            item: dict = {}
            for child in item_elem:
                tag = child.tag.split("}", 1)[-1] if "}" in child.tag else child.tag
                if tag == "title":
                    item["title"] = child.text or ""
                elif tag == "link":
                    item["link"] = child.text or ""
                elif tag == "description":
                    item["description"] = child.text or ""
                elif tag == "creator" and "dc" in child.tag:
                    item["creator"] = child.text or ""
                elif tag == "pubDate":
                    item["pub_date"] = child.text or ""
                elif tag == "encoded" and "content" in child.tag:
                    item["content_encoded"] = child.text or ""
            if item.get("link"):
                items.append(item)
    except ET.ParseError as exc:
        logger.debug("RSS parse error: %s", exc)
    return items


def _find_item_by_link(items: list[dict], target_url: str) -> dict | None:
    """Find the feed item whose ``link`` matches *target_url*."""
    for item in items:
        if item.get("link", "").rstrip("/") == target_url.rstrip("/"):
            return item
    # Fallback: match by slug (strip query parameters)
    target_slug = target_url.split("?")[0].rstrip("/").split("/")[-1]
    for item in items:
        link_slug = item.get("link", "").split("?")[0].rstrip("/").split("/")[-1]
        if link_slug == target_slug:
            return item
    return None


# ── Content extraction ───────────────────────────────────────────


async def _fetch_feed(origin: str) -> str | None:
    """Fetch the RSS feed XML from *origin*/feed."""
    feed_url = _feed_url_for(origin)
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(
                feed_url,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/131.0.0.0 Safari/537.36"
                    ),
                },
            )
            if resp.status_code == 200:
                return resp.text
            logger.debug("Feed fetch returned %d for %s", resp.status_code, feed_url)
    except Exception as exc:
        logger.debug("Feed fetch failed for %s: %s", feed_url, exc)
    return None


def _rss_content_to_markdown(html_content: str) -> str:
    """Convert RSS article HTML to clean markdown.

    Uses readability-lxml + markdownify (both are standard deps of
    the scraper-svc).  Falls back to BeautifulSoup text extraction.
    """
    try:
        from readability import Document
        from markdownify import markdownify as md

        doc = Document(html_content)
        summary = doc.summary()
        markdown = md(summary, heading_style="ATX", strip=["script", "style"])
        markdown = re.sub(r"\n{3,}", "\n\n", markdown)
        return markdown.strip()
    except ImportError:
        pass
    except Exception as exc:
        logger.debug("readability-lxml failed: %s", exc)

    # Fallback: BeautifulSoup text extraction
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html_content, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        text = soup.get_text(separator="\n", strip=True)
        return text[:20000]  # Limit to 20K chars
    except Exception as exc:
        logger.debug("BeautifulSoup fallback failed: %s", exc)

    return html_content[:10000]


async def _fetch_via_readability(url: str) -> str | None:
    """Fallback: fetch the page HTML and extract via readability-lxml."""
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(
                url,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/131.0.0.0 Safari/537.36"
                    ),
                },
            )
            if resp.status_code != 200:
                return None

            html = resp.text
            return _rss_content_to_markdown(html)
    except Exception as exc:
        logger.debug("Readability fetch failed for %s: %s", url, exc)
    return None


async def _fetch_via_browser(url: str, ctx: AdapterContext) -> str | None:
    """Last-resort fallback: render the page via browser-svc."""
    browser_svc_url = ctx.config.get("BROWSER_SVC_URL", "http://browser-svc:8012")
    session_id = None
    try:
        async with httpx.AsyncClient(timeout=45) as client:
            # Create session
            create_resp = await client.post(
                f"{browser_svc_url}/browsers",
                json={"ttl": 60},
            )
            if create_resp.status_code != 200:
                return None
            session_id = create_resp.json().get("id")
            if not session_id:
                return None

            # Navigate
            nav_resp = await client.post(
                f"{browser_svc_url}/browsers/{session_id}/execute",
                json={"action": "navigate", "url": url, "timeout": 45000},
            )
            if not nav_resp.json().get("success"):
                return None

            # Extract article text
            text_resp = await client.post(
                f"{browser_svc_url}/browsers/{session_id}/execute",
                json={
                    "action": "executeScript",
                    "script": (
                        "document.querySelector('article')?.innerText "
                        "|| document.querySelector('[class*=\"post\"]')?.innerText "
                        "|| document.body.innerText"
                    ),
                },
            )
            if text_resp.json().get("success"):
                return text_resp.json().get("result", {}).get("script_result", "") or None

    except Exception as exc:
        logger.debug("Browser fallback failed for %s: %s", url, exc)
    finally:
        if session_id:
            try:
                async with httpx.AsyncClient(timeout=5) as c:
                    await c.delete(f"{browser_svc_url}/browsers/{session_id}")
            except Exception as e:
                logger.debug("Session cleanup failed for %s: %s", url, e)
    return None


# ── Adapter class ────────────────────────────────────────────────


@adapter
class SubstackAdapter(SiteAdapter):
    """Extract articles from Substack publications.

    Primary path: RSS feed lookup (fast, no auth, structured data).
    Fallback: readability-lxml extraction from the article page HTML.
    Last resort: browser-svc render.
    """

    name = "substack"

    patterns = _SUBSTACK_URL_PATTERNS

    # High priority — Substack should be checked before the generic pipeline
    priority = 200

    async def can_handle(self, url: str) -> bool:
        """Fast pre-check: only handle URLs on Substack origins."""
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.hostname}" if parsed.hostname else url
        return _is_substack_origin(origin)

    async def scrape(self, url: str, ctx: AdapterContext) -> AdapterResult:
        logger.info("Substack adapter: url=%s", url)

        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.hostname}"

        # ── Tier 1: RSS feed lookup ──────────────────────────────
        feed_xml = await ctx.with_timeout(
            _fetch_feed(origin), timeout=10
        )
        if feed_xml:
            items = _parse_rss_items(feed_xml)
            if items:
                item = _find_item_by_link(items, url)
                if item and item.get("content_encoded"):
                    logger.info(
                        "Substack adapter: feed hit for %s",
                        item.get("title", url),
                    )
                    markdown = _rss_content_to_markdown(item["content_encoded"])
                    metadata: dict = {
                        "title": item.get("title", ""),
                        "author": item.get("creator", ""),
                        "publication": "",
                        "published_date": item.get("pub_date", ""),
                        "source": "substack-rss",
                    }
                    # Try to get publication name from channel-level feed data
                    try:
                        root = ET.fromstring(feed_xml)
                        channel = root.find("channel")
                        if channel is not None:
                            title_el = channel.find("title")
                            if title_el is not None and title_el.text:
                                metadata["publication"] = title_el.text
                    except ET.ParseError:
                        pass

                    return AdapterResult(
                        success=True,
                        markdown=markdown,
                        metadata=metadata,
                        source="substack-rss",
                        url=url,
                    )
                logger.debug("Feed items found but no match for %s", url)

        # ── Tier 2: Readability extraction ───────────────────────
        logger.info("Substack adapter: trying readability for %s", url)
        readability_md = await ctx.with_timeout(
            _fetch_via_readability(url), timeout=12
        )
        if readability_md:
            return AdapterResult(
                success=True,
                markdown=readability_md,
                metadata={
                    "source": "substack-readability",
                },
                source="substack-readability",
                url=url,
            )

        # ── Tier 3: Browser render ───────────────────────────────
        logger.info("Substack adapter: trying browser for %s", url)
        browser_text = await ctx.with_timeout(
            _fetch_via_browser(url, ctx), timeout=35
        )
        if browser_text:
            return AdapterResult(
                success=True,
                markdown=browser_text,
                metadata={
                    "source": "substack-browser",
                },
                source="substack-browser",
                url=url,
            )

        raise AdapterError(
            f"Could not extract content from Substack URL {url}"
        )
