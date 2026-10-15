"""
CRT.sh adapter — Certificate Transparency log lookup.

No API key required.
API docs: https://crt.sh/
"""

from __future__ import annotations

import logging
import re

from ..source_http import source_httpx_client
from ._helpers import scrape_page
from .base import AdapterContext, AdapterError, AdapterResult, SiteAdapter, adapter

logger = logging.getLogger(__name__)

_CRTSH_URL_PATTERNS = [
    re.compile(r"^https?://crt\.sh/\?q="),
    re.compile(r"^https?://crt\.sh/\?id=\d+"),
]

_DOMAIN_PATTERN = re.compile(r"\?q=([^&]+)")


def _extract_domain(url: str) -> str | None:
    m = _DOMAIN_PATTERN.search(url)
    if m:
        return m.group(1)
    return None


async def _fetch_api(domain: str) -> list[dict] | None:
    try:
        async with source_httpx_client(timeout=10) as client:
            resp = await client.get(
                "https://crt.sh/", params={"output": "json", "q": domain}
            )
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list):
                    return data
    except Exception as exc:
        logger.debug("CRT.sh API failed for %s: %s", domain, exc)
    return None


def _format_api_result(certs: list[dict], domain: str) -> tuple[str, dict]:
    metadata = {
        "domain": domain,
        "certificate_count": len(certs),
        "source": "crtsh-api",
    }

    parts = [f"## Certificate Transparency Logs — {domain}", ""]
    parts.append("| Common Name | Issuer | Not Before | Not After |")
    parts.append("|-------------|--------|------------|-----------|")

    for cert in certs[:20]:
        cn = cert.get("common_name", "")
        issuer = cert.get("issuer_name", "")[:40] if cert.get("issuer_name") else ""
        nb = (cert.get("not_before") or "")[:10]
        na = (cert.get("not_after") or "")[:10]
        parts.append(f"| {cn} | {issuer} | {nb} | {na} |")

    parts.append("")
    return "\n".join(parts), metadata


@adapter
class CrtShAdapter(SiteAdapter):
    name = "crtsh"
    patterns = _CRTSH_URL_PATTERNS
    priority = 200

    async def scrape(self, url: str, ctx: AdapterContext) -> AdapterResult:
        domain = _extract_domain(url)
        if not domain:
            raise AdapterError(f"Could not extract domain from URL: {url}")

        certs = await ctx.with_timeout(_fetch_api(domain), timeout=10)
        if certs:
            md, meta = _format_api_result(certs, domain)
            return AdapterResult(
                success=True, markdown=md, metadata=meta, source="crtsh-api", url=url
            )

        result = await scrape_page(url)
        if result:
            return AdapterResult(
                success=True,
                markdown=result,
                metadata={"domain": domain, "source": "crtsh-html"},
                url=url,
            )

        raise AdapterError(f"Could not extract certificate data for {domain}")
