"""Domain classification, retention scoring, and eviction logic.

Extracted from app.py per ADR-0037.
"""

import datetime
import logging
import math
import urllib.parse

from qdrant_client import QdrantClient, models

from app import COLLECTION_NAME, MAX_DOCS
from metrics import METRICS

logger = logging.getLogger(__name__)


# ── Domain classification ─────────────────────────────────────────

# Domain patterns for retention categories (unchanged from Phase 3)
_DOCS_DOMAINS = {
    "readthedocs.io",
    "readthedocs.org",
}
_REFERENCE_DOMAINS = {
    "wikipedia.org",
    "stackoverflow.com",
    "stackexchange.com",
    "github.com",
    "gitlab.com",
}
_NEWS_DOMAINS = {
    "reuters.com",
    "nytimes.com",
    "cnn.com",
    "bbc.com",
    "bbc.co.uk",
    "bloomberg.com",
    "apnews.com",
    "npr.org",
    "theguardian.com",
    "wsj.com",
    "washingtonpost.com",
    "economist.com",
    "cnbc.com",
    "abcnews.go.com",
    "cbsnews.com",
    "nbcnews.com",
    "usatoday.com",
    "politico.com",
    "axios.com",
    "thehill.com",
}
_SOCIAL_DOMAINS = {
    "reddit.com",
    "twitter.com",
    "x.com",
    "youtube.com",
    "instagram.com",
    "tiktok.com",
    "threads.net",
    "bluesky",
    "bsky.app",
}
_BLOG_DOMAINS = {
    "medium.com",
    "substack.com",
    "ghost.io",
    "wordpress.com",
    "blogger.com",
    "blogspot.com",
}


def _compute_domain_category(url: str) -> str:
    """Classify a URL's domain into a retention category.

    Categories (in eviction-priority order):
        news (0.3), social (0.4), blog (0.6), api (0.7),
        unknown (0.8), reference (1.0), docs (1.2)
    """
    netloc = urllib.parse.urlparse(url).netloc.lower()

    if not netloc:
        return "unknown"

    # Strip leading www. for consistent matching
    netloc = netloc.removeprefix("www.")

    # Prefix-based: docs./learn./help./api./developer.
    if netloc.startswith(("docs.", "learn.", "help.")):
        return "docs"
    if netloc.startswith(("api.", "developer.")):
        return "api"
    if netloc.startswith("blog."):
        return "blog"

    # Substring-based matches
    for d in _DOCS_DOMAINS:
        if d in netloc:
            return "docs"
    for d in _REFERENCE_DOMAINS:
        if d in netloc:
            return "reference"
    for d in _NEWS_DOMAINS:
        if d in netloc:
            return "news"
    for d in _SOCIAL_DOMAINS:
        if d in netloc:
            return "social"
    for d in _BLOG_DOMAINS:
        if d in netloc:
            return "blog"

    return "unknown"


# ── Retention scoring ─────────────────────────────────────────────

_DOMAIN_MULTIPLIERS = {
    "news": 0.3,
    "social": 0.4,
    "blog": 0.6,
    "api": 0.7,
    "unknown": 0.8,
    "reference": 1.0,
    "docs": 1.2,
}

_RECENCY_HALF_LIFE_DAYS = 90.0


def _compute_retention_score(payload: dict) -> float:
    """Compute retention score from a Qdrant point's payload."""
    category = payload.get("domain_category", "unknown")
    domain_mult = _DOMAIN_MULTIPLIERS.get(category, 0.8)

    raw_date = payload.get("last_indexed_at", "")
    if raw_date:
        try:
            last_indexed = datetime.datetime.fromisoformat(raw_date)
            days_since = (datetime.datetime.now(datetime.UTC) - last_indexed).days
            days_since = max(0, days_since)
            recency_factor = math.exp(-days_since / _RECENCY_HALF_LIFE_DAYS)
            recency_factor = max(0.1, min(1.0, recency_factor))
        except (ValueError, TypeError):
            recency_factor = 0.5
    else:
        recency_factor = 0.5

    access_count = payload.get("access_count", 0)
    access_boost = min(int(access_count), 100) * 0.01

    crawl_count = payload.get("crawl_count", 0)
    crawl_boost = min(int(crawl_count), 20) * 0.05

    return round(domain_mult * recency_factor + access_boost + crawl_boost, 4)


# ── Scoring-based eviction (Phase 3) ──────────────────────────────


async def _evict_if_needed(qdrant: QdrantClient):
    """Scoring-based eviction if the index exceeds MAX_DOCS."""
    count = qdrant.count(COLLECTION_NAME).count
    if count <= MAX_DOCS:
        return

    excess = count - MAX_DOCS
    target_delete = excess + max(100, int(MAX_DOCS * 0.02))

    logger.info(
        "Index over capacity (%d / %d), scoring eviction candidates...",
        count,
        MAX_DOCS,
    )

    scored_points: list[tuple[float, int]] = []
    next_offset: int | None = None
    page_size = 2000

    while len(scored_points) < target_delete or next_offset is not None:
        page, next_offset = qdrant.scroll(
            COLLECTION_NAME,
            limit=page_size,
            offset=next_offset,
            with_payload=True,
            with_vectors=False,
        )
        if not page:
            break
        for point in page:
            if point.payload is None:
                continue
            score = _compute_retention_score(point.payload)
            scored_points.append((score, point.id))

        if next_offset is None:
            break

    scored_points.sort(key=lambda x: x[0])

    to_delete = [pid for _, pid in scored_points[:target_delete]]

    if to_delete:
        qdrant.delete(
            COLLECTION_NAME,
            points_selector=models.PointIdsList(points=to_delete),
        )
        new_count = qdrant.count(COLLECTION_NAME).count
        METRICS.counter(
            "groktocrawl_index_evictions_total",
            "Cumulative evictions from the vector index",
        ).inc(value=len(to_delete))
        logger.info(
            "Scored eviction: removed %d documents (score range: %.4f – %.4f). "
            "Index at %d / %d",
            len(to_delete),
            scored_points[0][0] if scored_points else 0,
            scored_points[min(len(scored_points), target_delete) - 1][0]
            if scored_points
            else 0,
            new_count,
            MAX_DOCS,
        )
    else:
        logger.info("No eviction candidates found")
