"""Bounded query-to-reports entry point using server-owned acquisition tools."""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime
from urllib.parse import urlsplit

from .consolidated_journey import JourneyResult
from .model_review import Complete
from .query_construction import CapturedSource
from .real_journey import research_from_sources

Search = Callable[[str, int], Awaitable[tuple[str, ...]]]
Acquire = Callable[[str], Awaitable[CapturedSource]]


async def run_query(
    objective: str,
    *,
    search: Search,
    acquire: Acquire,
    complete: Complete,
    scope_id: str,
    model: str = "local",
    source_limit: int = 3,
    clock: Callable[[], datetime] | None = None,
) -> JourneyResult:
    """One search, up to three acquisitions, one construction, executed reviews.

    Tool implementations own destination policy, capacity admission and credentials.
    No callbacks are derived from source text; no retries or additional searches
    occur. Any failed acquisition fails this initial policy rather than silently
    turning an operational error into a successful partial answer.
    """
    if (
        not isinstance(objective, str)
        or not objective.strip()
        or len(objective) > 10_000
    ):
        raise ValueError("invalid research objective")
    if type(source_limit) is not int or not 1 <= source_limit <= 3:
        raise ValueError("source limit must be between one and three")
    async with asyncio.timeout(180):
        urls = await asyncio.ensure_future(search(objective, source_limit))
        owner = asyncio.current_task()
        if owner is not None and owner.cancelling():
            raise asyncio.CancelledError
        if not isinstance(urls, tuple) or not 1 <= len(urls) <= source_limit:
            raise ValueError("search did not return a bounded source plan")
        if len(set(urls)) != len(urls):
            raise ValueError("search returned duplicate source URLs")
        for url in urls:
            parsed = urlsplit(url)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username
                or parsed.password
            ):
                raise ValueError("search returned invalid source URL")
        sources = []
        retained_bytes = 0
        for url in urls:
            source = await asyncio.ensure_future(acquire(url))
            task = asyncio.current_task()
            if task is not None and task.cancelling():
                raise asyncio.CancelledError
            if source.url != url or not source.text.strip():
                raise ValueError(
                    "acquisition changed source identity or returned empty content"
                )
            retained_bytes += len(source.text.encode())
            if retained_bytes > 256_000:
                raise ValueError("acquisition exceeded research byte budget")
            sources.append(source)
        return await research_from_sources(
            objective,
            tuple(sources),
            complete=complete,
            scope_id=scope_id,
            model=model,
            clock=clock,
        )
