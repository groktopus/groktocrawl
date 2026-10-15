"""
Vaco (Highspring) adapter — extracts job postings from jobs.vaco.com.

Vaco embeds full schema.org JobPosting LD+JSON in the server-rendered HTML.
No API calls are needed.

Fallback chain:
  1. LD+JSON extraction from <script type="application/ld+json"> blocks
  2. Page scrape via readability-lxml
  3. AdapterError — falls through to the generic scrape pipeline
"""

from __future__ import annotations

import json
import logging
import re

import httpx

from ..source_http import source_httpx_client
from ._helpers import scrape_page
from .base import AdapterContext, AdapterError, AdapterResult, SiteAdapter, adapter

logger = logging.getLogger(__name__)

# ── URL pattern matching ─────────────────────────────────────────

_VACO_URL_PATTERNS = [
    # jobs.vaco.com/job/{id}
    re.compile(r"^https?://jobs\.vaco\.com/job/\d+"),
]


# ── Data extraction ──────────────────────────────────────────────


async def _fetch_html(url: str) -> str | None:
    """Fetch HTML content from a jobs.vaco.com URL.

    Uses a browser-like User-Agent to ensure SSR content is returned.
    """
    try:
        async with source_httpx_client(
            timeout=15,
            follow_redirects=True,
        ) as client:
            resp = await client.get(
                url,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (compatible; GroktoCrawl/0.7.0; Vaco adapter)"
                    ),
                },
            )
            if resp.status_code == 200:
                return resp.text
            logger.debug("Vaco fetch returned %d for %s", resp.status_code, url)
            return None
    except httpx.TimeoutException:
        logger.debug("Vaco fetch timed out for %s", url)
        return None
    except httpx.RequestError as exc:
        logger.debug("Vaco fetch failed for %s: %s", url, exc)
        return None


def _extract_ldjson(html: str) -> dict | None:
    """Extract the schema.org JobPosting LD+JSON block from Vaco HTML.

    Searches for ``<script type="application/ld+json">`` blocks,
    finds the one with ``@type`` == ``"JobPosting"``, and returns it.
    Returns ``None`` if no JobPosting block is found.
    """
    pattern = re.compile(
        r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        re.DOTALL | re.IGNORECASE,
    )
    for match in pattern.finditer(html):
        raw = match.group(1).strip()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue

        # Handle both direct object and @graph wrappers
        candidates: list[dict] = []
        if isinstance(data, dict):
            graph = data.get("@graph")
            if isinstance(graph, list):
                candidates.extend(item for item in graph if isinstance(item, dict))
            else:
                candidates.append(data)
        elif isinstance(data, list):
            candidates.extend(data)

        for candidate in candidates:
            if candidate.get("@type") == "JobPosting":
                return candidate

    return None


async def _fetch_and_parse_ldjson(url: str) -> dict | None:
    """Fetch a Vaco URL and extract the JobPosting LD+JSON.

    Returns the parsed JobPosting dict, or ``None`` on failure.
    """
    html = await _fetch_html(url)
    if not html:
        return None
    return _extract_ldjson(html)


# ── HTML → markdown conversion ───────────────────────────────────


def _html_to_markdown(html: str) -> str:
    """Convert HTML job description to clean markdown.

    Uses readability-lxml + markdownify (standard deps of scraper-svc).
    Falls back to BeautifulSoup text extraction.
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


def _normalize_employment_type(et: str) -> str:
    """Normalize employment type values to a readable form."""
    mapping = {
        "direct hire": "Direct Hire",
        "contract": "Contract",
        "contract-to-hire": "Contract-to-Hire",
        "full-time": "Full-Time",
        "part-time": "Part-Time",
        "temporary": "Temporary",
    }
    key = et.strip().lower()
    return mapping.get(key, et.strip())


# ── Response formatting ──────────────────────────────────────────


def _format_job(ld_json: dict, url: str) -> tuple[str, dict]:
    """Convert a Vaco JobPosting LD+JSON dict to markdown + metadata.

    Returns ``(markdown, metadata)``.
    """
    title = ld_json.get("title", "")

    # Company
    org = ld_json.get("hiringOrganization", {})
    if isinstance(org, dict):
        company = org.get("name", "")
    else:
        company = str(org) if org else ""

    # Employment type
    employment_type_raw = ld_json.get("employmentType", "")
    if isinstance(employment_type_raw, list):
        employment_type = ", ".join(
            _normalize_employment_type(et) for et in employment_type_raw if et
        )
    elif employment_type_raw:
        employment_type = _normalize_employment_type(employment_type_raw)
    else:
        employment_type = ""

    # Salary
    salary_min = ""
    salary_max = ""
    salary_data = ld_json.get("baseSalary", {})
    if isinstance(salary_data, dict):
        value = salary_data.get("value", {})
        if isinstance(value, dict):
            salary_min = value.get("minValue", "")
            salary_max = value.get("maxValue", "")
        elif isinstance(value, (int, float)):
            salary_min = str(value)

    # Date posted
    date_posted = (ld_json.get("datePosted") or "")[:10]

    # Industry
    industry_raw = ld_json.get("industry", [])
    if isinstance(industry_raw, list):
        industry = ", ".join(filter(None, industry_raw))
    else:
        industry = str(industry_raw) if industry_raw else ""

    # Location
    location = ""
    region = ""
    job_locations = ld_json.get("jobLocation", [])
    if isinstance(job_locations, list) and job_locations:
        loc = job_locations[0]
        if isinstance(loc, dict):
            address = loc.get("address", {})
            if isinstance(address, dict):
                location = address.get("addressLocality", "")
                region = address.get("addressRegion", "")
    elif isinstance(job_locations, dict):
        address = job_locations.get("address", {})
        if isinstance(address, dict):
            location = address.get("addressLocality", "")
            region = address.get("addressRegion", "")

    # Build location string for the details table
    location_str = location
    if region:
        location_str = f"{location}, {region}" if location else region

    # Build metadata
    metadata: dict = {
        "title": title,
        "company": company,
        "employment_type": employment_type,
        "location": location,
        "region": region,
        "date_posted": date_posted,
    }
    if salary_min:
        metadata["salary_min"] = salary_min
    if salary_max:
        metadata["salary_max"] = salary_max
    if industry:
        metadata["industry"] = industry

    # Build markdown
    parts: list[str] = []
    parts.append(f"# {title}")
    parts.append("")

    # Key details table
    detail_items: list[tuple[str, str]] = [
        ("Company", company),
        ("Employment Type", employment_type),
        ("Location", location_str),
        ("Posted", date_posted),
    ]
    if salary_min and salary_max:
        detail_items.append(("Salary Range", f"${salary_min} — ${salary_max}"))
    elif salary_min:
        detail_items.append(("Salary", f"${salary_min}"))
    if industry:
        detail_items.append(("Industry", industry))

    parts.append("| Field | Value |")
    parts.append("|-------|-------|")
    for label, val in detail_items:
        if val:
            parts.append(f"| **{label}** | {val} |")
    parts.append("")

    # Description
    description_html = ld_json.get("description", "")
    if description_html:
        desc_md = _html_to_markdown(description_html)
        if desc_md:
            parts.append("## Description")
            parts.append("")
            parts.append(desc_md)
        else:
            parts.append("*No description available*")
    else:
        parts.append("*No description available*")

    parts.append("")
    parts.append(f"*Source: [Vaco]({url})*")

    markdown = "\n".join(parts).strip()
    return markdown, metadata


# ── Adapter class ────────────────────────────────────────────────


@adapter
class VacoAdapter(SiteAdapter):
    """Extract job postings from Vaco (jobs.vaco.com) career pages."""

    name = "vaco"

    patterns = _VACO_URL_PATTERNS

    priority = 200

    async def scrape(self, url: str, ctx: AdapterContext) -> AdapterResult:
        # Tier 1: LD+JSON extraction from SSR HTML
        logger.info("Vaco adapter: trying LD+JSON extraction for %s", url)
        ld_json = await ctx.with_timeout(_fetch_and_parse_ldjson(url), timeout=15)

        if ld_json:
            markdown, metadata = _format_job(ld_json, url)
            logger.info(
                "Vaco adapter: extracted job posting (%d chars)",
                len(markdown),
            )
            return AdapterResult(
                success=True,
                markdown=markdown,
                metadata=metadata,
                source="vaco-ldjson",
                url=url,
            )

        # Tier 2: readability page scrape
        logger.info("Vaco adapter: trying readability fallback for %s", url)
        result = await scrape_page(url)
        if result:
            return AdapterResult(
                success=True,
                markdown=result,
                metadata={"source": "vaco-readability"},
                url=url,
            )

        raise AdapterError(f"Could not extract content from Vaco URL: {url}")
