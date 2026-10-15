"""
Greenhouse adapter — extracts job postings from Greenhouse public boards API.

Fallback chain:
  1. Greenhouse Boards API — single-job endpoint, no auth required
  2. Page scrape via readability-lxml — for rate-limited or blocked API calls
  3. AdapterError — falls through to the generic scrape pipeline

API docs: https://developers.greenhouse.io/job-board.html
Single-job endpoint: GET https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{id}?content=true
"""

from __future__ import annotations

import logging
import re
from urllib.parse import parse_qs, urlparse

import httpx

from ..source_http import source_httpx_client
from ._helpers import scrape_page
from .base import AdapterContext, AdapterError, AdapterResult, SiteAdapter, adapter

logger = logging.getLogger(__name__)

# ── URL pattern matching ─────────────────────────────────────────

_GREENHOUSE_URL_PATTERNS = [
    # boards.greenhouse.io/{board}/jobs/{id}
    re.compile(
        r"^https?://boards\.greenhouse\.io/"
        r"(?P<board>[^/]+)/jobs/(?P<job_id>\d+)"
    ),
    # Standalone iframe URL: greenhouse.io/embed/job_app?token={board}&gh_jid={id}
    re.compile(
        r"^https?://(?:www\.)?greenhouse\.io/embed/job_app"
        r"\?(?:[^&]*&)*token=(?P<board>[^&]+)"
        r"(?:&[^&]*)*gh_jid=(?P<job_id>\d+)"
    ),
]

# ── URL parsing ──────────────────────────────────────────────────


def _extract_board_and_job_id(url: str) -> tuple[str, str] | None:
    """Extract board token and job ID from a Greenhouse URL.

    Returns ``(board_token, job_id)`` or ``None`` if the URL doesn't
    match expected patterns.
    """
    for pattern in _GREENHOUSE_URL_PATTERNS:
        m = pattern.search(url)
        if m:
            return m.group("board"), m.group("job_id")
    return None


def _extract_gh_jid_from_query(url: str) -> str | None:
    """Extract gh_jid from a query parameter.

    Handles URLs like ``https://example.com/careers?gh_jid=12345``.
    """
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    jids = qs.get("gh_jid", [])
    return jids[0] if jids else None


# ── API helper ───────────────────────────────────────────────────

API_BASE = "https://boards-api.greenhouse.io"


async def _fetch_job_api(board: str, job_id: str) -> dict | None:
    """Fetch a single job posting from the Greenhouse Boards API.

    Returns the parsed JSON response dict, or ``None`` on failure.
    """
    url = f"{API_BASE}/v1/boards/{board}/jobs/{job_id}?content=true"
    try:
        async with source_httpx_client(timeout=15) as client:
            resp = await client.get(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (compatible; GroktoCrawl/0.7.0; Greenhouse adapter)",
                },
            )
            if resp.status_code == 200:
                return resp.json()
            elif resp.status_code == 404:
                logger.debug(
                    "Greenhouse API 404: job %s not found on board %s", job_id, board
                )
                return None
            elif resp.status_code == 429:
                logger.debug("Greenhouse API rate limited for board %s", board)
                return None
            else:
                logger.debug(
                    "Greenhouse API returned %d for board=%s job=%s",
                    resp.status_code,
                    board,
                    job_id,
                )
                return None
    except httpx.TimeoutException:
        logger.debug("Greenhouse API timed out for board=%s job=%s", board, job_id)
        return None
    except Exception as exc:
        logger.debug(
            "Greenhouse API failed for board=%s job=%s: %s", board, job_id, exc
        )
        return None


# ── HTML → markdown conversion ───────────────────────────────────


def _html_to_markdown(html: str) -> str:
    """Convert HTML job description to clean markdown.

    Uses readability-lxml + markdownify (both are standard deps of the
    scraper-svc). Falls back to BeautifulSoup text extraction.
    """
    try:
        from markdownify import markdownify as md
        from readability import Document

        doc = Document(html)
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

        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        text = soup.get_text(separator="\n", strip=True)
        return text[:20000]
    except Exception as exc:
        logger.debug("BeautifulSoup fallback failed: %s", exc)

    return html[:10000]


# ── Response formatting ─────────────────────────────────────────


def _format_job_as_markdown(data: dict, board: str, job_id: str) -> tuple[str, dict]:
    """Convert a Greenhouse job API response to markdown + metadata.

    Returns ``(markdown, metadata)``.
    """
    title = data.get("title", "")
    location_name = ""
    loc = data.get("location")
    if isinstance(loc, dict):
        location_name = loc.get("name", "")
    elif isinstance(loc, str):
        location_name = loc

    # Departments
    departments = [
        d.get("name", "") for d in data.get("departments", []) if d.get("name")
    ]

    # Employment type from metadata
    employment_type = ""
    for m in data.get("metadata", []):
        if m.get("id") == 1 or "employment" in m.get("name", "").lower():
            employment_type = m.get("value", "")
            break

    requisition_id = data.get("requisition_id", "")
    first_published = (data.get("first_published") or "")[:10]
    updated_at = (data.get("updated_at") or "")[:10]
    absolute_url = data.get("absolute_url", "")
    company_name = data.get("company_name", "")

    # Build metadata
    metadata: dict = {
        "title": title,
        "company": company_name,
        "location": location_name,
        "department": "; ".join(departments) if departments else "",
        "requisition_id": requisition_id,
        "employment_type": employment_type,
        "date_posted": first_published,
        "updated_at": updated_at,
        "source": "greenhouse-api",
        "url": absolute_url or f"https://boards.greenhouse.io/{board}/jobs/{job_id}",
    }

    # Build markdown
    parts: list[str] = []
    parts.append(f"# {title}")
    parts.append("")

    # Key details
    detail_items = [
        ("Company", company_name),
        ("Location", location_name),
        ("Department", "; ".join(departments) if departments else ""),
        ("Employment Type", employment_type),
        ("Requisition ID", requisition_id),
        ("Posted", first_published),
        ("Updated", updated_at),
    ]
    parts.append("| Field | Value |")
    parts.append("|-------|-------|")
    for label, val in detail_items:
        if val:
            parts.append(f"| **{label}** | {val} |")
    parts.append("")

    # Description
    content = data.get("content", "")
    if content:
        desc_md = _html_to_markdown(content)
        parts.append("## Description")
        parts.append("")
        parts.append(desc_md)
    else:
        parts.append("*No description available*")

    parts.append("")
    parts.append(
        f"*Source: [Greenhouse]({absolute_url or f'https://boards.greenhouse.io/{board}/jobs/{job_id}'})*"
    )

    markdown = "\n".join(parts).strip()
    return markdown, metadata


# ── Adapter class ────────────────────────────────────────────────


@adapter
class GreenhouseAdapter(SiteAdapter):
    """Extract job postings from Greenhouse-powered career pages."""

    name = "greenhouse"

    patterns = _GREENHOUSE_URL_PATTERNS

    priority = 190

    async def can_handle(self, url: str) -> bool:
        """Fast pre-check: handle if URL contains gh_jid query param or matches patterns."""
        return bool(_extract_board_and_job_id(url) or _extract_gh_jid_from_query(url))

    async def scrape(self, url: str, ctx: AdapterContext) -> AdapterResult:
        parsed = _extract_board_and_job_id(url)
        if parsed:
            board, job_id = parsed
        else:
            gh_jid = _extract_gh_jid_from_query(url)
            if not gh_jid:
                raise AdapterError(
                    f"Could not extract board and job ID from URL: {url}"
                )
            # For gh_jid-only URLs, try to discover the company via the embed page
            discovered = await self._discover_board(gh_jid, ctx)
            if not discovered:
                raise AdapterError(
                    f"Could not discover Greenhouse board name for job {gh_jid}"
                )
            board = discovered
            job_id = gh_jid

        # Tier 1: Greenhouse Boards API
        logger.info("Greenhouse adapter: trying API for %s/%s", board, job_id)
        data = await ctx.with_timeout(_fetch_job_api(board, job_id), timeout=15)
        if data:
            markdown, metadata = _format_job_as_markdown(data, board, job_id)
            logger.info(
                "Greenhouse adapter: API hit for %s (%d chars)",
                url,
                len(markdown),
            )
            return AdapterResult(
                success=True,
                markdown=markdown,
                metadata=metadata,
                source="greenhouse-api",
                url=url,
            )

        # Tier 2: readability page scrape
        logger.info("Greenhouse adapter: trying readability fallback for %s", url)
        result = await scrape_page(url)
        if result:
            return AdapterResult(
                success=True,
                markdown=result,
                metadata={"source": "greenhouse-readability"},
                url=url,
            )

        raise AdapterError(f"Could not extract job posting for {board}/{job_id}")

    async def _discover_board(self, job_id: str, ctx: AdapterContext) -> str | None:
        """Try to discover the Greenhouse board name from an embed page.

        Fetches ``boards.greenhouse.io/embed/job_app?gh_jid={job_id}``
        and looks for the board name in the page content.
        """
        embed_url = f"https://boards.greenhouse.io/embed/job_app?gh_jid={job_id}"
        result = await scrape_page(embed_url)
        if not result:
            return None

        # Try to extract board name from the page — the embed page
        # often includes a "Careers at {Company}" heading
        match = re.search(r"Careers?\s+(?:at|@)\s+(\w[\w\s&.]+)", result, re.IGNORECASE)
        if match:
            # Convert company name to board slug: lowercase, spaces->hyphens
            company = match.group(1).strip()
            return (
                company.lower().replace(" ", "-").replace("&", "and").replace(".", "")
            )

        return None
