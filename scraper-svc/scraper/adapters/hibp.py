"""
Have I Been Pwned adapter — breach and paste lookup for email addresses.

API docs: https://haveibeenpwned.com/API/v3
"""

from __future__ import annotations

import logging
import re

from ..source_http import source_httpx_client
from ._helpers import scrape_page
from .base import AdapterContext, AdapterError, AdapterResult, SiteAdapter, adapter

logger = logging.getLogger(__name__)

_HIBP_URL_PATTERNS = [
    re.compile(r"^https?://(?:www\.)?haveibeenpwned\.com/account/[^/]+"),
]

_EMAIL_PATTERN = re.compile(r"/account/([^/?#]+)")


def _extract_email(url: str) -> str | None:
    m = _EMAIL_PATTERN.search(url)
    if m:
        return m.group(1)
    return None


async def _fetch_breaches(email: str, api_key: str) -> list[dict] | None:
    if not api_key:
        return None
    try:
        async with source_httpx_client(timeout=10) as client:
            resp = await client.get(
                f"https://haveibeenpwned.com/api/v3/breachedaccount/{email}",
                headers={
                    "hibp-api-key": api_key,
                    "hibp-client-id": "groktocrawl",
                    "User-Agent": "GroktoCrawl/0.7.0",
                },
                params={"truncateResponse": "false"},
            )
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code == 404:
                return []
    except Exception as exc:
        logger.debug("HIBP API failed for %s: %s", email, exc)
    return None


def _format_breaches(breaches: list[dict], email: str) -> tuple[str, dict]:
    metadata = {
        "email": email,
        "breach_count": len(breaches),
        "source": "hibp-api",
    }

    parts = [f"## Have I Been Pwned — {email}", ""]

    if not breaches:
        parts.append("No breaches found for this account.")
        return "\n".join(parts), metadata

    parts.append(f"Found in **{len(breaches)}** breaches:")
    parts.append("")
    parts.append("| Breach | Date | Compromised Data |")
    parts.append("|--------|------|------------------|")

    for b in breaches[:20]:
        name = b.get("Name", "")
        date = b.get("BreachDate", "")
        classes = ", ".join(b.get("DataClasses", []))
        parts.append(f"| {name} | {date} | {classes} |")

    parts.append("")
    return "\n".join(parts), metadata


@adapter
class HIBPAdapter(SiteAdapter):
    name = "hibp"
    patterns = _HIBP_URL_PATTERNS
    priority = 180

    async def scrape(self, url: str, ctx: AdapterContext) -> AdapterResult:
        email = _extract_email(url)
        if not email:
            raise AdapterError(f"Could not extract email from URL: {url}")

        api_key = ctx.config.get("ADAPTER_HIBP_API_KEY", "")

        if api_key:
            breaches = await ctx.with_timeout(
                _fetch_breaches(email, api_key), timeout=10
            )
            if breaches is not None:
                md, meta = _format_breaches(breaches, email)
                return AdapterResult(
                    success=True, markdown=md, metadata=meta, source="hibp-api", url=url
                )

        result = await scrape_page(url)
        if result:
            return AdapterResult(
                success=True,
                markdown=result,
                metadata={"email": email, "source": "hibp-html"},
                url=url,
            )

        raise AdapterError(f"Could not extract breach data for {email}")
