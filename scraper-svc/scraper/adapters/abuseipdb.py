"""
AbuseIPDB adapter — IP address reputation and abuse reports.

Fallback chain:
  1. AbuseIPDB API (/api/v2/check) — structured JSON with abuse confidence
  2. Page scrape via readability-lxml

API docs: https://docs.abuseipdb.com/
"""

from __future__ import annotations

import logging
import re

from ..source_http import source_httpx_client
from ._helpers import scrape_page
from .base import AdapterContext, AdapterError, AdapterResult, SiteAdapter, adapter

logger = logging.getLogger(__name__)

_ABUSEIPDB_URL_PATTERNS = [
    re.compile(
        r"^https?://(?:www\.)?abuseipdb\.com/check/\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}"
    ),
]

_IP_PATTERN = re.compile(r"\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\b")


def _extract_ip(url: str) -> str | None:
    m = _IP_PATTERN.search(url)
    return m.group(1) if m else None


async def _fetch_api(ip: str, api_key: str) -> dict | None:
    if not api_key:
        return None
    try:
        async with source_httpx_client(timeout=10) as client:
            resp = await client.get(
                "https://api.abuseipdb.com/api/v2/check",
                params={"ipAddress": ip, "maxAgeInDays": 90, "verbose": True},
                headers={"Key": api_key, "Accept": "application/json"},
            )
            if resp.status_code == 200:
                return resp.json().get("data")
    except Exception as exc:
        logger.debug("AbuseIPDB API failed for %s: %s", ip, exc)
    return None


def _format_api_result(data: dict) -> tuple[str, dict]:
    ip = data.get("ipAddress", "")
    confidence = data.get("abuseConfidenceScore", 0)
    domain = data.get("domain", "")
    hostnames = data.get("hostnames", [])
    country = data.get("countryCode", "")
    isp = data.get("isp", "")
    usage = data.get("usageType", "")
    total_reports = data.get("totalReports", 0)
    last_reported = (data.get("lastReportedAt") or "")[:10]

    metadata = {
        "ip": ip,
        "abuse_confidence": confidence,
        "total_reports": total_reports,
        "isp": isp,
        "country": country,
        "domain": domain,
        "source": "abuseipdb-api",
    }

    parts = [f"## AbuseIPDB Report — {ip}", ""]
    parts.append("| Metric | Value |")
    parts.append("|--------|-------|")
    parts.append(f"| Abuse Confidence | **{confidence}%** |")
    parts.append(f"| Total Reports | {total_reports} |")
    parts.append(f"| ISP | {isp} |")
    parts.append(f"| Country | {country} |")
    parts.append(f"| Domain | {domain} |")
    parts.append(f"| Usage Type | {usage} |")
    if last_reported:
        parts.append(f"| Last Reported | {last_reported} |")
    if hostnames:
        parts += ["", "### Hostnames", ""] + [f"- {h}" for h in hostnames[:5]]
    parts.append("")
    parts.append(f"Source: https://www.abuseipdb.com/check/{ip}")

    return "\n".join(parts), metadata


@adapter
class AbuseIPDBAdapter(SiteAdapter):
    name = "abuseipdb"
    patterns = _ABUSEIPDB_URL_PATTERNS
    priority = 180

    async def scrape(self, url: str, ctx: AdapterContext) -> AdapterResult:
        ip = _extract_ip(url)
        if not ip:
            raise AdapterError(f"Could not extract IP from URL: {url}")

        api_key = ctx.config.get("ADAPTER_ABUSEIPDB_API_KEY", "")

        if api_key:
            data = await ctx.with_timeout(_fetch_api(ip, api_key), timeout=10)
            if data:
                md, meta = _format_api_result(data)
                return AdapterResult(
                    success=True,
                    markdown=md,
                    metadata=meta,
                    source="abuseipdb-api",
                    url=url,
                )

        result = await scrape_page(url)
        if result:
            return AdapterResult(
                success=True,
                markdown=result,
                metadata={"ip": ip, "source": "abuseipdb-html"},
                url=url,
            )

        raise AdapterError(f"Could not extract data for {ip}")
