"""Main research loops: run_research, run_research_stream, run_answer,
run_answer_stream, run_extract."""

import asyncio
import contextlib
import logging
import re
import time
from collections.abc import AsyncGenerator
from typing import Any

from common.stage_metrics import StreamTiming, observe_elapsed

from ..exceptions import (
    ProviderOutputError,
    RetryableRateLimitError,
    StructuredOutputError,
)
from ..llm import LLMClient
from ..models import CitationStyle
from ..scraper_client import ScraperClient
from ..searxng_client import SearXNGClient
from .citations import _apply_citation_style, _build_answer_user_prompt
from .discovery import (
    _build_answer_context,
    _run_answer_discover_and_scrape,
    _run_multi_query_discover_and_scrape,
    _run_research_discover_and_scrape,
    _scrape_answer_sources,
    _scrape_urls,
)
from .events import ResearchEvent
from .gaps import _detect_gaps
from .plan import _generate_research_plan
from .prompts import ANSWER_SYSTEM_PROMPT, EXTRACT_SYSTEM_PROMPT, SYSTEM_PROMPT
from .sources import (
    SourceArtifact,
    SourceRegistry,
    artifacts_to_documents_and_details,
)
from .utils import _validate_json_if_schema

logger = logging.getLogger(__name__)


async def _discover_with_progress(factory, initial_pending=None):
    """Run discovery while forwarding search and acquisition events immediately."""
    progress: asyncio.Queue[ResearchEvent] = asyncio.Queue()

    async def on_artifact(artifact: SourceArtifact) -> None:
        await progress.put(
            {
                "type": "source_scraped",
                "url": artifact.url,
                "source": artifact.source,
                "chars": artifact.char_count,
            }
        )

    async def on_search_results(results) -> None:
        await progress.put(
            {
                "type": "sources_pending",
                "sources": [
                    {
                        "url": result["url"],
                        "title": result.get("title", ""),
                        "relevance": result.get("description", ""),
                    }
                    for result in results
                    if result.get("url")
                ],
            }
        )

    if initial_pending:
        await progress.put({"type": "sources_pending", "sources": initial_pending})

    task = asyncio.create_task(factory(on_artifact, on_search_results))
    event_task: asyncio.Task | None = None
    try:
        while not task.done():
            event_task = asyncio.create_task(progress.get())
            done, _ = await asyncio.wait(
                {task, event_task}, return_when=asyncio.FIRST_COMPLETED
            )
            if event_task in done:
                yield event_task.result()
                event_task = None
            else:
                event_task.cancel()
                await asyncio.gather(event_task, return_exceptions=True)
                event_task = None
        discovered = await task
        while not progress.empty():
            yield progress.get_nowait()
        yield {"type": "_discovery_complete", "result": discovered}
    finally:
        if event_task is not None and not event_task.done():
            event_task.cancel()
            await asyncio.gather(event_task, return_exceptions=True)
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


def _research_error_event(event: dict[str, Any]) -> ResearchEvent:
    """Preserve bounded LLM error metadata in the research event contract."""
    result: ResearchEvent = {
        "type": "error",
        "content": str(event.get("content", "LLM provider failed")),
    }
    classification = event.get("classification")
    if isinstance(classification, str):
        result["classification"] = classification
    retry_after = event.get("retry_after_seconds")
    if isinstance(retry_after, int | float) and not isinstance(retry_after, bool):
        result["retry_after_seconds"] = float(retry_after)
    return result


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
    max_credits: int | None = None,
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
        source_registry = SourceRegistry()
        all_source_details: list[dict] = []
        credits_used = 0
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

                    async def discover_pass_one_multi(
                        on_artifact,
                        on_search_results,
                        _queries=queries,
                        _urls=urls,
                        _pass_count=pass_count,
                    ):
                        return await _run_multi_query_discover_and_scrape(
                            queries=_queries,
                            urls=_urls,
                            searxng=searxng,
                            scraper=scraper,
                            max_searches_per_request=max_searches_per_request,
                            scrape_options=scrape_opts,
                            max_credits=max_credits,
                            source_registry=source_registry,
                            pass_number=_pass_count,
                            on_artifact=on_artifact,
                            on_search_results=on_search_results,
                        )
                else:
                    query = queries[0] if queries else prompt

                    async def discover_pass_one_single(
                        on_artifact,
                        on_search_results,
                        _query=query,
                        _urls=urls,
                        _pass_count=pass_count,
                    ):
                        return await _run_research_discover_and_scrape(
                            prompt=_query,
                            urls=_urls,
                            searxng=searxng,
                            scraper=scraper,
                            scrape_options=scrape_opts,
                            max_credits=max_credits,
                            source_registry=source_registry,
                            pass_number=_pass_count,
                            on_artifact=on_artifact,
                            on_search_results=on_search_results,
                        )
            else:
                # ── Pass 2: gap-focused discovery ─────────────────
                async def discover_pass_two(
                    on_artifact,
                    on_search_results,
                    _gap_topics=gap_topics,
                    _pass_count=pass_count,
                    _credits_used=credits_used,
                ):
                    return await _run_multi_query_discover_and_scrape(
                        queries=_gap_topics,
                        urls=None,
                        searxng=searxng,
                        scraper=scraper,
                        max_searches_per_request=min(
                            len(_gap_topics), max_searches_per_request
                        ),
                        scrape_options=scrape_opts,
                        max_credits=(
                            max_credits - _credits_used
                            if max_credits is not None
                            else None
                        ),
                        source_registry=source_registry,
                        pass_number=_pass_count,
                        on_artifact=on_artifact,
                        on_search_results=on_search_results,
                    )

            discovery_factory = (
                discover_pass_two
                if pass_count > 1
                else (
                    discover_pass_one_multi
                    if strategy == "deep" and len(queries) > 1
                    else discover_pass_one_single
                )
            )
            discovered = None
            initial_pending = (
                [{"url": url, "title": "", "relevance": ""} for url in urls]
                if urls and pass_count == 1
                else None
            )
            async with contextlib.aclosing(
                _discover_with_progress(discovery_factory, initial_pending)
            ) as discovery_events:
                async for progress_event in discovery_events:
                    if progress_event["type"] == "_discovery_complete":
                        discovered = progress_event["result"]
                    else:
                        yield progress_event
            if discovered is None:
                raise RuntimeError("Discovery ended without a result")

            context = discovered["context"]
            source_details = discovered["source_details"]
            novel_artifacts = discovered.get("new_artifacts", [])
            previous_context = combined_context
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

            all_source_details = list(source_details)
            credits_used += discovered.get("credits_used", len(novel_artifacts))
            combined_context = context

            # A duplicate-only gap pass leaves the final evidence unchanged.
            if pass_count > 1 and context == previous_context:
                break

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

            # Coverage depends only on evidence, so decide follow-up work
            # before the one final synthesis in every response mode.
            if pass_count == 1:
                # Count novel successful acquisitions; reuse remains free.
                budget_spent = max_credits is not None and credits_used >= max_credits
                gap_topics = (
                    []
                    if budget_spent
                    else await _detect_gaps(
                        combined_context, llm, original_query=prompt
                    )
                )
                if not gap_topics:
                    break  # Coverage is adequate, done
                max_passes = 2  # Enable second pass

        yield {"type": "status", "state": "synthesizing"}
        if schema or not stream_tokens:
            try:
                answer = await llm.generate(
                    system_prompt=SYSTEM_PROMPT,
                    user_prompt=prompt,
                    context=combined_context,
                    schema=schema,
                    stage="synthesis",
                )
            except RetryableRateLimitError as exc:
                if stream_tokens:
                    yield {
                        "type": "error",
                        "classification": "retryable",
                        "retry_after_seconds": exc.retry_after_seconds,
                        "content": exc.detail,
                    }
                    return
                raise
            except ProviderOutputError as exc:
                if stream_tokens:
                    yield {
                        "type": "error",
                        "classification": "non_retryable",
                        "content": exc.detail,
                    }
                    return
                raise
            try:
                _validate_json_if_schema(answer, schema)
            except StructuredOutputError as exc:
                if stream_tokens:
                    yield {
                        "type": "error",
                        "classification": "non_retryable",
                        "content": exc.detail,
                    }
                    return
                raise
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
                stage="synthesis",
            ):
                if event["type"] == "token":
                    answer += event["content"]
                    yield {"type": "token", "content": event["content"]}
                elif event["type"] == "error":
                    yield _research_error_event(event)
                    return
                elif event["type"] == "done":
                    answer = event["full_content"]

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
        observe_elapsed(
            "groktocrawl_research_total_seconds",
            "Total research pipeline latency by search type",
            {"search_type": search_type},
            start,
        )
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
    max_credits: int | None = None,
    include_images: bool = False,
    citation_style: Any = None,
    search_type: str = "deep",
) -> dict:
    """Consume the canonical research event stream and return its terminal result."""
    async with contextlib.aclosing(
        _run_research_events(
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
            max_credits,
            include_images,
            citation_style,
            search_type,
        )
    ) as research_events:
        async for event in research_events:
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
    max_credits: int | None = None,
    include_images: bool = False,
    citation_style: Any = None,
    search_type: str = "deep",
) -> AsyncGenerator[ResearchEvent, None]:
    """Expose events from the canonical research engine for SSE adaptation."""
    async with contextlib.aclosing(
        _run_research_events(
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
            max_credits,
            include_images,
            citation_style,
            search_type,
            stream_tokens=True,
        )
    ) as research_events:
        async for event in research_events:
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
        artifacts = await _scrape_urls(urls, scraper)
        documents, source_details = artifacts_to_documents_and_details(artifacts)
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
            stage="extract",
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
                stage="answer",
            )
            _validate_json_if_schema(answer, output_schema)
        else:
            user_prompt = _build_answer_user_prompt(query, cs)
            answer = await llm.generate(
                system_prompt=ANSWER_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                context=context,
                stage="answer",
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
    timing = StreamTiming("answer")

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
        if retrieval_mode == "hybrid_vector":
            # Defer web search to the hybrid planner (concurrent web+vector).
            search_results: list[dict] = []
        else:
            search_results, _health = await searxng.search(
                query, limit=num_sources * 2, raise_on_rate_limit=True
            )

        rerank_artifacts: list[SourceArtifact] = []
        if retrieval_mode != "keyword":
            from .rerank import _rerank_answer_sources

            search_results, rerank_artifacts = await _rerank_answer_sources(
                search_results,
                query,
                retrieval_mode,
                semantic_url,
                scraper_url,
                num_sources,
                searxng=searxng,
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
        timing.on_first_event()
        yield {"type": "sources_pending", "sources": pending_sources}

        # Step 2: Scrape only missing content, reusing rerank artifacts
        artifacts = await _scrape_answer_sources(
            target_urls, rerank_artifacts, scraper, num_sources
        )

        # Step 3: Build context + citation source map from artifacts
        built = _build_answer_context(search_results, artifacts)
        context = built["context"]
        source_map = built["source_map"]

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
            try:
                full_answer = await llm.generate(
                    system_prompt=ANSWER_SYSTEM_PROMPT,
                    user_prompt=user_prompt,
                    context=context,
                    schema=output_schema,
                    stage="answer",
                )
            except RetryableRateLimitError as exc:
                yield {
                    "type": "error",
                    "classification": "retryable",
                    "retry_after_seconds": exc.retry_after_seconds,
                    "content": exc.detail,
                }
                return
            except ProviderOutputError as exc:
                yield {
                    "type": "error",
                    "classification": "non_retryable",
                    "content": exc.detail,
                }
                return
            try:
                _validate_json_if_schema(full_answer, output_schema)
            except StructuredOutputError as exc:
                yield {
                    "type": "error",
                    "classification": "non_retryable",
                    "content": exc.detail,
                }
                return
        else:
            user_prompt = _build_answer_user_prompt(query, cs)
            full_answer = ""
            async for event in llm.generate_stream(
                system_prompt=ANSWER_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                context=context,
                stage="answer",
            ):
                if event["type"] == "token":
                    timing.on_first_token()
                    full_answer += event["content"]
                    yield {"type": "token", "content": event["content"]}
                elif event["type"] == "error":
                    yield dict(_research_error_event(event))
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
