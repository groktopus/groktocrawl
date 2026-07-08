"""Search functions: deep_search, rich_search, and search_stream."""

import asyncio
import contextlib
import json
import logging
import time
from typing import Any

from ..llm import LLMClient
from ..scraper_client import ScraperClient
from ..searxng_client import SearXNGClient
from .prompts import DEEP_SEARCH_GAP_PROMPT, RICH_SEARCH_SYSTEM_PROMPT

logger = logging.getLogger(__name__)


async def run_deep_search(
    query: str,
    limit: int,
    searxng_url: str = "http://searxng:8080",
    llm_base_url: str = "https://api.openai.com/v1",
    llm_api_key: str = "",
    llm_model: str | None = None,
) -> dict:
    """Multi-pass search with LLM gap analysis and follow-up queries.

    Algorithm:
    1. Initial SearXNG search with primary query
    2. LLM evaluates result titles/descriptions for coverage gaps
    3. Formulates 2-4 follow-up queries targeting identified gaps
    4. Runs all follow-up queries in parallel via asyncio.gather
    5. Merges all results, URL-dedup (first occurrence wins)
    6. Returns SearchResult list + query_variations
    """
    if llm_model is None:
        raise ValueError("llm_model is required — set via LLM_MODEL env var")
    searxng = SearXNGClient(searxng_url)
    llm = LLMClient(llm_base_url, llm_api_key, llm_model)

    try:
        # 1. Initial search
        results, _health = await searxng.search(query, limit=max(limit, 5))

        if not results:
            return {"results": [], "query_variations": [query]}

        # 2. Gap analysis via LLM
        # Build context from result URLs, titles, descriptions (no scraping)
        result_summaries = [
            {
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "description": r.get("description", ""),
            }
            for r in results[:limit]
        ]
        context = json.dumps(result_summaries)

        gap_prompt = (
            f'Search query: "{query}"\n\n'
            f"Current search results:\n{context}\n\n"
            f"What sub-topics, angles, or specific aspects are NOT covered by these results? "
            f"Suggest 2-4 additional search queries that would fill these gaps. "
            f"Return ONLY a JSON array of query strings. No other text."
        )

        follow_ups: list[str] = []
        try:
            gap_result = await llm.generate(
                system_prompt=DEEP_SEARCH_GAP_PROMPT,
                user_prompt=gap_prompt,
            )
            if gap_result and not gap_result.startswith("Error:"):
                # Parse the JSON array from the LLM response
                cleaned = gap_result.strip()
                cleaned = cleaned.removeprefix("```json")
                cleaned = cleaned.removeprefix("```")
                cleaned = cleaned.removesuffix("```")
                parsed = json.loads(cleaned)
                if isinstance(parsed, list):
                    follow_ups = [str(q) for q in parsed if q and str(q).strip()]
        except (json.JSONDecodeError, Exception) as e:
            logger.warning("Deep search gap analysis failed: %s", e)

        # 3. Run all queries (original + follow-ups) in parallel
        all_queries = [query, *follow_ups]

        async def search_one(q: str) -> list[dict]:
            r, _ = await searxng.search(q, limit=limit)
            return r

        all_result_lists = [results]
        if follow_ups:
            follow_up_results = await asyncio.gather(
                *[search_one(q) for q in follow_ups], return_exceptions=True
            )
            for fr in follow_up_results:
                if isinstance(fr, list):
                    all_result_lists.append(fr)
                elif isinstance(fr, Exception):
                    logger.warning("Follow-up search failed: %s", fr)

        # 4. Merge and URL-dedup (first occurrence wins)
        from ..models import SearchResult

        seen_urls: set[str] = set()
        merged: list[SearchResult] = []
        for result_list in all_result_lists:
            for r in result_list:
                url = r.get("url", "")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    merged.append(
                        SearchResult(
                            url=url,
                            title=r.get("title", ""),
                            description=r.get("description", ""),
                        )
                    )

        logger.info(
            "Deep search: %d unique results from %d queries for: %s",
            len(merged),
            len(all_queries),
            query,
        )

        return {
            "results": merged[:limit],
            "query_variations": all_queries,
        }

    finally:
        await searxng.close()
        await llm.close()


async def run_rich_search(
    search_results: list[dict],
    query: str,
    limit: int = 5,
    output_schema: dict[str, Any] | None = None,
    system_prompt: str | None = None,
    scraper_url: str = "http://scraper-svc:8001",
    llm_base_url: str = "https://api.openai.com/v1",
    llm_api_key: str = "",
    llm_model: str | None = None,
) -> dict[str, Any] | None:
    """Enrich search results with scraped content and optional structured extraction.

    Returns dict with keys: content (structured data) and grounding (citations),
    or None if no results could be enriched.
    """
    start = time.monotonic()
    if llm_model is None:
        raise ValueError("llm_model is required — set via LLM_MODEL env var")
    scraper = ScraperClient(scraper_url)
    llm = LLMClient(llm_base_url, llm_api_key, llm_model)

    try:
        top_results = search_results[:limit]

        # Scrape top results
        enriched = []
        for r in top_results:
            url = r.get("url", "")
            if not url:
                continue
            try:
                resp = await scraper.scrape(url)
                if resp.get("success") and resp.get("data", {}).get("markdown"):
                    content = resp["data"]["markdown"][:3000]  # Trim to 3K chars
                    enriched.append(
                        {
                            "url": url,
                            "title": r.get("title", ""),
                            "content": content,
                        }
                    )
                else:
                    enriched.append(
                        {
                            "url": url,
                            "title": r.get("title", ""),
                            "content": r.get("description", ""),
                        }
                    )
            except Exception:
                enriched.append(
                    {
                        "url": url,
                        "title": r.get("title", ""),
                        "content": r.get("description", ""),
                    }
                )

        if not enriched:
            return None

        # Build context for LLM
        context_parts = []
        for i, item in enumerate(enriched, start=1):
            context_parts.append(
                f"[{i}] URL: {item['url']}\nTitle: {item['title']}\nContent: {item['content']}"
            )

        context = "\n\n---\n\n".join(context_parts)

        # Build prompt
        prompt_parts = [
            f"Search query: {query}",
            f"\nSearch results with full content:\n\n{context}",
        ]

        if output_schema:
            schema_json = json.dumps(output_schema, indent=2)
            prompt_parts.append(
                f"\nExtract structured data matching this JSON Schema:\n```json\n{schema_json}\n```"
            )

        prompt = "\n".join(prompt_parts)

        # LLM call for enrichment or structured extraction
        effective_system = system_prompt or RICH_SEARCH_SYSTEM_PROMPT
        content = await llm.generate(
            system_prompt=effective_system,
            user_prompt=prompt,
        )

        result: dict[str, Any] = {}

        if output_schema:
            # Try to parse structured JSON from the response
            try:
                parsed = json.loads(content)
                result["content"] = parsed
            except json.JSONDecodeError:
                # Extract JSON block if wrapped in markdown
                if "```json" in content:
                    block = content.split("```json")[1].split("```")[0].strip()
                    try:
                        result["content"] = json.loads(block)
                    except json.JSONDecodeError:
                        result["content"] = content
                else:
                    result["content"] = content

            # Build grounding citations
            grounding = []
            for item in enriched:
                grounding.append(
                    {
                        "url": item["url"],
                        "title": item["title"],
                    }
                )
            result["grounding"] = grounding
        else:
            # Enrichment mode: parse the improved descriptions
            result["content"] = content
            result["grounding"] = [
                {"url": item["url"], "title": item["title"]} for item in enriched
            ]

        elapsed = int((time.monotonic() - start) * 1000)
        logger.info("Rich search completed in %dms for query: %s", elapsed, query)

        return result

    finally:
        await scraper.close()
        await llm.close()


async def run_search_stream(
    query: str,
    limit: int = 5,
    search_type: str = "fast",
    retrieval_mode: str = "keyword",
    categories: list[str] | None = None,
    sources: list[str] | None = None,
    output_schema: dict[str, Any] | None = None,
    system_prompt: str | None = None,
    searxng_url: str = "http://searxng:8080",
    scraper_url: str = "http://scraper-svc:8001",
    semantic_url: str = "http://semantic-svc:8003",
    llm_base_url: str = "https://api.openai.com/v1",
    llm_api_key: str = "",
    llm_model: str | None = None,
    max_searches_per_request: int = 5,
):
    """Streaming version of /v2/search. Yields SSE-suitable dicts.

    Phase 1: Search via SearXNG (or Qdrant for vector modes).
    Phase 2 (rich only): Scrape top results + LLM synthesis.

    Yields:
      {"type": "search_result", "result": {"url":"...","title":"...","description":"..."}}
      {"type": "scrape_result", "url": "...", "contents": {"markdown": "..."}}  (rich only)
      {"type": "token", "content": "..."}  (rich only, no output_schema)
      {"type": "done", "total_results": N, "latency_ms": N, "output": ...}
      {"type": "error", "content": "..."}
    """
    start = time.monotonic()
    if llm_model is None:
        raise ValueError("llm_model is required — set via LLM_MODEL env var")

    searxng = SearXNGClient(searxng_url, max_searches=max_searches_per_request)
    scraper = ScraperClient(scraper_url)
    llm = LLMClient(llm_base_url, llm_api_key, llm_model)

    try:
        # ── Phase 1: Search ──────────────────────────────────────
        if retrieval_mode == "vector":
            from ..semantic_client import SemanticClient

            semantic = SemanticClient(semantic_url)
            try:
                vector_results = await semantic.search_vector(query, limit=limit)
                search_results = [
                    {"url": r["url"], "title": r["title"], "description": ""}
                    for r in vector_results
                ]
            finally:
                await semantic.close()

        elif retrieval_mode == "hybrid_vector":
            from ..semantic_client import SemanticClient

            semantic = SemanticClient(semantic_url)
            try:
                searxng_results, _health = await searxng.search(
                    query, limit=limit, categories=categories, sources=sources
                )
                vector_results = await semantic.search_vector(query, limit=limit)

                seen: set[str] = set()
                merged: list[dict] = []
                for r in searxng_results:
                    if r["url"] not in seen:
                        seen.add(r["url"])
                        merged.append(r)
                for r in vector_results:
                    if r["url"] not in seen:
                        seen.add(r["url"])
                        merged.append(
                            {"url": r["url"], "title": r["title"], "description": ""}
                        )
                search_results = merged[:limit]
            finally:
                await semantic.close()

        else:
            # Keyword, semantic, hybrid: standard SearXNG path
            results, _health = await searxng.search(
                query, limit=limit, categories=categories, sources=sources
            )
            search_results = results

        # ── Yield search_result events ──────────────────────────
        for r in search_results:
            yield {
                "type": "search_result",
                "result": {
                    "url": r.get("url", ""),
                    "title": r.get("title", ""),
                    "description": r.get("description", ""),
                },
            }

        total_results = len(search_results)

        # Semantic/hybrid reranking for keyword results
        if retrieval_mode in ("semantic", "hybrid") and search_results:
            from ..semantic_client import SemanticClient

            semantic = SemanticClient(semantic_url)
            scraper_rerank = ScraperClient(scraper_url)
            try:
                urls_to_scrape = [r["url"] for r in search_results[:limit]]
                contents = []
                for url in urls_to_scrape:
                    try:
                        scraped = await scraper_rerank.scrape(url)
                        content = (
                            scraped.get("data", {}).get("markdown", "")
                            if scraped.get("success")
                            else ""
                        )
                        contents.append(content[:2000])
                    except Exception:
                        contents.append("")

                if retrieval_mode == "hybrid":
                    reranked = await semantic.rerank(
                        query,
                        [r.get("description", "") for r in search_results[:limit]],
                        top_k=limit,
                    )
                    new_order = [item["index"] for item in reranked]
                    search_results = [
                        search_results[i] for i in new_order if i < len(search_results)
                    ]
                else:
                    embeddings = await semantic.embed([query, *contents])
                    query_em = embeddings[0]
                    similarities = [
                        sum(a * b for a, b in zip(query_em, doc_em, strict=False))
                        for doc_em in embeddings[1:]
                    ]
                    ranked_indices = sorted(
                        range(len(similarities)),
                        key=lambda i: similarities[i],
                        reverse=True,
                    )
                    search_results = [
                        search_results[i]
                        for i in ranked_indices
                        if i < len(search_results)
                    ]
            finally:
                await semantic.close()
                await scraper_rerank.close()

        # ── Phase 2: Rich enrichment (scrape + LLM) ─────────────
        output = None
        if search_type == "rich" and search_results:
            top_results = search_results[:limit]

            # Scrape each result, yield scrape_result events
            enriched: list[dict] = []
            semaphore = asyncio.Semaphore(3)
            queue: asyncio.Queue = asyncio.Queue()

            async def _scrape_and_queue(item: dict) -> None:
                url = item.get("url", "")
                if not url:
                    await queue.put(None)
                    return
                async with semaphore:
                    try:
                        resp = await asyncio.wait_for(scraper.scrape(url), timeout=20)
                        if resp.get("success") and resp.get("data", {}).get("markdown"):
                            md = resp["data"]["markdown"][:3000]
                            await queue.put(
                                {
                                    "scrape_event": {
                                        "type": "scrape_result",
                                        "url": url,
                                        "contents": {"markdown": md},
                                    },
                                    "enriched": {
                                        "url": url,
                                        "title": item.get("title", ""),
                                        "content": md,
                                    },
                                }
                            )
                            return
                    except Exception:
                        pass
                    await queue.put(
                        {
                            "enriched": {
                                "url": url,
                                "title": item.get("title", ""),
                                "content": item.get("description", ""),
                            },
                        }
                    )

            tasks = [asyncio.create_task(_scrape_and_queue(r)) for r in top_results]

            completed_count = 0
            while completed_count < len(tasks):
                item = await queue.get()
                completed_count += 1
                if item is None:
                    continue
                if "scrape_event" in item:
                    yield item["scrape_event"]
                enriched.append(item["enriched"])

            # Wait for any remaining tasks to settle
            await asyncio.gather(*tasks, return_exceptions=True)

            if enriched:
                # Build context for LLM
                context_parts = []
                for i, item in enumerate(enriched, start=1):
                    context_parts.append(
                        f"[{i}] URL: {item['url']}\nTitle: {item['title']}\n"
                        f"Content: {item['content']}"
                    )
                context = "\n\n---\n\n".join(context_parts)

                if output_schema:
                    # Structured extraction: single LLM call
                    prompt_parts = [
                        f"Search query: {query}",
                        f"\nSearch results with full content:\n\n{context}",
                        f"\nExtract structured data matching this JSON Schema:\n"
                        f"```json\n{json.dumps(output_schema, indent=2)}\n```",
                    ]
                    prompt = "\n".join(prompt_parts)
                    effective_system = system_prompt or RICH_SEARCH_SYSTEM_PROMPT
                    content = await llm.generate(
                        system_prompt=effective_system,
                        user_prompt=prompt,
                    )

                    parsed_output: Any = content
                    try:
                        parsed_output = json.loads(content)
                    except json.JSONDecodeError:
                        if "```json" in content:
                            block = content.split("```json")[1].split("```")[0].strip()
                            with contextlib.suppress(json.JSONDecodeError):
                                parsed_output = json.loads(block)

                    output = {
                        "content": parsed_output,
                        "grounding": [
                            {"url": item["url"], "title": item["title"]}
                            for item in enriched
                        ],
                    }
                else:
                    # Enrichment mode: stream LLM tokens
                    prompt_parts = [
                        f"Search query: {query}",
                        f"\nSearch results with full content:\n\n{context}",
                    ]
                    prompt = "\n".join(prompt_parts)
                    effective_system = system_prompt or RICH_SEARCH_SYSTEM_PROMPT

                    full_result = ""
                    async for event in llm.generate_stream(
                        system_prompt=effective_system,
                        user_prompt=prompt,
                    ):
                        if event["type"] == "token":
                            full_result += event["content"]
                            yield {"type": "token", "content": event["content"]}
                        elif event["type"] == "error":
                            yield {"type": "error", "content": event["content"]}
                            break
                        elif event["type"] == "done":
                            full_result = event["full_content"]

                    output = {
                        "content": full_result,
                        "grounding": [
                            {"url": item["url"], "title": item["title"]}
                            for item in enriched
                        ],
                    }

        elapsed = int((time.monotonic() - start) * 1000)
        done_event: dict[str, Any] = {
            "type": "done",
            "total_results": total_results,
            "latency_ms": elapsed,
        }
        if output is not None:
            done_event["output"] = output
        yield done_event

    finally:
        await searxng.close()
        await scraper.close()
        await llm.close()
