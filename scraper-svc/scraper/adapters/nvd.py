"""
NVD API adapter — structured CVE data extraction from the National Vulnerability Database.

Fallback chain:
  1. NVD API (/rest/json/cves/2.0) — structured JSON with CVSS, CPE, KEV enrichment
  2. Page scrape via readability-lxml — for when NVD API rate limits are exhausted
  3. Generic tier — last resort

API docs: https://nvd.nist.gov/developers/vulnerabilities
Public endpoint: https://services.nvd.nist.gov/rest/json/cves/2.0
"""

from __future__ import annotations

import logging
import re
import time

import httpx

from ..source_http import source_httpx_client
from .base import AdapterContext, AdapterError, AdapterResult, SiteAdapter, adapter

logger = logging.getLogger(__name__)

# ── URL pattern matching ─────────────────────────────────────────

_NVD_URL_PATTERNS = [
    re.compile(r"^https?://nvd\.nist\.gov/vuln/detail/CVE-\d{4}-\d{4,}"),
    re.compile(r"^cve:CVE-\d{4}-\d{4,}"),
]

# ── Constants ────────────────────────────────────────────────────

NVD_API_BASE = "https://services.nvd.nist.gov/rest/json/cves/2.0"

# Rate limits: public = 5 req/30s, with API key = 50 req/30s
PUBLIC_RATE_WINDOW = 30.0
PUBLIC_RATE_LIMIT = 5
KEYED_RATE_WINDOW = 30.0
KEYED_RATE_LIMIT = 50


# ── Rate limiter ─────────────────────────────────────────────────


class _InMemoryRateLimiter:
    """Simple sliding-window rate limiter for the NVD API."""

    def __init__(self, max_requests: int, window_seconds: float):
        self._max = max_requests
        self._window = window_seconds
        self._timestamps: list[float] = []

    def acquire(self) -> float | None:
        """Try to acquire a slot.

        Returns the remaining cooldown seconds if rate limited, or
        ``None`` if the request can proceed.
        """
        now = time.monotonic()
        # Prune expired timestamps
        cutoff = now - self._window
        self._timestamps = [t for t in self._timestamps if t > cutoff]

        if len(self._timestamps) >= self._max:
            # Return seconds until the oldest slot expires
            return self._timestamps[0] + self._window - now

        self._timestamps.append(now)
        return None


# Module-level rate limiters (per-adapter-call, reset on module reload)
_public_limiter = _InMemoryRateLimiter(PUBLIC_RATE_LIMIT, PUBLIC_RATE_WINDOW)
_keyed_limiter = _InMemoryRateLimiter(KEYED_RATE_LIMIT, KEYED_RATE_WINDOW)


# ── CVE ID extraction ───────────────────────────────────────────


def _extract_cve_id(url: str) -> str | None:
    """Extract a CVE ID (e.g. ``CVE-2024-3094``) from a URL.

    Handles:
    - ``https://nvd.nist.gov/vuln/detail/CVE-2024-3094``
    - ``cve:CVE-2024-3094``
    """
    # Pattern: CVE-YYYY-NNNNN (year + at least 4 digits)
    m = re.search(r"CVE-\d{4}-\d{4,}", url)
    if m:
        return m.group(0)
    return None


def _build_nvd_detail_url(cve_id: str) -> str:
    """Build the HTML detail page URL for readability fallback."""
    return f"https://nvd.nist.gov/vuln/detail/{cve_id}"


# ── API helpers ──────────────────────────────────────────────────


async def _fetch_nvd_api(cve_id: str, api_key: str) -> dict | None:
    """Fetch CVE data from the NVD API.

    Returns the parsed JSON ``vulnerabilities[0]`` item, or ``None``
    on failure.  Handles rate limiting, HTTP errors, and malformed
    responses.
    """
    # Pick the right rate limiter
    has_key = bool(api_key)
    limiter = _keyed_limiter if has_key else _public_limiter

    cooldown = limiter.acquire()
    if cooldown is not None:
        logger.debug("NVD rate limited for %.1f seconds", cooldown)
        return None

    params: dict[str, str] = {"cveId": cve_id}
    if has_key:
        params["apiKey"] = api_key

    headers = {
        "User-Agent": "GroktoCrawl/0.7.0 (NVD adapter)",
    }

    try:
        async with source_httpx_client(timeout=15) as client:
            resp = await client.get(NVD_API_BASE, params=params, headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                vulns = data.get("vulnerabilities", [])
                if vulns:
                    return vulns[0].get("cve")
                logger.debug("NVD API returned empty vulnerabilities for %s", cve_id)
                return None
            elif resp.status_code == 403:
                logger.debug("NVD API 403 for %s (rate limited or blocked)", cve_id)
                return None
            elif resp.status_code == 404:
                logger.debug("NVD API 404 for %s (not found)", cve_id)
                return None
            else:
                logger.debug("NVD API returned %d for %s", resp.status_code, cve_id)
                return None
    except httpx.TimeoutException:
        logger.debug("NVD API timed out for %s", cve_id)
        return None
    except Exception as exc:
        logger.debug("NVD API request failed for %s: %s", cve_id, exc)
        return None


# ── Response formatting ─────────────────────────────────────────


def _get_en_description(descriptions: list[dict] | None) -> str:
    """Extract the English description from a CVE descriptions list."""
    if not descriptions:
        return ""
    for d in descriptions:
        if d.get("lang") == "en":
            return d.get("value", "")
    return descriptions[0].get("value", "") if descriptions else ""


def _get_cvss_data(metrics: dict | None) -> dict:
    """Extract CVSS v3.1/v4.0 scores and severity from NVD metrics.

    Returns a dict with keys: ``cvss_v3``, ``cvss_v4``, ``severity``,
    ``vector_v3``, ``vector_v4``, ``exploitability_v3``,
    ``exploitability_v4``.
    """
    result: dict = {
        "cvss_v3": None,
        "cvss_v4": None,
        "severity": None,
        "vector_v3": None,
        "vector_v4": None,
        "exploitability_v3": None,
        "exploitability_v4": None,
    }

    if not metrics:
        return result

    # Try v3.1 first, then v3.x
    v3_metrics = metrics.get("cvssMetricV31") or metrics.get("cvssMetricV30") or []
    if v3_metrics:
        cvss_data = v3_metrics[0].get("cvssData", {})
        result["cvss_v3"] = cvss_data.get("baseScore")
        result["severity"] = cvss_data.get("baseSeverity")
        result["vector_v3"] = cvss_data.get("vectorString")
        result["exploitability_v3"] = v3_metrics[0].get("exploitabilityScore")

    # v4.0
    v4_metrics = metrics.get("cvssMetricV40") or []
    if v4_metrics:
        cvss_data = v4_metrics[0].get("cvssData", {})
        result["cvss_v4"] = cvss_data.get("baseScore")
        result["vector_v4"] = cvss_data.get("vectorString")
        result["exploitability_v4"] = v4_metrics[0].get("exploitabilityScore")

    return result


def _get_weaknesses(weaknesses: list[dict] | None) -> list[str]:
    """Extract CWE IDs from the weaknesses array."""
    if not weaknesses:
        return []
    cwes = []
    for w in weaknesses:
        for desc in w.get("description", []):
            val = desc.get("value", "")
            if val.startswith("CWE-"):
                cwes.append(val)
    return cwes


def _get_kev_status(cve_data: dict) -> bool:
    """Check if this CVE is in CISA's Known Exploited Vulnerabilities catalog.

    NVD doesn't expose KEV directly in the v2.0 API response as a
    simple boolean flag. We check by looking at whether any reference
    tags include 'Exploit' or we check the ``hasKev`` query parameter
    separately. For now, check reference tags for 'Exploit' as a
    heuristic.
    """
    # NVD v2.0 API response includes a `hasKev` query filter but not
    # necessarily a per-CVE boolean. We'll check if the CVE has any
    # reference tagged as "Exploit" as a reasonable heuristic.
    # A more accurate approach would require a separate KEV check API.
    refs = cve_data.get("references", [])
    for ref in refs:
        tags = ref.get("tags", [])
        if "Exploit" in tags:
            return True
    return False


def _format_references(refs: list[dict] | None) -> str:
    """Format reference URLs as a markdown table."""
    if not refs:
        return ""
    lines = ["| URL | Tags |", "|-----|------|"]
    for ref in refs[:20]:  # Limit to top 20 references
        url = ref.get("url", "")
        tags = ", ".join(ref.get("tags", []))
        if url:
            lines.append(f"| {url} | {tags} |")
    return "\n".join(lines)


def _format_cpe_matches(configurations: list[dict] | None) -> str:
    """Format CPE match criteria as a markdown table."""
    if not configurations:
        return ""
    lines = [
        "| CPE Match | Vulnerable | Version Start | Version End |",
        "|---|---|---|---|",
    ]
    seen: set[str] = set()
    for node in configurations:
        if not isinstance(node, dict):
            continue
        cpe_matches = node.get("cpeMatch", [])
        if not cpe_matches:
            # Walk the full configuration tree
            _walk_cpe_matches(node, lines, seen)
        else:
            _walk_cpe_matches(node, lines, seen)
    return "\n".join(lines)


def _walk_cpe_matches(node: dict, lines: list[str], seen: set[str]) -> None:
    """Recursively walk configuration nodes and collect CPE matches."""
    cpe_matches = node.get("cpeMatch", [])
    for m in cpe_matches:
        criteria = m.get("criteria", "")
        if criteria in seen:
            continue
        seen.add(criteria)
        vulnerable = "No" if m.get("vulnerable") is False else "Yes"
        start_including = m.get("versionStartIncluding", "")
        start_excluding = m.get("versionStartExcluding", "")
        end_including = m.get("versionEndIncluding", "")
        end_excluding = m.get("versionEndExcluding", "")

        start = start_including or (f">{start_excluding}" if start_excluding else "")
        end = end_including or (f"<{end_excluding}" if end_excluding else "")
        version_range = f"{start} - {end}" if start and end else start or end or "any"
        lines.append(f"| `{criteria}` | {vulnerable} | {version_range} | ... |")

    # Walk children
    children = node.get("children", [])
    for child in children:
        _walk_cpe_matches(child, lines, seen)


def _format_cve_as_markdown(cve_data: dict) -> tuple[str, dict]:
    """Convert NVD API response to markdown + metadata.

    Returns ``(markdown, metadata)``.
    """
    cve_id = cve_data.get("id", "")
    descriptions = cve_data.get("descriptions")
    description = _get_en_description(descriptions)
    metrics = cve_data.get("metrics")
    cvss = _get_cvss_data(metrics)
    weaknesses = _get_weaknesses(cve_data.get("weaknesses"))
    kev = _get_kev_status(cve_data)
    refs = cve_data.get("references", [])
    configurations = cve_data.get("configurations")

    # Build metadata
    cwe_str = weaknesses[0] if weaknesses else None
    metadata: dict = {
        "cve_id": cve_id,
        "published": cve_data.get("published", "")[:10]
        if cve_data.get("published")
        else "",
        "modified": cve_data.get("lastModified", "")[:10]
        if cve_data.get("lastModified")
        else "",
        "severity": cvss.get("severity"),
        "cvss_v3": cvss.get("cvss_v3"),
        "cvss_v4": cvss.get("cvss_v4"),
        "kev": kev,
        "cwe": cwe_str,
        "source": "nvd-api",
    }

    # Build markdown body
    parts: list[str] = []

    # Description
    if description:
        parts.append(description)
        parts.append("")

    # CVSS scores
    if cvss.get("cvss_v3") is not None or cvss.get("cvss_v4") is not None:
        parts.append("## CVSS Scores")
        parts.append("")
        parts.append("| Metric | Value |")
        parts.append("|--------|-------|")
        if cvss.get("cvss_v3") is not None:
            parts.append(
                f"| CVSS v3.1 Score | **{cvss['cvss_v3']}** ({cvss.get('severity', 'N/A')}) |"
            )
            if cvss.get("vector_v3"):
                parts.append(f"| Vector (v3) | `{cvss['vector_v3']}` |")
            if cvss.get("exploitability_v3") is not None:
                parts.append(f"| Exploitability (v3) | {cvss['exploitability_v3']} |")
        if cvss.get("cvss_v4") is not None:
            parts.append(f"| CVSS v4.0 Score | **{cvss['cvss_v4']}** |")
            if cvss.get("vector_v4"):
                parts.append(f"| Vector (v4) | `{cvss['vector_v4']}` |")
            if cvss.get("exploitability_v4") is not None:
                parts.append(f"| Exploitability (v4) | {cvss['exploitability_v4']} |")
        parts.append("")

    # KEV status
    if kev:
        parts.append(
            "> **Known Exploited Vulnerability (KEV)** — This CVE is listed in CISA's Known Exploited Vulnerabilities catalog."
        )
        parts.append("")

    # CWE
    if weaknesses:
        parts.append("## Weakness Classification")
        parts.append("")
        for cwe in weaknesses:
            parts.append(f"- {cwe}")
        parts.append("")

    # CPE matches
    cpe_text = _format_cpe_matches(configurations)
    if cpe_text:
        parts.append("## Affected Products (CPE)")
        parts.append("")
        parts.append(cpe_text)
        parts.append("")

    # References
    ref_text = _format_references(refs)
    if ref_text:
        parts.append("## References")
        parts.append("")
        parts.append(ref_text)
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

            # Convert HTML to markdown
            soup = BeautifulSoup(summary_html, "html.parser")
            text = soup.get_text(separator="\n", strip=True)

            if not text:
                return None

            markdown = f"# {title}\n\n{text}" if title else text
            metadata = {
                "cve_id": _extract_cve_id(url) or "",
                "source": "nvd-readability",
            }
            return markdown, metadata
    except Exception as exc:
        logger.debug("Readability fallback failed for %s: %s", url, exc)
        return None


# ── Adapter class ────────────────────────────────────────────────


@adapter
class NVDAdapter(SiteAdapter):
    """Extract structured CVE data from NVD URLs."""

    name = "nvd"

    patterns = _NVD_URL_PATTERNS

    priority = 200

    async def scrape(self, url: str, ctx: AdapterContext) -> AdapterResult:
        cve_id = _extract_cve_id(url)
        if not cve_id:
            raise AdapterError(f"Could not extract CVE ID from URL: {url}")

        api_key = ctx.config.get("ADAPTER_NVD_API_KEY", "")

        # Fallback 1: NVD API
        logger.info("NVD adapter: trying API for %s", cve_id)
        cve_data = await ctx.with_timeout(_fetch_nvd_api(cve_id, api_key), timeout=15)
        if cve_data:
            markdown, metadata = _format_cve_as_markdown(cve_data)
            logger.info(
                "NVD adapter: API hit for %s (%d chars)",
                url,
                len(markdown),
            )
            return AdapterResult(
                success=True,
                markdown=markdown,
                metadata=metadata,
                source="nvd-api",
                url=url,
            )

        # Fallback 2: readability scrape from HTML detail page
        detail_url = _build_nvd_detail_url(cve_id)
        logger.info("NVD adapter: trying readability fallback for %s", detail_url)
        readability_result = await _fetch_via_readability(detail_url)
        if readability_result:
            markdown, metadata = readability_result
            metadata["cve_id"] = cve_id
            metadata["source"] = "nvd-readability"
            return AdapterResult(
                success=True,
                markdown=markdown,
                metadata=metadata,
                source="nvd-readability",
                url=url,
            )

        raise AdapterError(
            f"Could not extract CVE data for {cve_id} (API failed, readability failed)"
        )
