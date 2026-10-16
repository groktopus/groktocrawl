"""
VirusTotal adapter — file hash, URL, domain, and IP reputation lookup.

API docs: https://developers.virustotal.com/reference
"""

from __future__ import annotations

import logging
import re

from ..source_http import source_httpx_client
from ._helpers import scrape_page
from .base import AdapterContext, AdapterError, AdapterResult, SiteAdapter, adapter

logger = logging.getLogger(__name__)

_VT_URL_PATTERNS = [
    re.compile(r"^https?://(?:www\.)?virustotal\.com/gui/file/[a-fA-F0-9]{64}"),
    re.compile(r"^https?://(?:www\.)?virustotal\.com/gui/url/"),
    re.compile(r"^https?://(?:www\.)?virustotal\.com/gui/domain/"),
    re.compile(r"^https?://(?:www\.)?virustotal\.com/gui/ip-address/"),
]

_HASH_PATTERN = re.compile(r"/file/([a-fA-F0-9]{64})")


def _extract_hash(url: str) -> str | None:
    m = _HASH_PATTERN.search(url)
    return m.group(1) if m else None


async def _fetch_api(resource_id: str, api_key: str) -> dict | None:
    if not api_key:
        return None
    try:
        async with source_httpx_client(timeout=10) as client:
            resp = await client.get(
                f"https://www.virustotal.com/api/v3/files/{resource_id}",
                headers={"x-apikey": api_key, "Accept": "application/json"},
            )
            if resp.status_code == 200:
                return resp.json().get("data", {}).get("attributes", {})
    except Exception as exc:
        logger.debug("VT API failed for %s: %s", resource_id, exc)
    return None


def _format_api_result(attrs: dict, resource_id: str) -> tuple[str, dict]:
    stats = attrs.get("last_analysis_stats", {})
    malicious = stats.get("malicious", 0)
    suspicious = stats.get("suspicious", 0)
    harmless = stats.get("harmless", 0)
    undetected = stats.get("undetected", 0)
    total = malicious + suspicious + harmless + undetected
    meaningful_name = attrs.get("meaningful_name", "")
    type_desc = attrs.get("type_description", "")
    magic = attrs.get("magic", "")
    times_submitted = attrs.get("times_submitted", 0)
    last_submission = attrs.get("last_submission_date") or ""
    if last_submission:
        import datetime
        from contextlib import suppress

        with suppress(ValueError, OSError):
            last_submission = datetime.datetime.fromtimestamp(
                int(last_submission)
            ).strftime("%Y-%m-%d")

    metadata = {
        "resource": resource_id[:16] + "...",
        "malicious": malicious,
        "total_scanners": total,
        "type": type_desc,
        "meaningful_name": meaningful_name or resource_id[:32],
        "source": "virustotal-api",
    }

    parts = [f"## VirusTotal — {meaningful_name or resource_id[:32]}", ""]
    parts.append("| Metric | Value |")
    parts.append("|--------|-------|")
    if total > 0:
        parts.append(
            f"| Detection | **{malicious}/{total}** ({suspicious} suspicious) |"
        )
    parts.append(f"| Type | {type_desc or 'Unknown'} |")
    if magic:
        parts.append(f"| Magic | {magic} |")
    if times_submitted:
        parts.append(f"| Times Submitted | {times_submitted} |")
    if last_submission:
        parts.append(f"| Last Submission | {last_submission} |")
    parts.append("")
    parts.append(f"Full report: https://virustotal.com/gui/file/{resource_id}")

    return "\n".join(parts), metadata


@adapter
class VirusTotalAdapter(SiteAdapter):
    name = "virustotal"
    patterns = _VT_URL_PATTERNS
    priority = 180

    async def scrape(self, url: str, ctx: AdapterContext) -> AdapterResult:
        resource_hash = _extract_hash(url)
        if not resource_hash:
            raise AdapterError(f"Could not extract resource hash from URL: {url}")

        api_key = ctx.config.get("ADAPTER_VIRUSTOTAL_API_KEY", "")

        if api_key:
            attrs = await ctx.with_timeout(
                _fetch_api(resource_hash, api_key), timeout=10
            )
            if attrs:
                md, meta = _format_api_result(attrs, resource_hash)
                return AdapterResult(
                    success=True,
                    markdown=md,
                    metadata=meta,
                    source="virustotal-api",
                    url=url,
                )

        result = await scrape_page(url)
        if result:
            return AdapterResult(
                success=True,
                markdown=result,
                metadata={"hash": resource_hash, "source": "virustotal-html"},
                url=url,
            )

        raise AdapterError("Could not extract VirusTotal data")
