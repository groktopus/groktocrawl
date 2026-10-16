"""Find-similar functions: semantic similarity search via Qdrant or web."""

import logging
import math
import re
from urllib.parse import urlparse

import httpx

from ..barrier_guard import is_barrier_flagged, log_refusal
from ..exceptions import SemanticError
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
        if is_barrier_flagged(scraped):
            # Barrier-flagged query URL (#586): refuse rather than embed
            # challenge text as the similarity query.
            log_refusal(url, scraped)
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
            # Vector index unavailable or slow (503, 500, timeout, connection
            # error). Surface a structured 502 instead of masking the backend
            # failure as an empty success result (issue #588).
            response = getattr(e, "response", None)
            status = (
                getattr(response, "status_code", None) if response is not None else None
            )
            detail = f" (HTTP {status})" if status else f" ({type(e).__name__})"
            logger.error(
                "find_similar: semantic vector search failed%s",
                detail,
            )
            raise SemanticError(f"Semantic vector search failed{detail}") from e

        results: list[dict] = []
        for r in vector_results:
            result_url = str(r.get("url", ""))
            if result_url.rstrip("/") == url.rstrip("/"):
                continue
            result_title = str(r.get("title", ""))
            result_description = str(r.get("description") or r.get("content") or "")[
                :200
            ]
            if result_url and (
                not result_title.strip() or not result_description.strip()
            ):
                try:
                    metadata = await scraper.scrape(result_url)
                    if metadata.get("success") and not is_barrier_flagged(metadata):
                        data = metadata.get("data", {})
                        page_markdown = str(data.get("markdown", ""))
                        result_title = result_title or str(
                            data.get("metadata", {}).get("title", "")
                        )
                        result_title = result_title or _fallback_title(
                            result_url, page_markdown
                        )
                        result_description = (
                            result_description or " ".join(page_markdown.split())[:200]
                        )
                except Exception:
                    logger.info(
                        "find_similar: metadata hydration failed for %s",
                        result_url,
                        exc_info=True,
                    )
            results.append(
                {
                    "url": r.get("url", ""),
                    "title": result_title,
                    "description": result_description,
                    "score": r.get("score"),
                    "confidence": _confidence(r.get("score")),
                    "metadata_complete": bool(
                        result_title.strip() and result_description.strip()
                    ),
                    "provenance": {
                        "source": "local_vector_index",
                        "indexed_at": r.get("indexed_at"),
                        "index_freshness": (
                            "known" if r.get("indexed_at") else "unavailable"
                        ),
                    },
                }
            )
            if len(results) >= limit:
                break
        return results
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

        # 2. Build a concise search query from meaningful page content. Pages
        # often lead with browser warnings, cookie notices, or navigation; using
        # that boilerplate sends retrieval toward unrelated JavaScript pages.
        keywords = _web_query(title, markdown)

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
                "description": r.get("description") or r.get("content") or "",
                "raw_rank": index + 1,
                "provenance": {
                    "source": "web_search",
                    "engines": r.get("engines") or [],
                    "index_freshness": "not_applicable",
                    "query_representation": "title_and_meaningful_content",
                },
            }
            for index, r in enumerate(results_list[: limit * 2])
            if r.get("url") and str(r.get("url")).rstrip("/") != url.rstrip("/")
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
        ranked = []
        for rank, (score, candidate) in enumerate(scored[:limit], start=1):
            metadata_complete = bool(
                candidate["title"].strip() and candidate["description"].strip()
            )
            ranked.append(
                {
                    **candidate,
                    "score": round(score, 6),
                    "rank": rank,
                    "confidence": _confidence(score),
                    "metadata_complete": metadata_complete,
                }
            )
        return ranked
    finally:
        await scraper.close()
        await semantic.close()
        await searxng.close()


def _confidence(score: object) -> str:
    """Map a cosine score to a deliberately coarse caller-facing band."""
    if not isinstance(score, int | float):
        return "unknown"
    return "high" if score >= 0.75 else "medium" if score >= 0.5 else "low"


_BOILERPLATE = re.compile(
    r"\b(javascript|enable cookies?|cookie policy|skip to|navigation|menu)\b",
    re.IGNORECASE,
)


def _web_query(title: str, markdown: str) -> str:
    """Return a bounded, human-readable search query without page chrome."""
    useful: list[str] = []
    for block in re.split(r"\n\s*\n", markdown):
        text = re.sub(r"[#*_`>\[\]()]", " ", block)
        text = " ".join(text.split())
        if len(text) < 20 or _BOILERPLATE.search(text):
            continue
        useful.append(text)
        if len(" ".join(useful)) >= 300:
            break
    query = " ".join(part for part in [title.strip(), *useful] if part)
    return query[:500] or title.strip() or markdown[:500]


def _fallback_title(url: str, markdown: str) -> str:
    """Derive a stable title when an older index entry has no title metadata."""
    heading = re.search(r"^#{1,6}\s+(.+?)\s*$", markdown, re.MULTILINE)
    if heading:
        return heading.group(1).strip()[:200]
    parsed = urlparse(url)
    path = parsed.path.strip("/").replace("/", " / ")
    return " — ".join(part for part in (parsed.netloc, path) if part)[:200]
