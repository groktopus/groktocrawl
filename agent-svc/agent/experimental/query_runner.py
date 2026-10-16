"""Bounded query-to-reports entry point using server-owned acquisition tools."""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
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
    research_id: str | None = None,
    revision_id: str | None = None,
    artifact_set_id: str | None = None,
    selected_sources: tuple[CapturedSource, ...] = (),
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
    if len(selected_sources) > 8 or any(
        not source.text.strip() for source in selected_sources
    ):
        raise ValueError("invalid retained source context")
    seed_bytes = sum(len(source.text.encode()) for source in selected_sources)
    if seed_bytes > 256_000:
        raise ValueError("retained context exceeds research byte budget")
    async with asyncio.timeout(180):
        fresh_limit = min(source_limit, 8 - len(selected_sources))
        urls = (
            await asyncio.ensure_future(search(objective, fresh_limit))
            if fresh_limit
            else ()
        )
        owner = asyncio.current_task()
        if owner is not None and owner.cancelling():
            raise asyncio.CancelledError
        if (
            not isinstance(urls, tuple)
            or not (0 if selected_sources else 1) <= len(urls) <= source_limit
        ):
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
        sources = list(selected_sources)
        retained_bytes = seed_bytes
        for url in urls:
            if any(source.url == url for source in selected_sources):
                continue
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
        evaluated = max(
            [clock() if clock is not None else datetime.now(UTC)]
            + [
                datetime.fromisoformat(source.retrieved_at)
                for source in sources
                if source.retrieved_at is not None
            ]
        )
        return await research_from_sources(
            objective,
            tuple(sources),
            complete=complete,
            scope_id=scope_id,
            model=model,
            clock=lambda: evaluated,
            research_id=research_id,
            revision_id=revision_id,
            artifact_set_id=artifact_set_id,
        )
