"""Bounded query-to-reports entry point using server-owned acquisition tools."""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from urllib.parse import urlsplit

from .consolidated_journey import JourneyResult
from .model_review import Complete
from .query_construction import CapturedSource
from .real_journey import research_from_sources
from .source_scope import (
    SourceScopeUnavailableError,
    canonical_allowed_host,
    canonical_source_url,
    url_host_allowed,
)

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
    seed_urls: tuple[str, ...] = (),
    allowed_hosts: tuple[str, ...] = (),
    discover: bool = True,
    as_of: str | None = None,
    queries: tuple[str, ...] | None = None,
    allow_partial: bool = False,
    max_output_tokens: int = 2048,
    evidence_budget_chars: int | None = None,
    source_bound: bool = False,
    timeout_seconds: int = 180,
    model_timeout_seconds: int | None = None,
) -> JourneyResult:
    """Run a bounded query plan, retained acquisitions, construction and checks.

    Tool implementations own destination policy, capacity admission and credentials.
    Explicit plans allow at most three searches and eight total sources. Known
    acquisition failures can produce an insufficient-coverage report; ambiguous
    dispatch failures always stop. The legacy default remains one search and
    three new sources. No retries or callbacks are derived from source text.
    """
    if (
        not isinstance(objective, str)
        or not objective.strip()
        or len(objective) > 10_000
    ):
        raise ValueError("invalid research objective")
    if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 300:
        raise ValueError("invalid research deadline")
    ceiling = 8 if queries is not None or seed_urls or allowed_hosts else 3
    if type(source_limit) is not int or not 1 <= source_limit <= ceiling:
        raise ValueError("invalid bounded source limit")
    planned = queries if queries is not None else (objective,)
    if (
        not 1 <= len(planned) <= 3
        or any(
            not isinstance(query, str) or not query.strip() or len(query) > 10_000
            for query in planned
        )
        or len(set(planned)) != len(planned)
    ):
        raise ValueError("invalid bounded query plan")
    if len(selected_sources) > 8 or any(
        not source.text.strip() for source in selected_sources
    ):
        raise ValueError("invalid retained source context")
    if len(seed_urls) > 8 or len(allowed_hosts) > 20:
        raise ValueError("invalid source scope budget")
    seed_urls = tuple(canonical_source_url(url) for url in seed_urls)
    allowed_hosts = tuple(canonical_allowed_host(host) for host in allowed_hosts)
    if len(set(seed_urls)) != len(seed_urls):
        raise ValueError("seed URLs must be distinct")
    if allowed_hosts and any(
        not url_host_allowed(seed, allowed_hosts) for seed in seed_urls
    ):
        raise ValueError("seed URL is outside the allowed hosts")
    if len(seed_urls) + len(selected_sources) > source_limit:
        raise ValueError("seed and retained sources exceed the bounded source limit")
    if len(seed_urls) + len(selected_sources) > 8:
        raise ValueError("seed and retained sources exceed the research source budget")
    seed_bytes = sum(len(source.text.encode()) for source in selected_sources)
    if seed_bytes > 256_000:
        raise ValueError("retained context exceeds research byte budget")
    async with asyncio.timeout(timeout_seconds):
        fresh_limit = min(source_limit, 8 - len(selected_sources) - len(seed_urls))
        if not discover:
            fresh_limit = 0
        results = []
        for query in planned if fresh_limit else ():
            rows = await asyncio.ensure_future(search(query, fresh_limit))
            if not isinstance(rows, tuple) or len(set(rows)) != len(rows):
                raise ValueError("search did not return a bounded source plan")
            scoped_rows = tuple(
                url for url in rows if url_host_allowed(url, allowed_hosts)
            )
            if len(scoped_rows) > fresh_limit:
                raise ValueError("search did not return a bounded source plan")
            results.append(scoped_rows)
        ordered = dict.fromkeys(
            url
            for index in range(fresh_limit)
            for rows in results
            for url in rows[index : index + 1]
        )
        urls = tuple(ordered)[:fresh_limit]
        owner = asyncio.current_task()
        if owner is not None and owner.cancelling():
            raise asyncio.CancelledError
        minimum_fresh = 0 if selected_sources or seed_urls or not discover else 1
        if not isinstance(urls, tuple) or not minimum_fresh <= len(urls) <= fresh_limit:
            raise ValueError("search did not return a bounded source plan")
        if len(set(urls)) != len(urls):
            raise ValueError("search returned duplicate source URLs")
        for url in (*seed_urls, *urls):
            parsed = urlsplit(url)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username
                or parsed.password
            ):
                raise ValueError("source scope returned an invalid URL")
        sources = list(selected_sources)
        retained_bytes = seed_bytes
        failures = []
        for url in (*seed_urls, *urls):
            if any(source.url == url for source in selected_sources):
                continue
            try:
                source = await asyncio.ensure_future(acquire(url))
            except ValueError:
                if not allow_partial:
                    raise
                failures.append(url)
                continue
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
        if not sources:
            if seed_urls or selected_sources or allowed_hosts:
                raise SourceScopeUnavailableError(
                    "no permitted research source could be acquired"
                )
            raise ValueError("no usable acquired research sources")
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
            as_of=as_of,
            acquisition_failures=tuple(failures),
            max_output_tokens=max_output_tokens,
            evidence_budget_chars=evidence_budget_chars,
            source_bound=source_bound,
            model_timeout_seconds=model_timeout_seconds,
        )
