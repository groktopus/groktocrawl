"""Streaming generators for the research agent.

Populated in Milestone 2 with stream_cached_artifact() and
stream_research_live() async generators extracted from api.py.
"""

import json
import logging
import time as _time
from typing import Any

from ..models import CitationStyle
from .citations import _apply_citation_style
from .loop import run_research_stream
from .memory import admit_research_memory

logger = logging.getLogger(__name__)


async def stream_cached_artifact(
    artifact_text: str,
    sources: list,
    memory_id: str,
    freshness: str,
    similarity: float,
    citation_style: CitationStyle,
    has_schema: bool,
) -> Any:
    """Replay cached agent results as SSE token events.

    Preserves citation style transformation and schema-mode skip logic.
    """
    stream_start = _time.monotonic()

    # Apply citation style
    transformed_text, _ = _apply_citation_style(artifact_text, sources, citation_style)

    # Schema mode (schema or output_schema): skip token replay,
    # emit only done event (matches non-cached schema streaming behavior)
    if not has_schema:
        # Replay artifact as token events
        chunk_size = 8
        for i in range(0, len(transformed_text), chunk_size):
            chunk = transformed_text[i : i + chunk_size]
            yield f"data: {json.dumps({'type': 'token', 'content': chunk})}\n\n"

    latency_ms = int((_time.monotonic() - stream_start) * 1000)
    done_payload: dict = {
        "type": "done",
        "result": transformed_text,
        "sources": [s.get("url", "") for s in sources],
        "latency_ms": latency_ms,
        "from_cache": True,
        "memory_id": memory_id,
        "freshness": freshness,
        "similarity": similarity,
        "citation_style": citation_style.value,
    }
    if citation_style == CitationStyle.compact:
        compact_srcs = []
        for i, src in enumerate(sources, start=1):
            compact_srcs.append({"index": i, "url": src.get("url", "")})
        done_payload["sources_compact"] = compact_srcs
        done_payload["source_details"] = []
    else:
        done_payload["source_details"] = sources
    yield f"data: {json.dumps(done_payload)}\n\n"
    yield "data: [DONE]\n\n"


async def stream_research_live(
    prompt: str,
    urls: Any,
    schema: dict | None,
    searxng_url: str,
    scraper_url: str,
    llm_base_url: str,
    llm_api_key: str,
    llm_model: str,
    requested_model: str | None,
    max_searches_per_request: int,
    include_images: bool,
    citation_style: CitationStyle,
    search_type: str = "deep",
    research_memory: Any = None,
    user_id: str | None = None,
) -> Any:
    """Orchestrate full research SSE pipeline for cache-miss or force-fresh.

    Forwards all 8 SSE event types: token, done, sources_pending,
    source_scraped, sources, error, status, research_plan, research_pass.
    """
    async for event in run_research_stream(
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
        include_images=include_images,
        citation_style=citation_style,
        search_type=search_type,
    ):
        if event["type"] == "sources_pending":
            yield f"data: {json.dumps({'type': 'sources_pending', 'sources': event['sources']})}\n\n"
        elif event["type"] == "source_scraped":
            yield f"data: {json.dumps({'type': 'source_scraped', 'url': event['url'], 'source': event.get('source', ''), 'chars': event.get('chars', 0)})}\n\n"
        elif event["type"] == "sources":
            yield f"data: {json.dumps({'type': 'sources', 'sources': event['sources']})}\n\n"
        elif event["type"] == "token":
            yield f"data: {json.dumps({'type': 'token', 'content': event['content']})}\n\n"
        elif event["type"] == "done":
            # Apply citation_style to transform result text markers
            source_details = event.get("source_details", [])
            cs = citation_style

            transformed_result, _ = _apply_citation_style(
                event["result"], source_details, cs
            )
            if research_memory is not None:
                await admit_research_memory(
                    research_memory,
                    prompt=prompt,
                    artifact=transformed_result,
                    source_details=source_details,
                    model=llm_model,
                    citation_style=cs.value,
                    requested_model=requested_model,
                    latency_ms=event["latency_ms"],
                    user_id=user_id,
                )

            done_payload: dict = {
                "type": "done",
                "result": transformed_result,
                "sources": event["sources"],
                "latency_ms": event["latency_ms"],
            }
            # Apply citation_style transformation (VAL-CC-008, VAL-CC-009)
            done_payload["citation_style"] = cs.value
            if cs == CitationStyle.compact:
                compact_sources = []
                for i, src in enumerate(source_details, start=1):
                    compact_sources.append(
                        {
                            "index": i,
                            "url": src.get("url", ""),
                        }
                    )
                done_payload["sources_compact"] = compact_sources
                done_payload["source_details"] = []
            else:
                done_payload["source_details"] = source_details
            yield f"data: {json.dumps(done_payload)}\n\n"
        elif event["type"] == "error":
            yield f"data: {json.dumps({'type': 'error', 'content': event['content']})}\n\n"
        elif event["type"] == "status":
            yield f"data: {json.dumps({'type': 'status', 'state': event['state']})}\n\n"
        elif event["type"] == "research_plan":
            yield f"data: {json.dumps({'type': 'research_plan', 'strategy': event['strategy'], 'queries': event['queries'], 'reasoning': event['reasoning']})}\n\n"
        elif event["type"] == "research_pass":
            yield f"data: {json.dumps({'type': 'research_pass', 'pass': event['pass'], 'total_passes': event['total_passes']})}\n\n"
    yield "data: [DONE]\n\n"
