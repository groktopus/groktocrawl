"""Best-effort admission of completed research into Research Memory."""

import logging
import os
from typing import Any

from ..models import CitationStyle
from .citations import _apply_citation_style

logger = logging.getLogger(__name__)


async def admit_research_memory(
    research_memory: Any,
    *,
    prompt: str,
    artifact: str,
    source_details: list[dict[str, Any]] | list[str],
    model: str,
    citation_style: str,
    requested_model: str | None = None,
    latency_ms: int = 0,
    user_id: str | None = None,
    fingerprint: str | None = None,
) -> str | None:
    """Store a valid final artifact, treating unavailable memory as non-fatal."""
    if (
        research_memory is None
        or not artifact
        or artifact.startswith("Error:")
        or not source_details
    ):
        return None

    memory_scope = os.environ.get("RESEARCH_MEMORY_SCOPE", "global")
    if memory_scope == "per_user" and user_id is None:
        user_id = "anonymous"
    metadata: dict[str, Any] = {
        "model": model,
        "citation_style": citation_style,
        "latency_ms": latency_ms,
    }
    if requested_model and requested_model != "default":
        metadata["requested_model"] = requested_model

    try:
        artifact_id = await research_memory.store(
            prompt=prompt,
            artifact=artifact,
            sources=source_details,
            model=model,
            user_id=user_id if memory_scope == "per_user" else None,
            metadata=metadata,
            fingerprint=fingerprint,
        )
        logger.info(
            "Stored research memory artifact %s (scope=%s)", artifact_id, memory_scope
        )
        return artifact_id
    except Exception:
        logger.warning(
            "Failed to store research memory (service may be down)", exc_info=True
        )
        return None


async def finalize_and_admit(
    research_memory: Any,
    *,
    prompt: str,
    result: dict[str, Any],
    llm_model: str,
    citation_style: CitationStyle,
    requested_model: str | None = None,
    user_id: str | None = None,
    fingerprint: str | None = None,
) -> dict[str, Any]:
    """Apply citation transform and admit a ``run_research`` result to memory.

    Returns the mutated *result* dict so the caller keeps a single source of
    truth for the completed response payload.
    """
    source_details = result.get("source_details", [])
    result_text, _ = _apply_citation_style(
        result["result"], source_details, citation_style
    )
    result["result"] = result_text
    memory_sources = source_details or result.get("sources", [])

    if citation_style == CitationStyle.compact:
        compact_sources: list[dict[str, str | int]] = []
        for i, src in enumerate(source_details, start=1):
            compact_sources.append({"index": i, "url": src.get("url", "")})
        result["sources_compact"] = compact_sources
        result["source_details"] = []

    artifact_id = await admit_research_memory(
        research_memory,
        prompt=prompt,
        artifact=result_text,
        source_details=memory_sources,
        model=llm_model,
        citation_style=citation_style.value,
        requested_model=requested_model,
        latency_ms=result.get("latency_ms", 0),
        user_id=user_id,
        fingerprint=fingerprint,
    )
    if artifact_id:
        result["research_memory_id"] = artifact_id
    return result


async def refresh_research_memory(
    research_memory: Any,
    *,
    prompt: str,
    urls: list[str] | None,
    schema: dict | None,
    searxng_url: str,
    scraper_url: str,
    llm_base_url: str,
    llm_api_key: str,
    llm_model: str,
    requested_model: str | None,
    max_searches_per_request: int,
    max_credits: int | None = None,
    include_images: bool,
    include_source_content: bool,
    citation_style: CitationStyle,
    search_type: str,
    user_id: str | None,
    fingerprint: str | None,
) -> dict[str, Any]:
    """Re-run the research pipeline and re-admit a fresh result to memory.

    Used by stale-while-revalidate so a single-flight refresh produces the
    same post-transform artifact shape as the blocking-fresh pipeline.
    """
    from .loop import run_research

    result = await run_research(
        prompt=prompt,
        urls=urls,
        schema=schema,
        searxng_url=searxng_url,
        scraper_url=scraper_url,
        llm_base_url=llm_base_url,
        llm_api_key=llm_api_key,
        llm_model=llm_model,
        requested_model=requested_model,
        max_searches_per_request=max_searches_per_request,
        max_credits=max_credits,
        include_images=include_images,
        include_source_content=include_source_content,
        citation_style=citation_style,
        search_type=search_type,
    )
    return await finalize_and_admit(
        research_memory,
        prompt=prompt,
        result=result,
        llm_model=llm_model,
        citation_style=citation_style,
        requested_model=requested_model,
        user_id=user_id,
        fingerprint=fingerprint,
    )
