"""Find-similar functions: semantic similarity search via Qdrant or web."""

import logging
import math

import httpx

from ..scraper_client import ScraperClient
from ..searxng_client import SearXNGClient

logger = logging.getLogger(__name__)


async def run_find_similar(
    url: str,
    limit: int = 10,
    search_mode: str = "qdrant",
    scraper_url: str = "http://scraper-svc:8001",
    semantic_url: str = "http://semantic-svc:8003",
    searxng_url: str = "http://searxng:8080",
) -> list[dict]:
    """Find semantically similar pages for a given URL.

    Dispatches to the appropriate mode based on ``search_mode``.
    Returns a list of dicts with url, title, description.
    """

    if search_mode == "web":
        results = await _run_find_similar_web(
            url=url,
            limit=limit,
            scraper_url=scraper_url,
            semantic_url=semantic_url,
            searxng_url=searxng_url,
        )
    else:
        # Default to qdrant for any unrecognized mode
        results = await _run_find_similar_qdrant(
            url=url,
            limit=limit,
            scraper_url=scraper_url,
            semantic_url=semantic_url,
        )

    return results


async def _run_find_similar_qdrant(
    url: str,
    limit: int,
    scraper_url: str,
    semantic_url: str,
) -> list[dict]:
    """Find similar pages by scraping a URL, embedding its content,
    and searching the local Qdrant vector index."""
    from ..semantic_client import SemanticClient

    scraper = ScraperClient(scraper_url)
    semantic = SemanticClient(semantic_url)

    try:
        # 1. Scrape the URL to get content
        scraped = await scraper.scrape(url)
        if not scraped.get("success"):
            return []
        markdown = scraped.get("data", {}).get("markdown", "")
        title = scraped.get("data", {}).get("metadata", {}).get("title", "")

        if not markdown.strip():
            return []

        # 2. Search Qdrant using the scraped content as the query
        # search_vector() embeds the text server-side and searches the index
        query_text = f"{title} {markdown[:3000]}"
        try:
            vector_results = await semantic.search_vector(query_text, limit=limit)
        except httpx.HTTPError as e:
            # Vector index unavailable or slow (503, timeout, connection
            # error). Degrade gracefully to no similar results rather than
            # surfacing a 500 to the caller.
            response = getattr(e, "response", None)
            status = (
                getattr(response, "status_code", None) if response is not None else None
            )
            detail = f" (HTTP {status})" if status else f" ({type(e).__name__})"
            logger.warning(
                "find_similar: semantic vector search failed%s; returning no results",
                detail,
            )
            return []

        return [
            {
                "url": r.get("url", ""),
                "title": r.get("title", ""),
                "description": r.get("content", "")[:200] if r.get("content") else "",
            }
            for r in vector_results
        ]
    finally:
        await scraper.close()
        await semantic.close()


async def _run_find_similar_web(
    url: str,
    limit: int,
    scraper_url: str,
    semantic_url: str,
    searxng_url: str,
) -> list[dict]:
    """Find similar pages by scraping a URL, extracting keywords,
    searching the open web, and reranking by cosine similarity."""
    from ..semantic_client import SemanticClient

    scraper = ScraperClient(scraper_url)
    semantic = SemanticClient(semantic_url)
    searxng = SearXNGClient(searxng_url)

    try:
        # 1. Scrape the URL
        scraped = await scraper.scrape(url)
        if not scraped.get("success"):
            return []
        markdown = scraped.get("data", {}).get("markdown", "")
        title = scraped.get("data", {}).get("metadata", {}).get("title", "")

        if not markdown.strip():
            return []

        # 2. Extract key terms from content (title + first paragraph)
        first_para = markdown.split("\n\n")[0] if "\n\n" in markdown else markdown[:500]
        keywords = f"{title} {first_para}"

        # 3. Search the web with key terms (fetch extra for reranking headroom)
        results_list, _health = await searxng.search(keywords, limit=limit * 2)

        if not results_list:
            return []

        # 4. Embed the query URL's content for reranking
        query_embeddings = await semantic.embed([markdown[:5000]])
        query_embedding = query_embeddings[0]

        # 5. Embed each candidate result's description
        candidates = [
            {
                "url": r.get("url", ""),
                "title": r.get("title", ""),
                "description": r.get("description", ""),
            }
            for r in results_list[: limit * 2]
        ]
        texts_to_embed = [f"{c['title']} {c['description']}" for c in candidates]
        if not texts_to_embed:
            return []

        candidate_embeddings = await semantic.embed(texts_to_embed)

        # 6. Rank by cosine similarity
        scored = []
        for _i, (candidate, emb) in enumerate(
            zip(candidates, candidate_embeddings, strict=False)
        ):
            dot = sum(a * b for a, b in zip(query_embedding, emb, strict=False))
            norm_q = math.sqrt(sum(a * a for a in query_embedding))
            norm_c = math.sqrt(sum(b * b for b in emb))
            sim = dot / (norm_q * norm_c) if norm_q > 0 and norm_c > 0 else 0.0
            scored.append((sim, candidate))

        scored.sort(key=lambda x: x[0], reverse=True)

        # 7. Return top N
        return [
            {
                "url": c["url"],
                "title": c["title"],
                "description": c["description"],
            }
            for _, c in scored[:limit]
        ]
    finally:
        await scraper.close()
        await semantic.close()
        await searxng.close()
