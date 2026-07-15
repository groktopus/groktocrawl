"""Compatibility imports for the pre-split fetch strategy module.

The canonical implementations live in ``fetch.py``, ``fetch_tiers.py``, and
``fetch_quality.py``. Keep these aliases so existing internal consumers do not
silently retain the obsolete duplicate orchestration path.
"""

from .fetch import (
    _enrich_with_politeness,
    _maybe_degrade,
    _politeness_check_and_delay,
    _politeness_check_for_tier,
    smart_scrape,
)
from .fetch_quality import _add_quality, _enrich_with_metadata, _quality_acceptable
from .fetch_tiers import (
    _fetch_via_browser_svc,
    _get_browser_page_content,
    _playwright_fetch_with_proxy,
    fetch_via_content_negotiation,
    fetch_via_flaresolverr,
    fetch_via_llms_txt,
    fetch_via_playwright,
    html_to_markdown,
)

__all__ = [
    "_add_quality",
    "_enrich_with_metadata",
    "_enrich_with_politeness",
    "_fetch_via_browser_svc",
    "_get_browser_page_content",
    "_maybe_degrade",
    "_playwright_fetch_with_proxy",
    "_politeness_check_and_delay",
    "_politeness_check_for_tier",
    "_quality_acceptable",
    "fetch_via_content_negotiation",
    "fetch_via_flaresolverr",
    "fetch_via_llms_txt",
    "fetch_via_playwright",
    "html_to_markdown",
    "smart_scrape",
]
