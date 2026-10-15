"""
CVE Program API adapter — structured CVE data extraction from the authoritative MITRE CVE API.

Fallback chain:
  1. CVE Services API — authoritative JSON 5.2 CVE Record Format (public read)
  2. Page scrape via readability-lxml — for cve.org or cve.mitre.org HTML pages
  3. Generic tier — last resort

API docs: https://cveproject.github.io/cve-services/api-docs/
Public endpoint: https://cveawg.mitre.org/api/cve/{cveId}
"""

from __future__ import annotations

import logging
import re

import httpx

from ..source_http import source_httpx_client
from .base import AdapterContext, AdapterError, AdapterResult, SiteAdapter, adapter

logger = logging.getLogger(__name__)

# ── URL pattern matching ─────────────────────────────────────────

_CVEORG_URL_PATTERNS = [
    re.compile(r"^https?://cve\.org/CVERecord\?id=CVE-\d{4}-\d{4,}"),
    re.compile(
        r"^https?://cve\.mitre\.org/cgi-bin/cvename\.cgi\?name=CVE-\d{4}-\d{4,}"
    ),
    re.compile(r"^cve:CVE-\d{4}-\d{4,}"),
]

# ── Constants ────────────────────────────────────────────────────

CVE_API_BASE = "https://cveawg.mitre.org/api/cve"
CVE_API_ORG = "MITRE"

# ── CVE ID extraction ───────────────────────────────────────────


def _extract_cve_id(url: str) -> str | None:
    """Extract a CVE ID (e.g. ``CVE-2024-3094``) from a URL.

    Handles:
    - ``https://cve.org/CVERecord?id=CVE-2024-3094``
    - ``https://cve.mitre.org/cgi-bin/cvename.cgi?name=CVE-2024-3094``
    - ``cve:CVE-2024-3094``
    """
    m = re.search(r"CVE-\d{4}-\d{4,}", url)
    if m:
        return m.group(0)
    return None


def _get_source_page_url(cve_id: str) -> str:
    """Build the cve.org page URL for readability fallback."""
    return f"https://cve.org/CVERecord?id={cve_id}"


# ── API helpers ──────────────────────────────────────────────────


async def _fetch_cve_api(cve_id: str) -> dict | None:
    """Fetch the authoritative CVE Record from the CVE Program API.

    Returns the parsed JSON response, or ``None`` on failure.
    Public endpoint — no auth required.
    """
    url = f"{CVE_API_BASE}/{cve_id}"
    headers = {
        "CVE-API-ORG": CVE_API_ORG,
        "User-Agent": "GroktoCrawl/0.7.0 (CVE Program adapter)",
    }

    try:
        async with source_httpx_client(timeout=15) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 200:
                return resp.json()
            elif resp.status_code == 404:
                logger.debug("CVE API 404 for %s (not found)", cve_id)
                return None
            elif resp.status_code == 429:
                logger.debug("CVE API 429 for %s (rate limited)", cve_id)
                return None
            else:
                logger.debug("CVE API returned %d for %s", resp.status_code, cve_id)
                return None
    except httpx.TimeoutException:
        logger.debug("CVE API timed out for %s", cve_id)
        return None
    except Exception as exc:
        logger.debug("CVE API request failed for %s: %s", cve_id, exc)
        return None


# ── Response formatting ─────────────────────────────────────────


def _get_assigner_shortname(cve_metadata: dict) -> str:
    """Extract the assigner short name from CVE metadata."""
    return cve_metadata.get("assignerShortName", cve_metadata.get("assignerOrgId", ""))


def _get_container_field(record: dict, field: str, default=None):
    """Get a field from the CNA container, or the ADP container as fallback."""
    containers = record.get("containers", {})
    cna = containers.get("cna", {})
    if field in cna:
        return cna.get(field)
    # Fallback to ADP container
    for adp in containers.get("adp", []):
        if field in adp:
            return adp.get(field)
    return default


def _get_en_description(record: dict) -> str:
    """Extract the English description from the CVE Record."""
    descriptions = _get_container_field(record, "descriptions", [])
    for d in descriptions:
        if d.get("lang") == "en":
            return d.get("value", "")
    # Fallback to any description
    for d in descriptions:
        val = d.get("value", "")
        if val:
            return val
    return ""


def _format_affected(record: dict) -> str:
    """Format affected product entries as a markdown table."""
    affected = _get_container_field(record, "affected", [])
    if not affected:
        return ""

    lines = ["| Product | Vendor | Versions |", "|---------|--------|----------|"]
    for item in affected:
        product = item.get("product", "")
        vendor = item.get("vendor", "")
        # Collect version ranges
        versions = item.get("versions", [])
        version_strs = []
        for v in versions:
            version = v.get("version", "")
            status = v.get("status", "")
            less_than = v.get("lessThan", "")
            less_than_or_equal = v.get("lessThanOrEqual", "")
            if less_than:
                version_strs.append(f"{version} (< {less_than})")
            elif less_than_or_equal:
                version_strs.append(f"{version} (<= {less_than_or_equal})")
            elif version:
                version_strs.append(f"{version} ({status})" if status else version)
        version_text = "; ".join(version_strs) if version_strs else "all"
        lines.append(f"| {product} | {vendor} | {version_text} |")

    return "\n".join(lines)


def _format_references(refs: list[dict] | None) -> str:
    """Format reference URLs as a markdown list."""
    if not refs:
        return ""
    lines = ["## References", ""]
    for ref in refs[:20]:
        url = ref.get("url", "")
        tags = ref.get("tags", [])
        tag_str = f" ({', '.join(tags)})" if tags else ""
        if url:
            lines.append(f"- {url}{tag_str}")
        elif ref.get("description"):
            lines.append(f"- {ref.get('description')}")
    return "\n".join(lines)


def _format_credits(credits: list[dict] | None) -> str:
    """Format credit/acknowledgement information."""
    if not credits:
        return ""
    lines = ["## Credits", ""]
    for c in credits:
        name = c.get("name", "Unknown")
        types = c.get("type", [])
        type_str = f" ({', '.join(types)})" if types else ""
        lines.append(f"- {name}{type_str}")
    return "\n".join(lines)


def _format_record_as_markdown(record: dict) -> tuple[str, dict]:
    """Convert CVE Record API response to markdown + metadata.

    Returns ``(markdown, metadata)``.
    """
    cve_metadata = record.get("cveMetadata", {})
    cve_id = cve_metadata.get("cveId", "")
    state = cve_metadata.get("state", "")
    assigner = _get_assigner_shortname(cve_metadata)
    date_published = (cve_metadata.get("datePublished") or "")[:10]
    title = _get_container_field(record, "title", "")

    # Build metadata
    metadata: dict = {
        "cve_id": cve_id,
        "state": state,
        "assigner": assigner,
        "published": date_published,
        "title": title,
        "source": "cve-api",
    }

    # Build markdown body
    parts: list[str] = []

    # Title as heading
    if title:
        parts.append(f"# {title}")
        parts.append("")
        parts.append(f"**CVE ID:** {cve_id}  ")
        parts.append(f"**State:** {state}  ")
        parts.append(f"**Assigner:** {assigner}  ")
        if date_published:
            parts.append(f"**Published:** {date_published}")
        parts.append("")

    # Description
    description = _get_en_description(record)
    if description:
        if not title:
            parts.append(f"# {cve_id}")
            parts.append("")
        parts.append(description)
        parts.append("")

    # Affected products
    affected_text = _format_affected(record)
    if affected_text:
        parts.append("## Affected Products")
        parts.append("")
        parts.append(affected_text)
        parts.append("")

    # Credits
    credits = _get_container_field(record, "credits")
    if credits:
        parts.append(_format_credits(credits))
        parts.append("")

    # References
    refs = _get_container_field(record, "references")
    if refs:
        parts.append(_format_references(refs))
        parts.append("")

    markdown = "\n".join(parts).strip()
    return markdown, metadata


# ── Readability fallback ─────────────────────────────────────────


async def _fetch_via_readability(url: str) -> tuple[str, dict] | None:
    """Fallback: fetch HTML and extract content with readability-lxml.

    Returns ``(markdown, metadata)`` or ``None``.
    """
    try:
        async with source_httpx_client(
            timeout=15,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (compatible; GroktoCrawl/0.7.0)"},
        ) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                return None

            from bs4 import BeautifulSoup
            from readability import Document

            html = resp.text
            doc = Document(html)
            title = doc.title()
            summary_html = doc.summary()

            soup = BeautifulSoup(summary_html, "html.parser")
            text = soup.get_text(separator="\n", strip=True)

            if not text:
                return None

            markdown = f"# {title}\n\n{text}" if title else text
            metadata = {
                "cve_id": _extract_cve_id(url) or "",
                "source": "cve-readability",
            }
            return markdown, metadata
    except Exception as exc:
        logger.debug("Readability fallback failed for %s: %s", url, exc)
        return None


# ── Adapter class ────────────────────────────────────────────────


@adapter
class CVEOrgAdapter(SiteAdapter):
    """Extract structured CVE data from cve.org and cve.mitre.org URLs."""

    name = "cveorg"

    patterns = _CVEORG_URL_PATTERNS

    priority = 150

    async def scrape(self, url: str, ctx: AdapterContext) -> AdapterResult:
        cve_id = _extract_cve_id(url)
        if not cve_id:
            raise AdapterError(f"Could not extract CVE ID from URL: {url}")

        # Fallback 1: CVE Program API
        logger.info("CVE org adapter: trying API for %s", cve_id)
        record = await ctx.with_timeout(_fetch_cve_api(cve_id), timeout=15)
        if record:
            markdown, metadata = _format_record_as_markdown(record)
            logger.info(
                "CVE org adapter: API hit for %s (%d chars)",
                url,
                len(markdown),
            )
            return AdapterResult(
                success=True,
                markdown=markdown,
                metadata=metadata,
                source="cve-api",
                url=url,
            )

        # Fallback 2: readability scrape from cve.org page
        page_url = _get_source_page_url(cve_id)
        logger.info("CVE org adapter: trying readability fallback for %s", page_url)
        readability_result = await _fetch_via_readability(page_url)
        if readability_result:
            markdown, metadata = readability_result
            metadata["cve_id"] = cve_id
            metadata["source"] = "cve-readability"
            return AdapterResult(
                success=True,
                markdown=markdown,
                metadata=metadata,
                source="cve-readability",
                url=url,
            )

        raise AdapterError(
            f"Could not extract CVE data for {cve_id} (API failed, readability failed)"
        )
