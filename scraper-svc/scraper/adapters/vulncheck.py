"""
VulnCheck Community adapter — vulnerability advisory lookup.

API docs: https://docs.vulncheck.com/
"""

from __future__ import annotations

import logging
import re

from ..source_http import source_httpx_client
from ._helpers import scrape_page
from .base import AdapterContext, AdapterError, AdapterResult, SiteAdapter, adapter

logger = logging.getLogger(__name__)

_VULNCHECK_URL_PATTERNS = [
    re.compile(r"^https?://(?:www\.)?vulncheck\.com/advisories/[^/]+"),
    re.compile(r"^https?://(?:www\.)?vulncheck\.com/cve/[^/]+"),
]

_CVE_PATTERN = re.compile(r"CVE-\d{4}-\d{4,}", re.IGNORECASE)
_ADVISORY_ID = re.compile(r"/advisories/([^/?#]+)")


def _extract_advisory_id(url: str) -> str | None:
    m = _ADVISORY_ID.search(url)
    if m:
        return m.group(1)
    return _extract_cve(url)


def _extract_cve(url: str) -> str | None:
    m = _CVE_PATTERN.search(url)
    return m.group(0).upper() if m else None


async def _fetch_api(cve_id: str, api_key: str) -> list[dict] | None:
    if not api_key:
        return None
    try:
        async with source_httpx_client(timeout=10) as client:
            resp = await client.get(
                f"https://api.vulncheck.com/v3/community/{cve_id}",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Accept": "application/json",
                },
            )
            if resp.status_code == 200:
                return resp.json().get("data", [])
    except Exception as exc:
        logger.debug("VulnCheck API failed for %s: %s", cve_id, exc)
    return None


def _format_api_result(data: list[dict], cve_id: str) -> tuple[str, dict]:
    metadata = {
        "cve_id": cve_id,
        "advisory_count": len(data),
        "source": "vulncheck-api",
    }

    parts = [f"## VulnCheck — {cve_id}", ""]

    if not data:
        parts.append("No advisories found.")
        return "\n".join(parts), metadata

    for advisory in data[:5]:
        title = advisory.get("title", "")
        description = advisory.get("description", "")
        cvss_score = advisory.get("cvss_score") or advisory.get("cvss_base_score")
        severity = advisory.get("severity", "")
        published = (advisory.get("date_public") or advisory.get("published") or "")[
            :10
        ]
        references = advisory.get("references", [])

        parts.append(f"### {title or cve_id}")
        if published:
            parts.append(f"**Published:** {published}  ")
        if cvss_score is not None:
            sev = f" ({severity})" if severity else ""
            parts.append(f"**CVSS:** {cvss_score}{sev}  ")
        parts.append("")
        if description:
            parts.append(description[:500])
            parts.append("")
        if references:
            parts.append("References:")
            for ref in references[:5]:
                url = ref.get("url", ref) if isinstance(ref, dict) else ref
                parts.append(f"- {url}")
            parts.append("")

    return "\n".join(parts), metadata


@adapter
class VulnCheckAdapter(SiteAdapter):
    name = "vulncheck"
    patterns = _VULNCHECK_URL_PATTERNS
    priority = 180

    async def scrape(self, url: str, ctx: AdapterContext) -> AdapterResult:
        cve_id = _extract_cve(url)
        advisory_id = _extract_advisory_id(url)

        api_key = ctx.config.get("ADAPTER_VULNCHECK_API_KEY", "")

        if cve_id and api_key:
            data = await ctx.with_timeout(_fetch_api(cve_id, api_key), timeout=10)
            if data is not None:
                md, meta = _format_api_result(data, cve_id)
                return AdapterResult(
                    success=True,
                    markdown=md,
                    metadata=meta,
                    source="vulncheck-api",
                    url=url,
                )

        result = await scrape_page(url)
        if result:
            return AdapterResult(
                success=True,
                markdown=result,
                metadata={
                    "advisory_id": advisory_id or cve_id or "",
                    "source": "vulncheck-html",
                },
                url=url,
            )

        raise AdapterError(
            f"Could not extract advisory data for {advisory_id or cve_id or url}"
        )
