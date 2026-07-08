"""Search route handlers — v1 and v2 search endpoints."""

import json
import logging
import re
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from ..models import (
    ImageSearchResult,
    SearchRequest,
    SearchResponse,
    SearchResult,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/v1/search")
async def search_v1(request: Request, body: SearchRequest) -> dict[str, Any]:
    """Firecrawl v1-compatible search endpoint.

    Returns a flat data array (v1 format) rather than the nested
    data.web / data.images / data.news structure used by v2.
    """
    from ..searxng_client import SearXNGClient

    searxng = SearXNGClient(request.app.state.searxng_url)
    try:
        results, _health = await searxng.search(
            body.query,
            limit=body.limit,
            categories=body.categories,
            sources=body.sources,
        )
        return {
            "success": True,
            "data": [
                {
                    "url": r["url"],
                    "title": r["title"],
                    "description": r.get("description", ""),
                }
                for r in results
            ],
        }
    finally:
        await searxng.close()


@router.post("/v2/search", response_model=SearchResponse)
async def search(request: Request, body: SearchRequest) -> SearchResponse:
    if body.stream:

        async def event_stream():
            from ..research import run_search_stream

            async for event in run_search_stream(
                query=body.query,
                limit=body.limit,
                search_type=body.search_type,
                retrieval_mode=body.retrieval_mode,
                categories=body.categories,
                sources=body.sources,
                output_schema=body.output_schema,
                system_prompt=body.system_prompt,
                searxng_url=request.app.state.searxng_url,
                scraper_url=request.app.state.scraper_url,
                semantic_url=request.app.state.semantic_url,
                llm_base_url=request.app.state.llm_base_url,
                llm_api_key=request.app.state.llm_api_key,
                llm_model=request.app.state.llm_model,
            ):
                yield f"data: {json.dumps(event)}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(event_stream(), media_type="text/event-stream")  # type: ignore[return-value]

    from ..searxng_client import SearXNGClient

    searxng = SearXNGClient(request.app.state.searxng_url)
    warning_msg: str | None = None

    try:
        # ── Determine which source types to query ──────────────────
        has_image_source = body.sources and "images" in body.sources
        has_non_image_sources = body.sources and any(
            s != "images" for s in body.sources
        )
        image_only = has_image_source and not has_non_image_sources

        # ── Image search (when sources includes "images") ─────────
        image_results: list[ImageSearchResult] = []
        if has_image_source:
            image_query_results, _img_health = await searxng.search(
                body.query,
                limit=body.limit,
                sources=["images"],
            )
            for pos, item in enumerate(image_query_results):
                resolution_str = item.get("description", "")
                width = None
                height = None
                # Try to parse resolution like "800 × 600" or "800x600"
                res_match = (
                    re.match(r"(\d+)\s*[×x]\s*(\d+)", resolution_str)
                    if resolution_str
                    else None
                )
                if res_match:
                    width = int(res_match.group(1))
                    height = int(res_match.group(2))
                image_results.append(
                    ImageSearchResult(
                        title=item.get("title", ""),
                        image_url=item.get("url", ""),
                        image_width=width,
                        image_height=height,
                        url=item.get("url", ""),
                        position=pos + 1,
                    )
                )

        if image_only:
            image_data_result: dict[str, list] = {
                "web": [],
                "images": [r.model_dump() for r in image_results],
                "news": [],
            }
            return SearchResponse(data=image_data_result)

        # ── Non-image sources: standard SearXNG path ──────────────
        # Determine effective sources/categories for the main query
        effective_sources = (
            [s for s in body.sources if s != "images"]
            if body.sources and has_image_source
            else body.sources
        )

        # Deep mode: multi-pass search with gap analysis and follow-up queries
        if body.search_type == "deep":
            from ..research import run_deep_search

            deep_result = await run_deep_search(
                query=body.query,
                limit=body.limit,
                searxng_url=request.app.state.searxng_url,
                llm_base_url=request.app.state.llm_base_url,
                llm_api_key=request.app.state.llm_api_key,
                llm_model=request.app.state.llm_model,
            )
            search_results = deep_result["results"]
            deep_data: dict[str, list] = {
                "web": search_results,
                "images": [],
                "news": [],
            }
            return SearchResponse(
                data=deep_data, query_variations=deep_result.get("query_variations", [])
            )

        # Vector-only mode: query Qdrant, no SearXNG
        if body.retrieval_mode == "vector":
            from ..semantic_client import SemanticClient

            semantic = SemanticClient(request.app.state.semantic_url)
            try:
                vector_results = await semantic.search_vector(
                    body.query, limit=body.limit
                )
                search_results = [
                    SearchResult(url=r["url"], title=r["title"], description="")
                    for r in vector_results
                ]
            finally:
                await semantic.close()

        # Hybrid vector mode: SearXNG + Qdrant in parallel, merge, dedup
        elif body.retrieval_mode == "hybrid_vector":
            from ..semantic_client import SemanticClient

            semantic = SemanticClient(request.app.state.semantic_url)
            try:
                # Fetch SearXNG results first
                searxng_results, _health = await searxng.search(
                    body.query,
                    limit=body.limit,
                    categories=body.categories,
                    sources=effective_sources,
                )
                if not searxng_results and _health.engines_responding == 0:
                    warning_msg = (
                        "All search engines returned no results. "
                        "Check your BRAVE_API_KEY configuration."
                    )
                # Query vector index in parallel (async would be better, but sequential for now)
                vector_results = await semantic.search_vector(
                    body.query, limit=body.limit
                )

                # Convert both to SearchResult lists
                kw_results = [
                    SearchResult(
                        url=r["url"],
                        title=r["title"],
                        description=r.get("description", ""),
                    )
                    for r in searxng_results
                ]
                vec_results = [
                    SearchResult(url=r["url"], title=r["title"], description="")
                    for r in vector_results
                ]

                # Merge and dedup by URL (keep first occurrence — SearXNG has richer metadata)
                seen: set[str] = set()
                merged: list[SearchResult] = []
                for r in kw_results + vec_results:
                    if r.url not in seen:
                        seen.add(r.url)
                        merged.append(r)

                search_results = merged[: body.limit]
            finally:
                await semantic.close()

        else:
            # Keyword, semantic, hybrid: standard SearXNG path
            results, _health = await searxng.search(
                body.query,
                limit=body.limit,
                categories=body.categories,
                sources=effective_sources,
            )
            if not results and _health.engines_responding == 0:
                warning_msg = (
                    "All search engines returned no results. "
                    "Check your BRAVE_API_KEY configuration."
                )
            search_results = [
                SearchResult(
                    url=r["url"], title=r["title"], description=r.get("description", "")
                )
                for r in results
            ]

        # Semantic/hybrid retrieval: rerank results by embedding similarity
        if body.retrieval_mode in ("semantic", "hybrid") and results:
            from ..scraper_client import ScraperClient
            from ..semantic_client import SemanticClient

            semantic = SemanticClient(request.app.state.semantic_url)
            scraper = ScraperClient(request.app.state.scraper_url)
            try:
                # Scrape content for top results
                urls_to_scrape = [r["url"] for r in results[: body.limit]]
                contents = []
                for url in urls_to_scrape:
                    try:
                        scraped = await scraper.scrape(url)
                        content = (
                            scraped.get("data", {}).get("markdown", "")
                            if scraped.get("success")
                            else ""
                        )
                        contents.append(content[:2000])  # Truncate for embedding
                    except Exception:
                        contents.append("")

                # Embed query + document contents
                texts = [body.query, *contents]
                embeddings = await semantic.embed(texts)
                query_embedding = embeddings[0]
                doc_embeddings = embeddings[1:]

                if body.retrieval_mode == "hybrid":
                    # Cross-encoder reranker for merged keyword+semantic scoring
                    reranked = await semantic.rerank(
                        body.query,
                        [r.description for r in search_results[: body.limit]],
                        top_k=body.limit,
                    )
                    # Reorder by cross-encoder scores
                    new_order = [item["index"] for item in reranked]
                    search_results = [
                        search_results[i] for i in new_order if i < len(search_results)
                    ]
                else:
                    # Cosine similarity reranking (vectors are L2-normalized, so cosine = dot product)
                    similarities = [
                        sum(
                            a * b
                            for a, b in zip(query_embedding, doc_emb, strict=False)
                        )
                        for doc_emb in doc_embeddings
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
                await scraper.close()

        # Route results to the correct top-level key based on sources filter
        result_data: dict[str, list] = {"web": [], "images": [], "news": []}
        if effective_sources:
            for src in effective_sources:
                if src in result_data:
                    result_data[src] = [r.model_dump() for r in search_results]
        else:
            result_data["web"] = [r.model_dump() for r in search_results]

        # Merge image results if sources included "images"
        if image_results:
            result_data["images"] = [r.model_dump() for r in image_results]

        # Rich mode: scrape results and synthesize enriched content
        output = None
        if body.search_type == "rich" and body.retrieval_mode in (
            "keyword",
            "semantic",
            "hybrid",
            "vector",
            "hybrid_vector",
        ):
            from ..research import run_rich_search

            output = await run_rich_search(
                search_results=[
                    {"url": r.url, "title": r.title, "description": r.description}
                    for r in search_results
                ],
                query=body.query,
                limit=body.limit,
                output_schema=body.output_schema,
                system_prompt=body.system_prompt,
                scraper_url=request.app.state.scraper_url,
                llm_base_url=request.app.state.llm_base_url,
                llm_api_key=request.app.state.llm_api_key,
                llm_model=request.app.state.llm_model,
            )

        # ── Contents options: per-result highlights, summary, extras ──
        if body.contents:
            from ..llm import LLMClient
            from ..research import process_contents_for_results
            from ..scraper_client import ScraperClient

            llm_client = LLMClient(
                request.app.state.llm_base_url,
                request.app.state.llm_api_key,
                request.app.state.llm_model,
            )
            scraper_client = ScraperClient(request.app.state.scraper_url)
            try:
                # Build result dicts from current search_results
                result_dicts = [
                    {"url": r.url, "title": r.title, "description": r.description}
                    for r in search_results
                ]
                enriched = await process_contents_for_results(
                    result_dicts,
                    body.query,
                    body.contents,
                    llm_client,
                    scraper_client,
                )
                # Update search_results with enriched data
                search_results = [
                    SearchResult(
                        url=r["url"],
                        title=r["title"],
                        description=r.get("description", ""),
                        highlights=r.get("highlights"),
                        summary=r.get("summary"),
                        extras=r.get("extras"),
                        markdown=r.get("markdown"),
                    )
                    for r in enriched
                ]
            finally:
                await llm_client.close()
                await scraper_client.close()

        return SearchResponse(data=result_data, output=output, warning=warning_msg)
    finally:
        await searxng.close()
