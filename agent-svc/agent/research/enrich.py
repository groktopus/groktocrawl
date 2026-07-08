"""Enrichment pipeline for web-sourced structured data."""

import asyncio
import json as _json
import logging

from ..llm import LLMClient
from ..scraper_client import ScraperClient
from ..searxng_client import SearXNGClient
from .prompts import ENRICH_SYSTEM_PROMPT

logger = logging.getLogger(__name__)


def _parse_enrich_json(text: str) -> dict:
    """Parse JSON from an LLM enrichment response, handling markdown wrapping."""
    try:
        return _json.loads(text.strip())
    except (_json.JSONDecodeError, Exception):
        pass

    # Extract JSON block if wrapped in markdown
    if "```json" in text:
        try:
            block = text.split("```json")[1].split("```")[0].strip()
            return _json.loads(block)
        except (_json.JSONDecodeError, Exception):
            pass
    elif "```" in text:
        try:
            block = text.split("```")[1].split("```")[0].strip()
            return _json.loads(block)
        except (_json.JSONDecodeError, Exception):
            pass

    logger.warning("Enrich: failed to parse LLM JSON response")
    return {}


async def run_enrich_pipeline(
    items: list[dict],
    fields: dict,
    source_hint: str | None = None,
    effort: str = "low",
    searxng_url: str = "http://searxng:8080",
    scraper_url: str = "http://scraper-svc:8001",
    llm_base_url: str = "https://api.openai.com/v1",
    llm_api_key: str = "",
    llm_model: str | None = None,
) -> list[dict]:
    """Enrich a list of entities with web-sourced structured data.

    Each item is processed independently: search → scrape → LLM extraction.
    Items are processed in parallel with a bounded semaphore.

    Args:
        items: List of entity dicts to enrich (e.g., [{"company": "Anthropic"}]).
        fields: Dict of field_name → EnrichmentField (with description).
        source_hint: Optional hint to guide search (company, person, url, product).
        effort: Controls search depth — "low" (3 results), "medium"/"high" (5).
        searxng_url: SearXNG API base URL.
        scraper_url: Scraper service base URL.
        llm_base_url: LLM API base URL.
        llm_api_key: LLM API key.
        llm_model: LLM model name.

    Returns:
        List of dicts with keys: ``item`` (original item) and ``enrichments``
        (dict of field_name → {value, source}).
    """
    search_limit = 3 if effort == "low" else 5
    if llm_model is None:
        raise ValueError("llm_model is required — set via LLM_MODEL env var")

    semaphore = asyncio.Semaphore(3)

    async def enrich_one(item: dict) -> dict:
        async with semaphore:
            searxng = SearXNGClient(searxng_url)
            scraper = ScraperClient(scraper_url)
            llm = LLMClient(llm_base_url, llm_api_key, llm_model)
            try:
                # Build search query from item values + source_hint
                search_parts = [str(v) for v in item.values() if v is not None]
                if source_hint:
                    search_parts.append(source_hint)
                query = " ".join(search_parts) if search_parts else ""

                if not query:
                    return {"item": item, "enrichments": {}}

                # Search
                logger.info("Enrich: searching for: %s", query)
                results, _health = await searxng.search(query, limit=search_limit)

                if not results:
                    logger.info("Enrich: no results for: %s", query)
                    return {"item": item, "enrichments": {}}

                # Scrape top result
                top_url = results[0]["url"]
                logger.info("Enrich: scraping: %s", top_url)
                scraped = await scraper.scrape(top_url)

                markdown = (
                    scraped.get("data", {}).get("markdown", "")
                    if scraped.get("success")
                    else ""
                )
                if not markdown:
                    return {"item": item, "enrichments": {}}

                # LLM extraction
                field_descriptions = "\n".join(
                    f"- {name}: {field.description}" for name, field in fields.items()
                )
                extract_prompt = (
                    "Extract the following fields from the text below.\n"
                    "Return ONLY a JSON object where keys are field names "
                    'and values are objects with "value" and "source_url".\n\n'
                    f"Source URL: {top_url}\n\n"
                    f"Fields to extract:\n{field_descriptions}\n\n"
                    f"---TEXT---\n{markdown[:8000]}"
                )

                try:
                    result_text = await llm.generate(
                        system_prompt=ENRICH_SYSTEM_PROMPT,
                        user_prompt=extract_prompt,
                    )
                    extracted = _parse_enrich_json(result_text)
                except Exception:
                    logger.warning(
                        "Enrich: LLM extraction failed for: %s", query, exc_info=True
                    )
                    extracted = {}

                enrichments: dict[str, dict[str, str | None]] = {}
                for name in fields:
                    val = extracted.get(name, {})
                    if isinstance(val, dict):
                        enrichments[name] = {
                            "value": val.get("value"),
                            "source": val.get("source_url", top_url),
                        }
                    else:
                        enrichments[name] = {
                            "value": str(val) if val is not None else None,
                            "source": top_url,
                        }

                return {"item": item, "enrichments": enrichments}
            finally:
                await searxng.close()
                await scraper.close()
                await llm.close()

    tasks = [enrich_one(item) for item in items]
    results = await asyncio.gather(*tasks)
    return list(results)
