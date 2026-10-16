"""
Censys adapter — internet host and certificate lookup.

API docs: https://search.censys.io/api/v2/docs
"""

from __future__ import annotations

import logging
import re

from ..source_http import source_httpx_client
from ._helpers import scrape_page
from .base import AdapterContext, AdapterError, AdapterResult, SiteAdapter, adapter

logger = logging.getLogger(__name__)

_CENSYS_URL_PATTERNS = [
    re.compile(
        r"^https?://(?:search\.)?censys\.io/ipv4/\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}"
    ),
    re.compile(r"^https?://(?:search\.)?censys\.io/certificates/[^/]+"),
]

_IP_PATTERN = re.compile(r"\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\b")


def _extract_ip(url: str) -> str | None:
    m = _IP_PATTERN.search(url)
    return m.group(1) if m else None


async def _fetch_api(ip: str, api_id: str, api_secret: str) -> dict | None:
    if not api_id or not api_secret:
        return None
    try:
        async with source_httpx_client(timeout=10) as client:
            resp = await client.get(
                f"https://search.censys.io/api/v2/hosts/{ip}",
                auth=(api_id, api_secret),
            )
            if resp.status_code == 200:
                return resp.json().get("result")
    except Exception as exc:
        logger.debug("Censys API failed for %s: %s", ip, exc)
    return None


def _format_api_result(data: dict, ip: str) -> tuple[str, dict]:
    services = data.get("services", [])
    location = data.get("location", {})
    autonomous_system = data.get("autonomous_system", {})

    metadata = {
        "ip": ip,
        "service_count": len(services),
        "asn": autonomous_system.get("asn", ""),
        "as_name": autonomous_system.get("name", ""),
        "country": location.get("country", ""),
        "city": location.get("city", ""),
        "source": "censys-api",
    }

    parts = [f"## Censys — {ip}", ""]
    parts.append("| Metric | Value |")
    parts.append("|--------|-------|")
    if autonomous_system:
        parts.append(
            f"| ASN | {autonomous_system.get('asn', '')} ({autonomous_system.get('name', '')}) |"
        )
    if location:
        parts.append(
            f"| Location | {location.get('city', '')}, {location.get('country', '')} |"
        )
    parts.append(f"| Services | {len(services)} |")

    if services:
        parts += ["", "### Services", ""]
        parts.append("| Port | Protocol | Service |")
        parts.append("|------|----------|---------|")
        for svc in services[:20]:
            port = svc.get("port", "")
            proto = svc.get("transport_protocol", "")
            svc_name = svc.get("service_name", "")
            parts.append(f"| {port} | {proto} | {svc_name} |")

    parts.append("")
    return "\n".join(parts), metadata


@adapter
class CensysAdapter(SiteAdapter):
    name = "censys"
    patterns = _CENSYS_URL_PATTERNS
    priority = 180

    async def scrape(self, url: str, ctx: AdapterContext) -> AdapterResult:
        ip = _extract_ip(url)
        if not ip:
            raise AdapterError(f"Could not extract IP from URL: {url}")

        api_id = ctx.config.get("ADAPTER_CENSYS_API_ID", "")
        api_secret = ctx.config.get("ADAPTER_CENSYS_API_SECRET", "")

        if api_id and api_secret:
            data = await ctx.with_timeout(
                _fetch_api(ip, api_id, api_secret), timeout=10
            )
            if data:
                md, meta = _format_api_result(data, ip)
                return AdapterResult(
                    success=True,
                    markdown=md,
                    metadata=meta,
                    source="censys-api",
                    url=url,
                )

        result = await scrape_page(url)
        if result:
            return AdapterResult(
                success=True,
                markdown=result,
                metadata={"ip": ip, "source": "censys-html"},
                url=url,
            )

        raise AdapterError(f"Could not extract data for {ip}")
