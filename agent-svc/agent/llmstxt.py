"""LLMs.txt generation — scan a website and produce an llms.txt file.

The llms.txt format follows Matt Webb's spec (https://llmstxt.org/):
a markdown file at the site root that helps LLMs discover and prioritize
the site's key pages.
"""

import logging
import re
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from common.url import extract_domain

logger = logging.getLogger(__name__)

# Boilerplate line signals — if a line contains any of these, skip it
_BOILERPLATE_SIGNALS = [
    "cookie",
    "cookies",
    "accept all",
    "accept cookies",
    "skip to",
    "skip navigation",
    "skip nav",
    "navigation",
    "nav bar",
    "main navigation",
    "footer",
    "copyright",
    "all rights reserved",
    "privacy policy",
    "terms of service",
    "terms and conditions",
    "this website uses",
    "we use cookies",
    "sign in",
    "sign up",
    "log in",
    "subscribe",
    "advertisement",
    "sponsored",
]


def _is_boilerplate(line: str) -> bool:
    """Check if a line is likely boilerplate (nav, cookie banner, footer, etc.)."""
    lower = line.lower().strip()
    if len(lower) < 30:
        return True  # Very short lines are rarely good descriptions
    for signal in _BOILERPLATE_SIGNALS:
        if signal in lower:
            return True
    return False


def _extract_description(text: str, max_chars: int = 300) -> str:
    """Extract a clean, sentence-boundary-aware description from markdown text.

    Skips boilerplate lines (nav, cookie, footer, short lines), then scans
    forward to the nearest sentence boundary after a minimum threshold.
    Falls back to truncated text with ellipsis if no boundary is found.
    """
    if not text:
        return ""

    candidates: list[str] = []
    for line in text.split("\n"):
        stripped = line.strip()
        # Skip headings, images, links-as-first-element, and boilerplate
        if not stripped:
            continue
        if (
            stripped.startswith("#")
            or stripped.startswith("!")
            or stripped.startswith("[")
            or stripped.startswith(">")
        ):
            continue
        if _is_boilerplate(stripped):
            continue
        candidates.append(stripped)

    if not candidates:
        # Fallback: use first substantive line from full text
        for line in text.split("\n"):
            stripped = line.strip()
            if (
                len(stripped) >= 30
                and not stripped.startswith("#")
                and not stripped.startswith("!")
            ):
                candidates.append(stripped)
                break

    if not candidates:
        return text[:max_chars].strip()

    # Take the first good candidate and find a sentence boundary
    desc = candidates[0]
    if len(desc) <= 100:
        # If the first candidate is short, try appending more
        for extra in candidates[1:]:
            desc += " " + extra
            if len(desc) >= 100:
                break

    # Find sentence boundary after minimum 100 chars
    min_length = min(100, len(desc))
    rest = desc[min_length:]
    # Look for sentence-ending punctuation followed by space or end-of-string
    boundary_match = re.search(r"[.!?](?:\s|$)", rest)
    if boundary_match:
        end_pos = min_length + boundary_match.end()
        # Include the punctuation but not the trailing space
        result = desc[:end_pos].rstrip()
    else:
        # No clean boundary — truncate with ellipsis if we cut
        if len(desc) > max_chars:
            result = desc[:max_chars].rstrip() + "..."
        else:
            result = desc

    return result.strip()


async def discover_pages(url: str, max_pages: int = 50) -> list[str]:
    """Discover page URLs on a site by fetching the homepage and finding same-domain links."""
    discovered: list[str] = []
    seen = set()

    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                logger.warning("Failed to fetch %s: %d", url, resp.status_code)
                return [url]  # fallback: just return the input URL

            soup = BeautifulSoup(resp.text, "html.parser")
            base_domain = extract_domain(url)
            base_url = extract_domain(url, include_scheme=True)
            for a in soup.find_all("a", href=True):
                href = a["href"].strip()
                full_url = urljoin(base_url, href)

                # Skip external links, anchors, mailto, etc.
                fd = extract_domain(full_url)
                if fd and fd != base_domain:
                    continue
                from urllib.parse import urlparse

                if not urlparse(full_url).scheme.startswith("http"):
                    continue
                if full_url in seen:
                    continue

                seen.add(full_url)
                discovered.append(full_url)

                if len(discovered) >= max_pages:
                    break

    except Exception as e:
        logger.warning("Page discovery failed for %s: %s", url, e)
        return [url]

    if not discovered:
        discovered = [url]

    return discovered


async def extract_title_and_description(
    page_url: str, scraper_url: str
) -> tuple[str, str]:
    """Extract the title and a short description from a page.

    Tries meta tag extraction first (cheap, one GET). Falls back to
    full scrape with sentence-boundary extraction if meta tags are
    missing or too short.
    """
    title = ""
    description = ""

    # Tier 1+2: Try lightweight meta tag extraction first
    meta_url = f"{scraper_url}/scrape/meta"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(meta_url, json={"url": page_url})
            if resp.status_code == 200:
                meta = resp.json()
                if meta.get("success"):
                    if meta.get("title"):
                        title = meta["title"]
                    # Prefer meta description, fall back to og:description
                    meta_desc = meta.get("description") or meta.get("og_description")
                    if meta_desc and len(meta_desc.strip()) >= 40:
                        return title or page_url.rstrip("/").split("/")[-1].replace(
                            "-", " "
                        ).title(), meta_desc.strip()
    except Exception as e:
        logger.debug("Meta fetch failed for %s: %s", page_url, e)

    # Tier 3+4: Full scrape with sentence-boundary extraction
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(f"{scraper_url}/scrape", json={"url": page_url})
            if resp.status_code == 200:
                body = resp.json()
                if body.get("success"):
                    md = body.get("data", {}).get("markdown", "")
                    source = body.get("data", {}).get("source", "")

                    # If we got llms.txt directly, extract just the title
                    if source == "llms.txt":
                        lines = md.strip().split("\n")
                        for line in lines:
                            if line.startswith("# ") and not line.startswith("## "):
                                title = line[2:].strip()
                                break

                    # Extract title from first heading in markdown
                    if not title:
                        for line in md.split("\n"):
                            line = line.strip()
                            if line.startswith("# ") and not line.startswith("## "):
                                title = line[2:].strip()
                                break

                    # Extract description: sentence-boundary-aware with boilerplate skipping
                    description = _extract_description(md)
    except Exception as e:
        logger.warning("Failed to scrape %s: %s", page_url, e)

    return title or page_url.rstrip("/").split("/")[-1].replace(
        "-", " "
    ).title(), description


def generate_llms_txt(site_url: str, pages: list[dict]) -> str:
    """Compile discovered page info into the llms.txt format."""
    site_name = extract_domain(site_url)

    lines = [f"# {site_name}"]
    lines.append("")
    lines.append(
        f"> Auto-generated by GroktoCrawl. This file helps AI agents discover and prioritize content on {site_url}."
    )
    lines.append("")

    for i, page in enumerate(pages):
        title = page.get("title", "")
        url = page.get("url", "")
        desc = page.get("description", "")

        if desc:
            lines.append(f"- [{title}]({url}): {desc}")
        else:
            lines.append(f"- [{title}]({url})")

    lines.append("")
    lines.append("---")
    lines.append(
        f"> This llms.txt was auto-generated by [GroktoCrawl](https://github.com/groktopus/groktocrawl) on {__import__('datetime').datetime.now().strftime('%Y-%m-%d')}."
    )

    return "\n".join(lines)


async def generate_llmstxt(
    url: str,
    max_pages: int = 50,
    scraper_url: str = "http://scraper-svc:8001",
) -> dict:
    """Full pipeline: discover → scrape → compile.

    Returns dict with keys: llms_txt (str), url (str), pages_discovered (int), pages_summarized (int)
    """
    discovered_urls = await discover_pages(url, max_pages)
    logger.info("Discovered %d pages from %s", len(discovered_urls), url)

    pages = []
    for page_url in discovered_urls[:max_pages]:
        title, description = await extract_title_and_description(page_url, scraper_url)
        pages.append({"url": page_url, "title": title, "description": description})

    llms_content = generate_llms_txt(url, pages)

    return {
        "llms_txt": llms_content,
        "url": url,
        "pages_discovered": len(discovered_urls),
        "pages_summarized": len(pages),
    }
