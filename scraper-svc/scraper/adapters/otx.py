"""
AlienVault OTX adapter — threat intelligence indicator lookup.

Fallback chain:
  1. OTX API (/api/v1/indicators/{type}/{value}/general) — structured JSON
  2. Page scrape via readability-lxml

API docs: https://otx.alienvault.com/api
"""

from __future__ import annotations

import logging
import re

from ..source_http import source_httpx_client
from ._helpers import scrape_page
from .base import AdapterContext, AdapterError, AdapterResult, SiteAdapter, adapter

logger = logging.getLogger(__name__)

_OTX_URL_PATTERNS = [
    re.compile(r"^https?://otx\.alienvault\.com/indicator/[^/]+/[^/]+"),
]

_INDICATOR_PATTERN = re.compile(r"/indicator/([^/]+)/([^/]+)")


def _extract_indicator(url: str) -> tuple[str, str] | None:
    m = _INDICATOR_PATTERN.search(url)
    if m:
        return m.group(1), m.group(2)
    return None


async def _fetch_api(indicator_type: str, value: str, api_key: str) -> dict | None:
    if not api_key:
        return None
    try:
        async with source_httpx_client(timeout=10) as client:
            resp = await client.get(
                f"https://otx.alienvault.com/api/v1/indicators/{indicator_type}/{value}/general",
                headers={"X-OTX-API-KEY": api_key},
            )
            if resp.status_code == 200:
                return resp.json()
    except Exception as exc:
        logger.debug("OTX API failed for %s/%s: %s", indicator_type, value, exc)
    return None


def _format_api_result(data: dict, indicator_type: str, value: str) -> tuple[str, dict]:
    pulse_count = (
        data.get("pulse_info", {}).get("count", 0) if data.get("pulse_info") else 0
    )
    pulses = (
        data.get("pulse_info", {}).get("pulses", []) if data.get("pulse_info") else []
    )
    base_score = data.get("base_score", 0)
    reput = data.get("reputation", 0)
    section = data.get("section", "")
    validation = data.get("validation", [])

    metadata = {
        "indicator_type": indicator_type,
        "indicator_value": value,
        "pulse_count": pulse_count,
        "base_score": base_score,
        "reputation": reput,
        "source": "otx-api",
    }

    parts = [f"## AlienVault OTX — {value}", ""]
    parts.append("| Metric | Value |")
    parts.append("|--------|-------|")
    parts.append(f"| Type | {indicator_type} |")
    parts.append(f"| Base Score | {base_score} |")
    parts.append(f"| Reputation | {reput} |")
    parts.append(f"| Pulses | {pulse_count} |")
    parts.append(f"| Section | {section} |")
    if validation:
        parts.append(f"| Validated | {'Yes' if validation else 'No'} |")
    parts.append("")

    if pulses:
        parts.append("### Associated Pulses")
        for p in pulses[:5]:
            name = p.get("name", "Unknown")
            parts.append(f"- {name}")
        parts.append("")

    return "\n".join(parts), metadata


@adapter
class OTXAdapter(SiteAdapter):
    name = "otx"
    patterns = _OTX_URL_PATTERNS
    priority = 180

    async def scrape(self, url: str, ctx: AdapterContext) -> AdapterResult:
        extracted = _extract_indicator(url)
        if not extracted:
            raise AdapterError(f"Could not extract indicator from URL: {url}")
        indicator_type, value = extracted

        api_key = ctx.config.get("ADAPTER_OTX_API_KEY", "")

        if api_key:
            data = await ctx.with_timeout(
                _fetch_api(indicator_type, value, api_key), timeout=10
            )
            if data:
                md, meta = _format_api_result(data, indicator_type, value)
                return AdapterResult(
                    success=True, markdown=md, metadata=meta, source="otx-api", url=url
                )

        result = await scrape_page(url)
        if result:
            return AdapterResult(
                success=True,
                markdown=result,
                metadata={"indicator": value, "source": "otx-html"},
                url=url,
            )

        raise AdapterError(f"Could not extract data for {value}")
