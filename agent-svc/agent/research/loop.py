"""Main research loops: run_research, run_research_stream, run_answer,
run_answer_stream, run_extract."""

import logging
import re
import time
from collections.abc import AsyncGenerator
from typing import Any

from ..llm import LLMClient
from ..models import CitationStyle
from ..scraper_client import ScraperClient
from ..searxng_client import SearXNGClient
from .citations import _apply_citation_style, _build_answer_user_prompt
from .discovery import (
    _run_answer_discover_and_scrape,
    _run_multi_query_discover_and_scrape,
    _run_research_discover_and_scrape,
    _scrape_urls,
)
from .events import ResearchEvent
from .gaps import _detect_gaps
from .plan import _generate_research_plan
from .prompts import ANSWER_SYSTEM_PROMPT, EXTRACT_SYSTEM_PROMPT, SYSTEM_PROMPT
from .utils import _validate_json_if_schema

logger = logging.getLogger(__name__)


async def _run_research_events(
    prompt: str,
    urls: list[str] | None = None,
    schema: dict | None = None,
    searxng_url: str = "http://searxng:8080",
    scraper_url: str = "http://scraper-svc:8001",
    llm_base_url: str = "https://api.openai.com/v1",
    llm_api_key: str = "",
    llm_model: str | None = None,
    requested_model: str | None = None,
    max_searches_per_request: int = 5,
    include_images: bool = False,
    citation_style: Any = None,
    search_type: str = "deep",
    stream_tokens: bool = False,
) -> AsyncGenerator[ResearchEvent, None]:
    """Execute the canonical research loop and emit progress and terminal events."""
    start = time.monotonic()
    if llm_model is None:
        raise ValueError("llm_model is required — set via LLM_MODEL env var")
    searxng = SearXNGClient(searxng_url, max_searches=max_searches_per_request)
    scraper = ScraperClient(scraper_url)
    effective_model = (
        requested_model
        if requested_model and requested_model != "default"
        else llm_model
    )
    llm = LLMClient(llm_base_url, llm_api_key, effective_model)
    scrape_opts: dict | None = (
        {"formats": ["markdown", "images"]} if include_images else None
    )

    try:
        yield {"type": "status", "state": "planning"}
        research_plan = await _generate_research_plan(prompt, llm)
        queries = research_plan["focused_queries"]
        strategy = research_plan["research_strategy"]
        if search_type == "deep":
            strategy = "deep"
        elif search_type == "focused":
            strategy = "focused"
        yield {
            "type": "research_plan",
            "strategy": strategy,
            "queries": queries,
            "reasoning": research_plan.get("reasoning", ""),
        }

        pass_count = 0
        max_passes = 2 if search_type == "deep" else 1
        all_source_details: list[dict] = []
        combined_context = ""
        gap_topics: list[str] = []
        answer = ""

        while pass_count < max_passes:
            pass_count += 1
            yield {
                "type": "research_pass",
                "pass": pass_count,
                "total_passes": max_passes,
            }
            yield {"type": "status", "state": "searching"}

            if pass_count == 1:
                # ── Pass 1: normal discovery ──────────────────────
                if strategy == "deep" and len(queries) > 1:
                    discovered = await _run_multi_query_discover_and_scrape(
                        queries=queries,
                        urls=urls,
                        searxng=searxng,
                        scraper=scraper,
                        max_searches_per_request=max_searches_per_request,
                        scrape_options=scrape_opts,
                    )
                else:
                    query = queries[0] if queries else prompt
                    discovered = await _run_research_discover_and_scrape(
                        prompt=query,
                        urls=urls,
                        searxng=searxng,
                        scraper=scraper,
                        scrape_options=scrape_opts,
                    )
            else:
                # ── Pass 2: gap-focused discovery ─────────────────
                discovered = await _run_multi_query_discover_and_scrape(
                    queries=gap_topics,
                    urls=None,
                    searxng=searxng,
                    scraper=scraper,
                    max_searches_per_request=min(
                        len(gap_topics), max_searches_per_request
                    ),
                    scrape_options=scrape_opts,
                )

            context = discovered["context"]
            source_details = discovered["source_details"]
            search_results = discovered["search_results"]
            pending_sources = (
                [
                    {
                        "url": result["url"],
                        "title": result.get("title", ""),
                        "relevance": result.get("description", ""),
                    }
                    for result in search_results
                    if result.get("url")
                ]
                if not urls
                else [{"url": url, "title": "", "relevance": ""} for url in urls]
            )
            yield {"type": "sources_pending", "sources": pending_sources}
            for source in source_details:
                yield {
                    "type": "source_scraped",
                    "url": source["url"],
                    "source": source["source"],
                    "chars": source["char_count"],
                }
            if not context and not combined_context:
                yield {"type": "sources", "sources": []}
                yield {
                    "type": "done",
                    "result": "I was unable to find or scrape any relevant web pages.",
                    "sources": [],
                    "source_details": [],
                    "latency_ms": int((time.monotonic() - start) * 1000),
                }
                return

            all_source_details.extend(source_details)
            if pass_count == 1:
                combined_context = context
            else:
                if context:
                    combined_context = (
                        combined_context
                        + "\n\n---\n\nAdditional sources (pass 2):\n\n"
                        + context
                    )

            if not combined_context:
                yield {"type": "sources", "sources": []}
                yield {
                    "type": "done",
                    "result": "I was unable to find or scrape any relevant web pages.",
                    "sources": [],
                    "source_details": [],
                    "latency_ms": int((time.monotonic() - start) * 1000),
                }
                return

            yield {"type": "status", "state": "synthesizing"}
            if schema or not stream_tokens:
                answer = await llm.generate(
                    system_prompt=SYSTEM_PROMPT,
                    user_prompt=prompt,
                    context=combined_context,
                    schema=schema,
                )
                _validate_json_if_schema(answer, schema)
                if not schema and not stream_tokens:
                    yield {
                        "type": "sources",
                        "sources": [s["url"] for s in all_source_details],
                    }
            else:
                yield {
                    "type": "sources",
                    "sources": [s["url"] for s in all_source_details],
                }
                answer = ""
                async for event in llm.generate_stream(
                    system_prompt=SYSTEM_PROMPT,
                    user_prompt=prompt,
                    context=combined_context,
                ):
                    if event["type"] == "token":
                        answer += event["content"]
                        yield {"type": "token", "content": event["content"]}
                    elif event["type"] == "error":
                        yield {"type": "error", "content": event["content"]}
                        return
                    elif event["type"] == "done":
                        answer = event["full_content"]

            # ── Gap detection after pass 1 ─────────────────────────
            if pass_count == 1:
                gap_topics = await _detect_gaps(
                    combined_context, llm, original_query=prompt
                )
                if not gap_topics:
                    break  # Coverage is adequate, done
                max_passes = 2  # Enable second pass

        source_list = [source["url"] for source in all_source_details]
        if schema:
            yield {"type": "sources", "sources": source_list}
        yield {
            "type": "done",
            "result": answer,
            "sources": source_list,
            "source_details": all_source_details,
            "latency_ms": int((time.monotonic() - start) * 1000),
        }
    finally:
        await searxng.close()
        await scraper.close()
        await llm.close()


async def run_research(
    prompt: str,
    urls: list[str] | None = None,
    schema: dict | None = None,
    searxng_url: str = "http://searxng:8080",
    scraper_url: str = "http://scraper-svc:8001",
    llm_base_url: str = "https://api.openai.com/v1",
    llm_api_key: str = "",
    llm_model: str | None = None,
    requested_model: str | None = None,
    max_searches_per_request: int = 5,
    include_images: bool = False,
    citation_style: Any = None,
    search_type: str = "deep",
) -> dict:
    """Consume the canonical research event stream and return its terminal result."""
    async for event in _run_research_events(
        prompt,
        urls,
        schema,
        searxng_url,
        scraper_url,
        llm_base_url,
        llm_api_key,
        llm_model,
        requested_model,
        max_searches_per_request,
        include_images,
        citation_style,
        search_type,
    ):
        if event["type"] == "done":
            result = event["result"]
            if not event["sources"] and result == (
                "I was unable to find or scrape any relevant web pages."
            ):
                result = (
                    "I was unable to find or scrape any relevant web pages "
                    "to answer your question."
                )
            return {
                "result": result,
                "sources": event["sources"],
                "source_details": event["source_details"],
            }
    raise RuntimeError("Research event engine ended without a terminal done event")


async def run_research_stream(
    prompt: str,
    urls: list[str] | None = None,
    schema: dict | None = None,
    searxng_url: str = "http://searxng:8080",
    scraper_url: str = "http://scraper-svc:8001",
    llm_base_url: str = "https://api.openai.com/v1",
    llm_api_key: str = "",
    llm_model: str | None = None,
    requested_model: str | None = None,
    max_searches_per_request: int = 5,
    include_images: bool = False,
    citation_style: Any = None,
    search_type: str = "deep",
) -> AsyncGenerator[ResearchEvent, None]:
    """Expose events from the canonical research engine for SSE adaptation."""
    async for event in _run_research_events(
        prompt,
        urls,
        schema,
        searxng_url,
        scraper_url,
        llm_base_url,
        llm_api_key,
        llm_model,
        requested_model,
        max_searches_per_request,
        include_images,
        citation_style,
        search_type,
        stream_tokens=True,
    ):
        yield event


async def run_extract(
    urls: list[str],
    prompt: str | None = None,
    schema: dict | None = None,
    scraper_url: str = "http://scraper-svc:8001",
    llm_base_url: str = "https://api.openai.com/v1",
    llm_api_key: str = "",
    llm_model: str | None = None,
) -> dict:
    """Extract structured data from given URLs. No search step."""
    if llm_model is None:
        raise ValueError("llm_model is required — set via LLM_MODEL env var")
    scraper = ScraperClient(scraper_url)
    llm = LLMClient(llm_base_url, llm_api_key, llm_model)

    try:
        documents, source_details = await _scrape_urls(urls, scraper)
        context = "\n\n---\n\n".join(documents) if documents else ""

        if not context:
            return {
                "result": "No content could be extracted from the provided URLs.",
                "sources": [],
                "source_details": [],
            }

        user_prompt = (
            prompt or "Extract the requested information from the provided content."
        )
        answer = await llm.generate(
            system_prompt=EXTRACT_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            context=context,
            schema=schema,
        )
        _validate_json_if_schema(answer, schema)
        return {
            "result": answer,
            "sources": [s["url"] for s in source_details],
            "source_details": source_details,
        }
    finally:
        await scraper.close()
        await llm.close()


async def run_answer(
    query: str,
    num_sources: int = 5,
    search_type: str = "auto",
    retrieval_mode: str = "keyword",
    searxng_url: str = "http://searxng:8080",
    scraper_url: str = "http://scraper-svc:8001",
    semantic_url: str = "http://semantic-svc:8003",
    llm_base_url: str = "https://api.openai.com/v1",
    llm_api_key: str = "",
    llm_model: str | None = None,
    requested_model: str | None = None,
    max_searches_per_request: int = 5,
    output_schema: dict | None = None,
    citation_style: Any = None,
) -> dict:
    """Run a grounded Q&A pipeline: search → scrape → LLM → citations.

    Returns a dict with keys: answer, sources (list of dicts), citations (list of dicts),
    search_type, latency_ms.
    """
    start = time.monotonic()

    cs = (
        citation_style
        if isinstance(citation_style, CitationStyle)
        else CitationStyle.inline
    )

    searxng = SearXNGClient(searxng_url, max_searches=max_searches_per_request)
    scraper = ScraperClient(scraper_url)
    if llm_model is None:
        raise ValueError("llm_model is required — set via LLM_MODEL env var")
    effective_model = (
        requested_model
        if requested_model and requested_model != "default"
        else llm_model
    )
    llm = LLMClient(llm_base_url, llm_api_key, effective_model)

    try:
        discovered = await _run_answer_discover_and_scrape(
            query=query,
            num_sources=num_sources,
            retrieval_mode=retrieval_mode,
            searxng=searxng,
            scraper=scraper,
            semantic_url=semantic_url,
            llm_base_url=llm_base_url,
            llm_api_key=llm_api_key,
            llm_model=llm_model,
            requested_model=requested_model,
        )

        context = discovered["context"]
        source_map = discovered["source_map"]

        if not context:
            elapsed = int((time.monotonic() - start) * 1000)
            return {
                "answer": "I was unable to find or scrape any relevant web pages to answer your question.",
                "sources": [],
                "citations": [],
                "search_type": search_type,
                "latency_ms": elapsed,
            }

        # Call LLM — adjust user prompt based on citation style and schema
        if output_schema:
            user_prompt = (
                f"Answer the following question using ONLY the sources provided above.\n\n"
                f"Question: {query}\n\n"
                f"Cite sources using [1], [2], etc. corresponding to the source numbers above."
            )
            answer = await llm.generate(
                system_prompt=ANSWER_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                context=context,
                schema=output_schema,
            )
        else:
            user_prompt = _build_answer_user_prompt(query, cs)
            answer = await llm.generate(
                system_prompt=ANSWER_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                context=context,
            )

        # Apply citation style post-processing
        if not output_schema:
            answer, citations = _apply_citation_style(answer, source_map, cs)
        else:
            citations: list[dict] = []  # type: ignore[no-redef]
            # For structured output, collect source URLs but don't apply citation styles
            seen_indices: set[int] = set()
            for match in re.finditer(r"\[(\d+)\]", answer):
                idx = int(match.group(1))
                if idx not in seen_indices and 1 <= idx <= len(source_map):
                    seen_indices.add(idx)
                    citations.append({"index": idx, "url": source_map[idx - 1]["url"]})

        elapsed = int((time.monotonic() - start) * 1000)

        return {
            "answer": answer,
            "sources": source_map,
            "citations": citations,
            "search_type": search_type,
            "latency_ms": elapsed,
        }
    finally:
        await searxng.close()
        await scraper.close()
        await llm.close()


async def run_answer_stream(
    query: str,
    num_sources: int = 5,
    search_type: str = "auto",
    retrieval_mode: str = "keyword",
    searxng_url: str = "http://searxng:8080",
    scraper_url: str = "http://scraper-svc:8001",
    semantic_url: str = "http://semantic-svc:8003",
    llm_base_url: str = "https://api.openai.com/v1",
    llm_api_key: str = "",
    llm_model: str | None = None,
    requested_model: str | None = None,
    max_searches_per_request: int = 5,
    output_schema: dict | None = None,
    citation_style: Any = None,
) -> AsyncGenerator[dict[str, Any], None]:
    """Streaming version of run_answer. Yields SSE-suitable dicts.

    Yields:
      {"type": "sources", "sources": [...]} — source list (sent once before tokens)
      {"type": "token", "content": "..."} — individual tokens from the LLM
      {"type": "done", "answer": "...", "citations": [...], "latency_ms": N} — final
      {"type": "error", "content": "..."} — error
    """
    start = time.monotonic()

    cs = (
        citation_style
        if isinstance(citation_style, CitationStyle)
        else CitationStyle.inline
    )

    searxng = SearXNGClient(searxng_url, max_searches=max_searches_per_request)
    scraper = ScraperClient(scraper_url)
    if llm_model is None:
        raise ValueError("llm_model is required — set via LLM_MODEL env var")
    effective_model = (
        requested_model
        if requested_model and requested_model != "default"
        else llm_model
    )
    llm = LLMClient(llm_base_url, llm_api_key, effective_model)

    try:
        # Step 1: Search (fetch extra results to allow for scrape failures)
        logger.info("Answer (stream): searching for: %s", query)
        search_results, _health = await searxng.search(query, limit=num_sources * 2)

        if retrieval_mode != "keyword":
            from .rerank import _rerank_answer_sources

            search_results = await _rerank_answer_sources(
                search_results,
                query,
                retrieval_mode,
                semantic_url,
                scraper_url,
                num_sources,
            )
        target_urls = [r["url"] for r in search_results if r.get("url")]

        # Yield pending sources for progress visibility
        pending_sources = [
            {
                "url": r["url"],
                "title": r.get("title", ""),
                "relevance": r.get("description", ""),
            }
            for r in search_results
            if r.get("url")
        ]
        yield {"type": "sources_pending", "sources": pending_sources}

        # Step 2: Scrape (prefer text sources, use video platforms as fallback)
        from .scoring import _is_video_platform_url

        preferred = [u for u in target_urls if not _is_video_platform_url(u)]
        deprioritized = [u for u in target_urls if _is_video_platform_url(u)]

        if deprioritized:
            logger.info(
                "Answer (stream): %d preferred + %d video-platform URLs (deprioritized)",
                len(preferred),
                len(deprioritized),
            )

        documents, source_details = await _scrape_urls(
            preferred,
            scraper,
            min_sources=num_sources,
            max_attempts=num_sources * 2,
        )

        if len(documents) < num_sources and deprioritized:
            logger.info(
                "Answer (stream): %d/%d from preferred sources, falling back to video-platform URLs",
                len(documents),
                num_sources,
            )
            remaining = num_sources - len(documents)
            more_docs, more_details = await _scrape_urls(
                deprioritized,
                scraper,
                min_sources=remaining,
                max_attempts=remaining * 2,
            )
            documents.extend(more_docs)
            source_details.extend(more_details)

        # Step 3: Build context
        context_parts = []
        source_map: list[dict[str, str]] = []
        for i, (doc, detail) in enumerate(
            zip(documents, source_details, strict=False), start=1
        ):
            url = detail["url"]
            title = next(
                (r.get("title", "") for r in search_results if r.get("url") == url), ""
            )
            context_parts.append(f"[{i}] Source: {url}\nTitle: {title}\n\n{doc}")
            source_map.append(
                {
                    "url": url,
                    "title": title,
                    "relevance": next(
                        (
                            r.get("description", "")
                            for r in search_results
                            if r.get("url") == url
                        ),
                        "",
                    ),
                }
            )

        context = "\n\n---\n\n".join(context_parts) if context_parts else ""

        if not context:
            yield {"type": "sources", "sources": []}
            yield {
                "type": "done",
                "answer": "No relevant web pages found.",
                "citations": [],
                "latency_ms": int((time.monotonic() - start) * 1000),
            }
            return

        # Yield sources before streaming tokens
        yield {"type": "sources", "sources": source_map}

        # Step 4: Stream LLM response (or schema-based single call)
        if output_schema:
            user_prompt = (
                f"Answer the following question using ONLY the sources provided above.\n\n"
                f"Question: {query}\n\n"
                f"Cite sources using [1], [2], etc. corresponding to the source numbers above."
            )
            full_answer = await llm.generate(
                system_prompt=ANSWER_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                context=context,
                schema=output_schema,
            )
        else:
            user_prompt = _build_answer_user_prompt(query, cs)
            full_answer = ""
            async for event in llm.generate_stream(
                system_prompt=ANSWER_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                context=context,
            ):
                if event["type"] == "token":
                    full_answer += event["content"]
                    yield {"type": "token", "content": event["content"]}
                elif event["type"] == "error":
                    yield {"type": "error", "content": event["content"]}
                    return
                elif event["type"] == "done":
                    full_answer = event["full_content"]

        # Step 5: Apply citation style post-processing
        if not output_schema:
            full_answer, citations = _apply_citation_style(full_answer, source_map, cs)
        else:
            citations: list[dict] = []  # type: ignore[no-redef]
            seen_indices: set[int] = set()
            for match in re.finditer(r"\[(\d+)\]", full_answer):
                idx = int(match.group(1))
                if idx not in seen_indices and 1 <= idx <= len(source_map):
                    seen_indices.add(idx)
                    citations.append({"index": idx, "url": source_map[idx - 1]["url"]})

        elapsed = int((time.monotonic() - start) * 1000)
        yield {
            "type": "done",
            "answer": full_answer,
            "citations": citations,
            "latency_ms": elapsed,
        }

    finally:
        await searxng.close()
        await scraper.close()
        await llm.close()
