"""
Shodan adapter — internet-connected device lookup.

Fallback chain:
  1. Shodan API (/shodan/host/{ip}) — structured JSON with banners, services, CVEs
  2. Page scrape via readability-lxml

API docs: https://developer.shodan.io/api
"""

from __future__ import annotations

import logging
import re

from ..source_http import source_httpx_client
from ._helpers import scrape_page
from .base import AdapterContext, AdapterError, AdapterResult, SiteAdapter, adapter

logger = logging.getLogger(__name__)

_SHODAN_URL_PATTERNS = [
    re.compile(
        r"^https?://(?:www\.)?shodan\.io/host/\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}"
    ),
    re.compile(r"^https?://(?:www\.)?shodan\.io/search\?query="),
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
                f"https://api.shodan.io/shodan/host/{ip}",
                params={"key": api_key, "minify": True},
            )
            if resp.status_code == 200:
                return resp.json()
    except Exception as exc:
        logger.debug("Shodan API failed for %s: %s", ip, exc)
    return None


def _format_api_result(data: dict, ip: str) -> tuple[str, dict]:
    org = data.get("org", "")
    isp = data.get("isp", "")
    country = data.get("country_name", "")
    city = data.get("city", "")
    ports = data.get("ports", [])
    hostnames = data.get("hostnames", [])
    vulns = data.get("vulns", [])
    os = data.get("os", "")

    metadata = {
        "ip": ip,
        "org": org,
        "isp": isp,
        "country": country,
        "city": city,
        "open_ports": len(ports),
        "vuln_count": len(vulns),
        "os": os,
        "source": "shodan-api",
    }

    parts = [f"## Shodan — {ip}", ""]
    parts.append("| Metric | Value |")
    parts.append("|--------|-------|")
    if org:
        parts.append(f"| Organization | {org} |")
    if isp:
        parts.append(f"| ISP | {isp} |")
    if country:
        parts.append(f"| Country | {country} |")
    if city:
        parts.append(f"| City | {city} |")
    if os:
        parts.append(f"| OS | {os} |")
    if ports:
        parts.append(
            f"| Open Ports | {len(ports)} ({', '.join(str(p) for p in ports[:10])}) |"
        )
    if vulns:
        parts.append(f"| Vulnerabilities | {', '.join(list(vulns)[:10])} |")
    if hostnames:
        parts += ["", "### Hostnames", ""] + [f"- {h}" for h in hostnames[:5]]
    parts.append("")

    return "\n".join(parts), metadata


@adapter
class ShodanAdapter(SiteAdapter):
    name = "shodan"
    patterns = _SHODAN_URL_PATTERNS
    priority = 180

    async def scrape(self, url: str, ctx: AdapterContext) -> AdapterResult:
        ip = _extract_ip(url)
        if not ip:
            raise AdapterError(f"Could not extract IP from URL: {url}")

        api_key = ctx.config.get("ADAPTER_SHODAN_API_KEY", "")

        if api_key:
            data = await ctx.with_timeout(_fetch_api(ip, api_key), timeout=10)
            if data:
                md, meta = _format_api_result(data, ip)
                return AdapterResult(
                    success=True,
                    markdown=md,
                    metadata=meta,
                    source="shodan-api",
                    url=url,
                )

        result = await scrape_page(url)
        if result:
            return AdapterResult(
                success=True,
                markdown=result,
                metadata={"ip": ip, "source": "shodan-html"},
                url=url,
            )

        raise AdapterError(f"Could not extract data for {ip}")
