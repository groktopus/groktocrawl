"""Lightweight meta tag extraction from raw HTML.

Fetches a URL and extracts <title>, <meta name="description">,
and <meta property="og:description"> with a single HTTP GET.
No Playwright, no readability, no markdown conversion.
"""

import logging

from bs4 import BeautifulSoup

from .source_http import source_httpx_client

logger = logging.getLogger(__name__)


async def fetch_meta_tags(url: str, timeout: int = 15) -> dict:
    """Fetch a URL and extract meta tags from raw HTML.

    Performs a single GET and parses the <head> with BeautifulSoup.
    Returns dict with keys: title, description, og_description.
    Missing fields are set to None.
    """
    result: dict[str, str | None] = {
        "title": None,
        "description": None,
        "og_description": None,
    }

    try:
        async with source_httpx_client(
            follow_redirects=True, timeout=timeout
        ) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                logger.warning("Meta fetch returned %d for %s", resp.status_code, url)
                return result

            soup = BeautifulSoup(resp.text, "html.parser")

            # <title>
            title_tag = soup.find("title")
            if title_tag and title_tag.string:
                result["title"] = str(title_tag.string).strip()

            # <meta name="description" content="...">
            meta_desc = soup.find("meta", attrs={"name": "description"})
            if meta_desc and meta_desc.get("content"):
                result["description"] = str(meta_desc["content"]).strip()

            # <meta property="og:description" content="...">
            og_desc = soup.find("meta", attrs={"property": "og:description"})
            if og_desc and og_desc.get("content"):
                result["og_description"] = str(og_desc["content"]).strip()

    except Exception as e:
        logger.warning("Meta fetch failed for %s: %s", url, e)

    return result
