"""Discovery + scrape functions for the research agent."""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any, cast

from ..barrier_guard import is_barrier_flagged, log_refusal
from ..metrics import METRICS
from ..scraper_client import ScraperClient
from ..searxng_client import SearXNGClient
from .scoring import _filter_and_rank_urls, _is_video_platform_url
from .sources import (
    SourceArtifact,
    SourceRegistry,
    artifacts_to_documents_and_details,
    normalize_source_url,
)

logger = logging.getLogger(__name__)

ArtifactCallback = Callable[[SourceArtifact], Awaitable[None] | None]
SearchCallback = Callable[[list[dict]], Awaitable[None] | None]


async def _notify_artifact(callback: ArtifactCallback | None, artifact: SourceArtifact):
    if callback is None:
        return
    outcome = callback(artifact)
    if outcome is not None:
        await outcome


async def _notify_search(callback: SearchCallback | None, results: list[dict]):
    if callback is None:
        return
    outcome = callback(results)
    if outcome is not None:
        await outcome


def _rerank_artifact_flagged(artifact: SourceArtifact) -> bool:
    """Whether a rerank-reuse artifact carries barrier-flagged content.

    Rerank artifacts lose the scraper's ``warning``/``quality`` envelope (only
    markdown survives), so flagging is re-derived from the content itself via
    the shared challenge-marker check in barrier_guard (#586).
    """
    from ..barrier_guard import markdown_is_challenge

    return markdown_is_challenge(artifact.markdown)


async def _scrape_single(
    url: str,
    scraper: ScraperClient,
    semaphore: asyncio.Semaphore,
    url_timeout: int = 70,
    scrape_options: dict | None = None,
) -> SourceArtifact | None:
    """Scrape a single URL with a semaphore for concurrency control.

    Returns a ``SourceArtifact`` carrying the fetched Markdown, or None on
    failure. Barrier-flagged payloads (success-with-warning or block-fail
    quality) are refused — the source is never ingested (#586).
    """
    async with semaphore:
        try:
            logger.info("Scraping: %s", url)
            result = await asyncio.wait_for(
                scraper.scrape_with_fallback(url, scrape_options=scrape_options),
                timeout=url_timeout,
            )
            if result.get("success") and result.get("data", {}).get("markdown"):
                if is_barrier_flagged(result):
                    log_refusal(url, result)
                    return None
                md = result["data"]["markdown"]
                return SourceArtifact(
                    url=url,
                    markdown=md,
                    source=result["data"].get("source", "unknown"),
                    char_count=len(md),
                    cache_state="live",
                    fetched_at=time.time(),
                    fetch_options=scrape_options,
                )
            else:
                logger.warning("Failed to scrape %s: %s", url, result.get("error"))
                return None
        except TimeoutError:
            logger.warning("Timeout scraping %s after %ss", url, url_timeout)
            return None
        except Exception as e:
            logger.warning("Error scraping %s: %s", url, e)
            return None


async def _scrape_urls(
    urls: list[str],
    scraper: ScraperClient,
    min_sources: int = 3,
    max_attempts: int | None = None,
    max_concurrent: int = 5,
    scrape_options: dict | None = None,
    source_registry: SourceRegistry | None = None,
    on_artifact: ArtifactCallback | None = None,
) -> list[SourceArtifact]:
    """Scrape URLs with bounded concurrency and return ``SourceArtifact``s.

    Tries URLs in batches until ``min_sources`` are successfully scraped
    or the list is exhausted (whichever comes first).
    Uses a semaphore (default ``max_concurrent`` = 5) with per-URL timeout (70s).
    ``max_attempts`` sets an upper bound on how many URLs are tried.
    Cancelled speculative tasks are awaited before returning so no pending
    coroutine is left behind.
    """
    artifacts: list[SourceArtifact] = []
    urls_to_scrape: list[str] = []
    seen_keys: set[str] = set()
    for url in urls:
        key = normalize_source_url(url)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        if source_registry is not None:
            reused = source_registry.get(url, scrape_options)
            if reused is not None:
                artifacts.append(reused)
                METRICS.counter(
                    "fetches_deduped_total",
                    "Total scrapes avoided by reusing already-fetched content",
                    ["reason"],
                ).inc({"reason": "research_registry"})
                continue
        urls_to_scrape.append(url)

    # Reused evidence remains free, but must not crowd novel gap results out
    # of the fresh acquisition quota. A duplicate-only pass launches no work.
    if not urls_to_scrape:
        return artifacts
    min_sources += len(artifacts)

    urls = urls_to_scrape
    fresh_by_key: dict[str, SourceArtifact] = {}

    def finalize() -> list[SourceArtifact]:
        """Register fresh artifacts in candidate order for stable output."""
        if source_registry is None:
            return artifacts
        for url in urls:
            key = normalize_source_url(url)
            artifact = fresh_by_key.get(key)
            if artifact is not None:
                source_registry.register(artifact, scrape_options)
        fresh = [
            fresh_by_key[normalize_source_url(url)]
            for url in urls
            if normalize_source_url(url) in fresh_by_key
        ]
        return artifacts[: len(artifacts) - len(fresh)] + fresh

    max_attempts = len(urls) if max_attempts is None else max(0, max_attempts)
    semaphore = asyncio.Semaphore(max_concurrent)
    url_timeout = 70  # Accommodates scrape_with_fallback (20s generic + 45s browser)

    # Process URLs in batches — launch concurrent tasks, collect results,
    # stop when min_sources is reached or max_attempts exhausted
    pending = list(urls)
    tasks: set[asyncio.Task] = set()
    attempts = 0

    try:
        while pending or tasks:
            # Fill slots up to our budget
            while len(tasks) < max_concurrent and pending and attempts < max_attempts:
                url = pending.pop(0)
                attempts += 1
                task = asyncio.create_task(
                    _scrape_single(url, scraper, semaphore, url_timeout, scrape_options)
                )
                tasks.add(task)

            if not tasks:
                break

            # Wait for at least one task to complete
            done, tasks = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)

            for task in done:
                artifact = task.result()
                if artifact is not None:
                    artifacts.append(artifact)
                    fresh_by_key[normalize_source_url(artifact.url)] = artifact
                    await _notify_artifact(on_artifact, artifact)

            if len(artifacts) >= min_sources:
                # Process every task in this completed batch before stopping.
                # ``asyncio.wait`` may return several successful tasks together;
                # returning from inside the loop would lose already-fetched
                # artifacts, callbacks, and their accounting.
                for t in tasks:
                    t.cancel()
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)
                return finalize()

    finally:
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    return finalize()


def _dedupe_urls(urls: list[str]) -> list[str]:
    """Keep first URL spelling while deduplicating conservative identities."""
    seen: set[str] = set()
    result: list[str] = []
    for url in urls:
        key = normalize_source_url(url)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(url)
    return result


def _apply_credit_budget(
    urls: list[str],
    max_credits: int | None,
    source_registry: SourceRegistry | None,
    scrape_options: dict | None,
) -> list[str]:
    """Bound only novel acquisitions; compatible registry hits are free."""
    if max_credits is None or max_credits < 0:
        return urls
    if source_registry is None:
        return urls[:max_credits]

    result: list[str] = []
    novel = 0
    for url in urls:
        if source_registry.get(url, scrape_options) is None:
            if novel >= max_credits:
                continue
            novel += 1
        result.append(url)
    return result


def _discovery_result(
    *,
    search_results: list[dict],
    target_urls: list[str],
    artifacts: list[SourceArtifact],
    source_registry: SourceRegistry | None,
    reusable_keys: set[str],
    pass_number: int | None = None,
) -> dict:
    """Project discovery with unique context and acquisition accounting."""
    all_artifacts = (
        source_registry.artifacts() if source_registry is not None else artifacts
    )
    documents, source_details = artifacts_to_documents_and_details(all_artifacts)
    context = "\n\n---\n\n".join(documents) if documents else ""
    novel_artifacts = [
        artifact
        for artifact in artifacts
        if normalize_source_url(artifact.url) not in reusable_keys
    ]
    reused_artifacts = [
        artifact
        for artifact in artifacts
        if normalize_source_url(artifact.url) in reusable_keys
    ]
    if pass_number is not None:
        METRICS.counter(
            "research_novel_sources_total",
            "Successfully acquired novel sources by research pass",
            ["pass"],
        ).inc({"pass": str(pass_number)}, len(novel_artifacts))
    return {
        "search_results": search_results,
        "target_urls": target_urls,
        "documents": documents,
        "source_details": source_details,
        "context": context,
        "artifacts": all_artifacts,
        "new_artifacts": novel_artifacts,
        "reused_artifacts": reused_artifacts,
        "novel_sources": [artifact.url for artifact in novel_artifacts],
        "reused_sources": [artifact.url for artifact in reused_artifacts],
        "credits_used": len(novel_artifacts),
        "fetches_deduped": len(reused_artifacts),
    }


async def _scrape_with_fallback(
    urls: list[str],
    scraper: ScraperClient,
    min_sources: int = 3,
    scrape_options: dict | None = None,
    source_registry: SourceRegistry | None = None,
    on_artifact: ArtifactCallback | None = None,
) -> list[SourceArtifact]:
    """Scrape URLs with video-platform fallback strategy.

    Splits URLs into preferred (text-based) and deprioritized (video-platform).
    Scrapes preferred URLs first. If fewer than ``min_sources`` artifacts are
    obtained, falls back to deprioritized URLs.

    Returns a list of ``SourceArtifact``s.
    """
    preferred = [u for u in urls if not _is_video_platform_url(u)]
    deprioritized = [u for u in urls if _is_video_platform_url(u)]

    artifacts = await _scrape_urls(
        preferred,
        scraper,
        min_sources=min_sources,
        max_attempts=len(preferred) or 10,
        scrape_options=scrape_options,
        source_registry=source_registry,
        on_artifact=on_artifact,
    )
    logger.info(
        "Scrape with fallback: %d docs from %d preferred URLs (min_sources=%d)",
        len(artifacts),
        len(preferred),
        min_sources,
    )

    if len(artifacts) < min_sources and deprioritized:
        remaining = min_sources - len(artifacts)
        extra = await _scrape_urls(
            deprioritized,
            scraper,
            min_sources=remaining,
            max_attempts=remaining * 2,
            scrape_options=scrape_options,
            source_registry=source_registry,
            on_artifact=on_artifact,
        )
        artifacts.extend(extra)

    return artifacts


async def _run_multi_query_discover_and_scrape(
    queries: list[str],
    urls: list[str] | None,
    searxng: SearXNGClient,
    scraper: ScraperClient,
    max_searches_per_request: int = 5,
    scrape_options: dict | None = None,
    max_credits: int | None = None,
    source_registry: SourceRegistry | None = None,
    pass_number: int | None = None,
    on_artifact: ArtifactCallback | None = None,
    on_search_results: SearchCallback | None = None,
) -> dict:
    """Search multiple sub-queries, deduplicate URLs, scrape, and merge context.

    Iterates over ``queries`` (truncated to ``max_searches_per_request``),
    running a search for each. Collects all unique URLs across all queries
    (deduplicating by URL, keeping the first occurrence for richer metadata),
    then scrapes the union. Merges documents into a single context block
    organized by query.

    When ``max_credits`` is set (1 credit ≈ one successfully scraped page),
    the candidate list handed to the scraper is truncated to that budget so
    discovery can never exceed the requested credit allowance.

    Returns the same dict shape as ``_run_research_discover_and_scrape()``:
        search_results, target_urls, documents, source_details, context
    """
    source_registry = source_registry or SourceRegistry()
    target_urls = _dedupe_urls(list(urls) if urls else [])
    all_search_results: list[dict] = []
    seen_urls: set[str] = {normalize_source_url(url) for url in target_urls}
    scrape_by_key: dict[str, SourceArtifact] = {}
    streamed_acquisition = False
    admitted_urls: list[str] = []

    # Truncate to search budget
    budget = min(len(queries), max_searches_per_request)
    queries_to_run = queries[:budget]

    if not target_urls and queries_to_run:
        streamed_acquisition = True
        logger.info(
            "Multi-query research: running %d search queries (budget=%d)",
            len(queries_to_run),
            max_searches_per_request,
        )

        search_tasks = {
            asyncio.create_task(
                searxng.search(q, limit=10, raise_on_rate_limit=True)
            ): i
            for i, q in enumerate(queries_to_run)
        }
        scrape_tasks: set[asyncio.Task[SourceArtifact | None]] = set()
        candidate_urls: list[str] = []
        video_urls: list[str] = []
        attempted_keys: set[str] = set()
        ordered_results: dict[int, list[dict]] = {}
        attempts = 0
        max_attempts = 20 if max_credits is None else min(20, max(0, max_credits))
        scrape_semaphore = asyncio.Semaphore(5)
        admitted_query = 0

        async def start_candidates() -> None:
            nonlocal attempts
            while (
                candidate_urls
                and len(scrape_tasks) < 5
                and attempts < max_attempts
                and sum(
                    source_registry.get(a.url, scrape_options) is None
                    for a in scrape_by_key.values()
                )
                < 3
            ):
                url = candidate_urls.pop(0)
                key = normalize_source_url(url)
                if key in attempted_keys:
                    continue
                attempted_keys.add(key)
                reused = source_registry.get(url, scrape_options)
                if reused is not None:
                    scrape_by_key[key] = reused
                    admitted_urls.append(url)
                    continue
                attempts += 1
                admitted_urls.append(url)
                scrape_tasks.add(
                    asyncio.create_task(
                        _scrape_single(
                            url,
                            scraper,
                            scrape_semaphore,
                            70,
                            scrape_options,
                        )
                    )
                )

        try:
            while search_tasks or scrape_tasks:
                await start_candidates()
                if not search_tasks and not scrape_tasks:
                    break
                wait_set = set(search_tasks) | scrape_tasks
                done, _ = await asyncio.wait(
                    wait_set, return_when=asyncio.FIRST_COMPLETED
                )
                for task in done:
                    if task in search_tasks:
                        search_task = cast(asyncio.Task[Any], task)
                        index = search_tasks.pop(search_task)
                        query = queries_to_run[index]
                        logger.info(
                            "  [%d/%d] Searching: %s",
                            index + 1,
                            len(queries_to_run),
                            query,
                        )
                        try:
                            results, _health = search_task.result()
                        except Exception as exc:
                            from ..exceptions import RetryableRateLimitError

                            if isinstance(exc, RetryableRateLimitError):
                                raise
                            logger.warning("Search failed for %s: %s", query, exc)
                            # A failed query still resolves its position in
                            # the admission prefix so healthy later queries
                            # can proceed without changing their ordering.
                            ordered_results[index] = []
                        else:
                            ordered_results[index] = results
                            await _notify_search(on_search_results, results)
                        # Only a contiguous query prefix is eligible for
                        # admission. This makes speculation deterministic and
                        # prevents a late high-ranked query from being crowded
                        # out by an earlier completion.
                        while admitted_query in ordered_results:
                            # Freeze each query's ranked batch in plan order.
                            # Re-ranking the whole prefix would make credit
                            # admission depend on which tasks finish together.
                            batch = _filter_and_rank_urls(
                                [
                                    r.get("url", "")
                                    for r in ordered_results[admitted_query]
                                    if r.get("url")
                                ],
                                max_urls=20,
                            )
                            for url in batch:
                                key = normalize_source_url(url)
                                if url and key not in seen_urls:
                                    seen_urls.add(key)
                                    if not _is_video_platform_url(url):
                                        candidate_urls.append(url)
                                    else:
                                        video_urls.append(url)
                            admitted_query += 1
                    else:
                        scrape_task = cast(asyncio.Task[SourceArtifact | None], task)
                        scrape_tasks.remove(scrape_task)
                        artifact = scrape_task.result()
                        if artifact is not None:
                            scrape_by_key[normalize_source_url(artifact.url)] = artifact
                            await _notify_artifact(on_artifact, artifact)
                # Video sources remain a fallback until all preferred work
                # has settled; otherwise a slow text fetch could be crowded
                # out by a speculative video acquisition.
                if (
                    not search_tasks
                    and not scrape_tasks
                    and not candidate_urls
                    and len(scrape_by_key) < 3
                ):
                    candidate_urls.extend(video_urls)
                    video_urls.clear()
                await start_candidates()
                # Once discovery is complete, retain only enough successful
                # acquisitions for the final ranked evidence set.
                if (
                    sum(
                        source_registry.get(a.url, scrape_options) is None
                        for a in scrape_by_key.values()
                    )
                    >= 3
                ):
                    for task in scrape_tasks:
                        task.cancel()
                    if scrape_tasks:
                        await asyncio.gather(*scrape_tasks, return_exceptions=True)
                    scrape_tasks.clear()
        finally:
            remaining_tasks = set(search_tasks) | scrape_tasks
            for task in remaining_tasks:
                task.cancel()
            if remaining_tasks:
                await asyncio.gather(*remaining_tasks, return_exceptions=True)

        # Reconstruct search ordering separately from completion order for the
        # final rank and source metadata projection.
        all_search_results = []
        result_seen: set[str] = set()
        for index in range(len(queries_to_run)):
            for result in ordered_results.get(index, []):
                url = result.get("url", "")
                key = normalize_source_url(url)
                if url and key not in result_seen:
                    result_seen.add(key)
                    all_search_results.append(result)
        target_urls = _dedupe_urls([result["url"] for result in all_search_results])

    if not target_urls and not queries_to_run:
        return _discovery_result(
            search_results=[],
            target_urls=[],
            artifacts=[],
            source_registry=source_registry,
            reusable_keys=set(),
            pass_number=pass_number,
        )

    # Score and rank URLs before scraping (F1: source pre-filtering)
    ranked_urls = _dedupe_urls(_filter_and_rank_urls(target_urls, max_urls=20))
    # Keep successful prefix admissions even when later results change the
    # ranking. They were bounded and intentionally admitted from a ranked
    # resolved prefix, so final filtering must not discard their evidence.
    successful_admitted = [
        url for url in admitted_urls if normalize_source_url(url) in scrape_by_key
    ]
    target_urls = _dedupe_urls([*ranked_urls, *successful_admitted])
    reusable_keys = {
        normalize_source_url(url)
        for url in target_urls
        if source_registry.get(url, scrape_options) is not None
    }
    if not streamed_acquisition:
        target_urls = _apply_credit_budget(
            target_urls, max_credits, source_registry, scrape_options
        )
    if streamed_acquisition:
        artifacts = []
        for url in target_urls:
            artifact = scrape_by_key.get(normalize_source_url(url))
            if artifact is None:
                continue
            if source_registry.get(url, scrape_options) is None:
                source_registry.register(artifact, scrape_options)
            artifacts.append(artifact)
    else:
        artifacts = await _scrape_with_fallback(
            target_urls,
            scraper,
            min_sources=3,
            scrape_options=scrape_options,
            source_registry=source_registry,
            on_artifact=on_artifact,
        )
    return _discovery_result(
        search_results=all_search_results,
        target_urls=target_urls,
        artifacts=artifacts,
        source_registry=source_registry,
        reusable_keys=reusable_keys,
        pass_number=pass_number,
    )


async def _run_research_discover_and_scrape(
    prompt: str,
    urls: list[str] | None,
    searxng: SearXNGClient,
    scraper: ScraperClient,
    max_searches_per_request: int = 5,
    scrape_options: dict | None = None,
    max_credits: int | None = None,
    source_registry: SourceRegistry | None = None,
    pass_number: int | None = None,
    on_artifact: ArtifactCallback | None = None,
    on_search_results: SearchCallback | None = None,
) -> dict:
    """Search → filter → scrape → context-building phase for research.

    Shared by ``run_research`` and ``run_research_stream``. Uses
    ``_scrape_urls()`` for batch scraping; the stream variant yields
    progress events from the returned source_details after the call.

    When ``max_credits`` is set (1 credit ≈ one successfully scraped page),
    the candidate list handed to the scraper is truncated to that budget so
    discovery can never exceed the requested credit allowance.
    """
    source_registry = source_registry or SourceRegistry()
    target_urls = _dedupe_urls(list(urls) if urls else [])
    search_results: list[dict] = []
    if not target_urls:
        logger.info("No URLs provided. Searching for: %s", prompt)
        search_results, _health = await searxng.search(
            prompt, limit=10, raise_on_rate_limit=True
        )
        await _notify_search(on_search_results, search_results)
        target_urls = _dedupe_urls([r["url"] for r in search_results if r.get("url")])

    # Score and rank URLs before scraping (F1: source pre-filtering)
    target_urls = _dedupe_urls(_filter_and_rank_urls(target_urls, max_urls=20))
    reusable_keys = {
        normalize_source_url(url)
        for url in target_urls
        if source_registry.get(url, scrape_options) is not None
    }
    target_urls = _apply_credit_budget(
        target_urls, max_credits, source_registry, scrape_options
    )
    artifacts = await _scrape_with_fallback(
        target_urls,
        scraper,
        min_sources=3,
        scrape_options=scrape_options,
        source_registry=source_registry,
        on_artifact=on_artifact,
    )
    return _discovery_result(
        search_results=search_results,
        target_urls=target_urls,
        artifacts=artifacts,
        source_registry=source_registry,
        reusable_keys=reusable_keys,
        pass_number=pass_number,
    )


async def _scrape_answer_sources(
    target_urls: list[str],
    rerank_artifacts: list[SourceArtifact],
    scraper: ScraperClient,
    num_sources: int,
) -> list[SourceArtifact]:
    """Scrape only answer sources whose content was not already fetched.

    Reuses Markdown carried by ``rerank_artifacts``, scrapes the remaining
    preferred (non-video) URLs, and falls back to video-platform URLs only
    when the ``num_sources`` quota is still unmet. Returns ordered artifacts
    (preferred in rank order, then any video fallback), deduplicated by URL
    and bounded to ``num_sources``.

    Barrier-flagged rerank artifacts are dropped (#586): their markdown came
    from bare ``scraper.scrape()`` calls that bypassed the shared refusal
    seam, so they are re-gated here before reaching the answer context.
    """
    # Search results can repeat the same URL (keyword mode returns up to
    # 2x num_sources entries, many of them duplicates). Deduplicate first so
    # one artifact per URL is produced and the quota cannot be exceeded.
    seen_urls: set[str] = set()
    deduped_urls: list[str] = []
    for u in target_urls:
        if u not in seen_urls:
            seen_urls.add(u)
            deduped_urls.append(u)
    target_urls = deduped_urls

    reused: dict[str, SourceArtifact] = {}
    for artifact in rerank_artifacts:
        if not artifact.markdown:
            continue
        if _rerank_artifact_flagged(artifact):
            log_refusal(
                artifact.url,
                {
                    "warning": None,
                    "data": {"quality": {"checks": {"block_detected": "fail"}}},
                },
            )
            continue
        reused[artifact.url] = artifact
    dedup_counter = METRICS.counter(
        "fetches_deduped_total",
        "Total scrapes avoided by reusing already-fetched content",
        ["reason"],
    )
    for _ in reused:
        dedup_counter.inc({"reason": "rerank_reuse"})

    preferred = [u for u in target_urls if not _is_video_platform_url(u)]
    deprioritized = [u for u in target_urls if _is_video_platform_url(u)]

    if deprioritized:
        logger.info(
            "Answer: %d preferred + %d video-platform URLs (deprioritized)",
            len(preferred),
            len(deprioritized),
        )

    missing_preferred = [u for u in preferred if u not in reused]
    fresh_preferred = await _scrape_urls(
        missing_preferred,
        scraper,
        min_sources=max(0, num_sources - len(reused)),
        max_attempts=len(missing_preferred) or 1,
    )
    fresh_by_url = {a.url: a for a in fresh_preferred}

    preferred_artifacts = [
        preferred_artifact
        for u in preferred
        if (preferred_artifact := reused.get(u) or fresh_by_url.get(u)) is not None
    ][:num_sources]
    artifacts = list(preferred_artifacts)

    if len(artifacts) < num_sources and deprioritized:
        logger.info(
            "Answer: %d/%d from preferred sources, falling back to video-platform URLs",
            len(artifacts),
            num_sources,
        )
        missing_video = [u for u in deprioritized if u not in reused]
        remaining = num_sources - len(artifacts)
        fresh_video = await _scrape_urls(
            missing_video,
            scraper,
            min_sources=remaining,
            max_attempts=remaining * 2,
        )
        video_by_url = {a.url: a for a in fresh_video}
        for u in deprioritized:
            if len(artifacts) >= num_sources:
                break
            video_artifact = reused.get(u) or video_by_url.get(u)
            if video_artifact is not None:
                artifacts.append(video_artifact)

    return artifacts


def _build_answer_context(
    search_results: list[dict],
    artifacts: list[SourceArtifact],
) -> dict:
    """Build answer context blocks and the citation source map from artifacts."""
    documents, source_details = artifacts_to_documents_and_details(artifacts)

    context_parts = []
    for i, artifact in enumerate(artifacts, start=1):
        title = next(
            (
                r.get("title", "")
                for r in search_results
                if r.get("url") == artifact.url
            ),
            "",
        )
        context_parts.append(
            f"[{i}] Source: {artifact.url}\nTitle: {title}\n\n{artifact.to_document()}"
        )

    context = "\n\n---\n\n".join(context_parts) if context_parts else ""

    # source_map is ordered to match context_parts so that the ``[N]`` markers
    # the LLM sees map 1:1 onto source_map[N-1].
    source_map: list[dict[str, str]] = []
    for artifact in artifacts:
        title = next(
            (
                r.get("title", "")
                for r in search_results
                if r.get("url") == artifact.url
            ),
            "",
        )
        relevance = next(
            (
                r.get("description", "")
                for r in search_results
                if r.get("url") == artifact.url
            ),
            "",
        )
        source_map.append({"url": artifact.url, "title": title, "relevance": relevance})

    return {
        "context_parts": context_parts,
        "documents": documents,
        "source_details": source_details,
        "context": context,
        "source_map": source_map,
    }


async def _run_answer_discover_and_scrape(
    query: str,
    num_sources: int,
    retrieval_mode: str,
    searxng: SearXNGClient,
    scraper: ScraperClient,
    semantic_url: str,
    llm_base_url: str,
    llm_api_key: str,
    llm_model: str,
    requested_model: str | None,
    max_searches_per_request: int = 5,
) -> dict:
    """Search → rerank → filter → scrape → context-building for answer.

    Shared by ``run_answer`` and ``run_answer_stream``. Reranking may fetch
    candidate content concurrently; that content is reused here so a URL is
    not scraped twice. Returns all intermediate data needed by both callers
    to proceed to LLM synthesis and citation parsing.
    """
    from .rerank import _rerank_answer_sources

    search_results: list[dict] = []
    logger.info("Answer: searching for: %s", query)
    if retrieval_mode == "hybrid_vector":
        # Defer web search to the hybrid planner, which runs SearXNG and
        # Qdrant discovery concurrently (web is not searched twice).
        search_results = []
    else:
        search_results, _health = await searxng.search(
            query, limit=num_sources * 2, raise_on_rate_limit=True
        )

    rerank_artifacts: list[SourceArtifact] = []
    if retrieval_mode != "keyword":
        search_results, rerank_artifacts = await _rerank_answer_sources(
            search_results,
            query,
            retrieval_mode,
            semantic_url,
            scraper.base_url,
            num_sources,
            searxng=searxng,
        )

    target_urls = [r["url"] for r in search_results if r.get("url")]
    artifacts = await _scrape_answer_sources(
        target_urls, rerank_artifacts, scraper, num_sources
    )

    return {
        "search_results": search_results,
        **_build_answer_context(search_results, artifacts),
    }
