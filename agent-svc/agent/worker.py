"""Worker entrypoint and processing functions for GroktoCrawl jobs."""

import asyncio
import logging
import os
import time
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime, timedelta
from typing import Any

from .admission import get_admission
from .barrier_guard import is_barrier_flagged, log_refusal
from .cancel import JobCancelledError, raise_if_cancelled, set_token
from .exceptions import RetryableRateLimitError
from .metrics import METRICS
from .research import run_extract, run_research
from .research.memory import finalize_and_admit, refresh_research_memory
from .retry import (
    RetryPolicy,
    clamp_retry_delay,
    default_retry_policy,
    retry_sleep,
)
from .scraper_client import ScraperClient
from .settings import load_settings
from .store import JobStore
from .webhook import deliver_webhook
from .workload_metrics import record_job_cancelled, record_job_end, record_job_start

logger = logging.getLogger(__name__)


def _get_worker_settings() -> Any:
    return load_settings()


def _iso_now_plus(seconds: float) -> str:
    """ISO 8601 UTC timestamp ``seconds`` from now."""
    return (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat()


async def _run_job_with_observability(
    job_id: str,
    job_type: str,
    store: JobStore,
    webhook_config: dict[str, Any] | None,
    work_fn: Callable[[], Coroutine[Any, Any, Any]],
    cleanup_fn: Callable[[], Coroutine[Any, Any, None]] | None = None,
    retry_policy: RetryPolicy | None = None,
) -> None:
    """Execute work_fn with standard observability scaffolding.

    Encapsulates metrics recording, store completion/failure, webhook
    delivery, retry scheduling (ADR-0053), and cleanup — the identical
    scaffolding shared by all worker processing functions.

    Retry behavior: when ``work_fn`` raises ``RetryableRateLimitError``
    and retry budget remains, the job transitions to the non-terminal
    ``retry_scheduled`` state, a ``retry_scheduled`` webhook fires, and
    the blocked operation is attempted again after a bounded, cancellable
    delay. Retry budget exhaustion fails the job with rate-limit details;
    a job cancelled while waiting to retry never starts another attempt.
    """
    start = time.monotonic()
    METRICS.counter("jobs_submitted_total", "Total jobs submitted", ["type"]).inc(
        {"type": job_type}
    )
    record_job_start(job_type)
    retry_policy = retry_policy or default_retry_policy()
    attempt = 0
    last_delay: float | None = None
    try:
        while True:
            attempt += 1
            try:
                result = await work_fn()
            except RetryableRateLimitError as e:
                if attempt >= retry_policy.max_attempts:
                    # ── Retry budget exhausted → terminal failure ──
                    error_message = (
                        f"Rate limit retry budget exhausted after {attempt} "
                        f"attempt(s) (reason={e.error_code}"
                        + (
                            f", last retry delay {last_delay:.0f}s)"
                            if last_delay is not None
                            else ")"
                        )
                    )
                    logger.error("%s job %s %s", job_type, job_id, error_message)
                    store.fail_job(job_id, error_message)
                    await deliver_webhook(
                        webhook_config,
                        "failed",
                        job_id,
                        {"error": error_message},
                        success=False,
                        error=error_message,
                    )
                    elapsed = time.monotonic() - start
                    METRICS.histogram(
                        "job_duration_seconds",
                        "Job processing duration",
                        ["type", "status"],
                    ).observe({"type": job_type, "status": "failed"}, elapsed)
                    METRICS.counter(
                        "jobs_failed_total", "Total failed jobs", ["type"]
                    ).inc({"type": job_type})
                    METRICS.counter(
                        "job_retry_exhaustion_total",
                        "Jobs that exhausted their rate-limit retry budget",
                        ["type"],
                    ).inc({"type": job_type})
                    return

                # ── Schedule a bounded, cancellable retry ──
                delay = clamp_retry_delay(
                    e.retry_after_seconds, attempt=attempt, policy=retry_policy
                )
                retry_at = _iso_now_plus(delay)
                if not store.schedule_retry(
                    job_id,
                    retry_at=retry_at,
                    retry_attempt=attempt,
                    retry_limit=retry_policy.max_attempts,
                    reason=e.error_code,
                    retry_after_seconds=delay,
                ):
                    # The job was cancelled/completed concurrently — do not
                    # schedule or start another attempt.
                    logger.info(
                        "%s job %s no longer retryable after rate limit",
                        job_type,
                        job_id,
                    )
                    return
                await deliver_webhook(
                    webhook_config,
                    "retry_scheduled",
                    job_id,
                    data={
                        "operation": job_type,
                        "reason_code": e.error_code,
                        "retry_attempt": attempt,
                        "retry_limit": retry_policy.max_attempts,
                        "retry_at": retry_at,
                        "retry_after_seconds": delay,
                    },
                )
                METRICS.counter(
                    "job_retries_scheduled_total",
                    "Jobs scheduled to retry after a rate-limit condition",
                    ["type"],
                ).inc({"type": job_type})
                logger.info(
                    "%s job %s rate limited — retry %d/%d scheduled in %.1fs",
                    job_type,
                    job_id,
                    attempt,
                    retry_policy.max_attempts,
                    delay,
                )
                last_delay = delay
                await retry_sleep(delay)
                if not store.resume_retry(job_id):
                    # Cancelled while waiting (DELETE) — do not start another
                    # attempt; the store already records the terminal status.
                    logger.info(
                        "%s job %s cancelled while waiting to retry",
                        job_type,
                        job_id,
                    )
                    return
                continue

            # ── Success ─────────────────────────────────────────
            store.complete_job(job_id, result)
            await deliver_webhook(webhook_config, "completed", job_id, result)
            elapsed = time.monotonic() - start
            METRICS.histogram(
                "job_duration_seconds", "Job processing duration", ["type", "status"]
            ).observe({"type": job_type, "status": "completed"}, elapsed)
            METRICS.counter(
                "jobs_completed_total", "Total completed jobs", ["type"]
            ).inc({"type": job_type})
            if attempt > 1:
                METRICS.counter(
                    "job_retries_succeeded_total",
                    "Jobs completed successfully after at least one rate-limit retry",
                    ["type"],
                ).inc({"type": job_type})
            logger.info("%s job %s completed in %.2fs", job_type, job_id, elapsed)
            return
    except JobCancelledError:
        # Cooperative cancellation: the DELETE handler already marked the job
        # cancelled in the store. Do not overwrite it, deliver a completion
        # webhook, or record completed/failed metrics.
        record_job_cancelled(job_type)
        logger.info("%s job %s cancelled", job_type, job_id)
    except asyncio.CancelledError:
        # Forced cancellation of the owning task. The DELETE handler already
        # marked the job cancelled in the store; record cancellation and let
        # the finally block run cleanup before the CancelledError unwinds.
        record_job_cancelled(job_type)
        logger.info("%s job %s cancelled (forced)", job_type, job_id)
        raise
    except Exception as e:
        logger.exception("%s job %s failed", job_type, job_id)
        store.fail_job(job_id, str(e))
        await deliver_webhook(webhook_config, "failed", job_id, {"error": str(e)})
        elapsed = time.monotonic() - start
        METRICS.histogram(
            "job_duration_seconds", "Job processing duration", ["type", "status"]
        ).observe({"type": job_type, "status": "failed"}, elapsed)
        METRICS.counter("jobs_failed_total", "Total failed jobs", ["type"]).inc(
            {"type": job_type}
        )
    finally:
        record_job_end(job_type)
        if cleanup_fn:
            await cleanup_fn()


async def _process_agent_async(
    job_id: str,
    prompt: str,
    urls: list[str] | None,
    schema_: dict[str, Any] | None,
    llm_base_url: str,
    llm_api_key: str,
    llm_model: str,
    searxng_url: str,
    scraper_url: str,
    webhook_config: dict[str, Any] | None = None,
    requested_model: str | None = None,
    include_images: bool = False,
    include_source_content: bool = False,
    citation_style: Any = None,
    force_fresh: bool = False,
    stale_while_revalidate: bool = False,
    max_stale_hours: float | None = None,
    user_id: str | None = None,
    research_memory: Any = None,
    search_type: str = "deep",
    max_searches_per_request: int = 5,
    max_credits: int | None = None,
    fingerprint: str | None = None,
    task_tracker: Any = None,
) -> None:
    if task_tracker is not None:
        set_token(task_tracker.cancel_token(job_id))
    settings = _get_worker_settings()
    redis_url = (
        f"redis://{settings.valkey_host}:{settings.valkey_port}/{settings.valkey_db}"
    )
    store = JobStore(redis_url)
    from .models import CitationStyle

    cs = (
        citation_style
        if isinstance(citation_style, CitationStyle)
        else CitationStyle.inline
    )

    # ── Research Memory scope ─────────────────────────────────────
    memory_scope = os.environ.get("RESEARCH_MEMORY_SCOPE", "global")
    if memory_scope == "per_user" and user_id is None:
        user_id = "anonymous"

    # ── Research Memory — check cache before pipeline ──────────────
    stale_cache_hit: dict | None = None
    if not force_fresh and not include_source_content:
        try:
            cache_result = await research_memory.query(
                prompt=prompt,
                user_id=user_id if memory_scope == "per_user" else None,
                fingerprint=fingerprint,
                max_stale_hours=max_stale_hours if stale_while_revalidate else None,
            )
            if cache_result["hit"]:
                freshness = cache_result.get("freshness", "stale")
                if freshness == "fresh" or freshness == "aging":
                    # Cache hit is fresh or aging — return cached result directly
                    logger.info(
                        "Research memory %s hit for agent %s — returning cached result",
                        freshness,
                        job_id,
                    )
                    entry = cache_result["artifact"]
                    # Apply citation style to cached artifact if needed
                    sources = entry.get("sources", [])
                    result_text = entry.get("artifact", "")
                    from .research import _apply_citation_style

                    result_text, _ = _apply_citation_style(result_text, sources, cs)

                    cached_payload: dict[str, Any] = {
                        "result": result_text,
                        "sources": [s.get("url", "") for s in sources],
                        "source_details": sources,
                        "from_cache": True,
                        "freshness": freshness,
                        "similarity": cache_result.get("similarity", 0),
                        "memory_id": cache_result.get("memory_id", ""),
                    }
                    # Apply compact citation transformation
                    if cs == CitationStyle.compact:
                        compact_sources = []
                        for i, src in enumerate(sources, start=1):
                            compact_sources.append(
                                {
                                    "index": i,
                                    "url": src.get("url", ""),
                                }
                            )
                        cached_payload["sources_compact"] = compact_sources
                        cached_payload["source_details"] = []
                    store.complete_job(job_id, cached_payload)
                    await deliver_webhook(
                        webhook_config, "completed", job_id, cached_payload
                    )
                    return
                elif (
                    freshness == "stale"
                    and stale_while_revalidate
                    and cache_result.get("swr_eligible")
                ):
                    # Stale-while-revalidate: serve stale immediately and start
                    # ONE background refresh keyed by the compatibility fingerprint.
                    logger.info(
                        "Research memory stale hit for agent %s — serving stale and "
                        "refreshing in background",
                        job_id,
                    )
                    entry = cache_result["artifact"]
                    sources = entry.get("sources", [])
                    result_text = entry.get("artifact", "")
                    from .research import _apply_citation_style

                    result_text, _ = _apply_citation_style(result_text, sources, cs)
                    stale_payload: dict[str, Any] = {
                        "result": result_text,
                        "sources": [s.get("url", "") for s in sources],
                        "source_details": sources,
                        "from_cache": True,
                        "freshness": "stale",
                        "refreshed": False,
                        "age_hours": cache_result.get("age_hours"),
                        "similarity": cache_result.get("similarity", 0),
                        "memory_id": cache_result.get("memory_id", ""),
                    }
                    if cs == CitationStyle.compact:
                        compact_sources = []
                        for i, src in enumerate(sources, start=1):
                            compact_sources.append(
                                {"index": i, "url": src.get("url", "")}
                            )
                        stale_payload["sources_compact"] = compact_sources
                        stale_payload["source_details"] = []
                    store.complete_job(job_id, stale_payload)
                    await deliver_webhook(
                        webhook_config, "completed", job_id, stale_payload
                    )

                    async def _refresh_and_update() -> dict[str, Any] | None:
                        try:
                            fresh = await refresh_research_memory(
                                research_memory,
                                prompt=prompt,
                                urls=urls,
                                schema=schema_,
                                searxng_url=searxng_url,
                                scraper_url=scraper_url,
                                llm_base_url=llm_base_url,
                                llm_api_key=llm_api_key,
                                llm_model=llm_model,
                                requested_model=requested_model,
                                max_searches_per_request=max_searches_per_request,
                                max_credits=max_credits,
                                include_images=include_images,
                                include_source_content=include_source_content,
                                citation_style=cs,
                                search_type=search_type,
                                user_id=user_id,
                                fingerprint=fingerprint,
                            )
                        except Exception:
                            logger.warning(
                                "Background research memory refresh failed for "
                                "agent %s",
                                job_id,
                                exc_info=True,
                            )
                            return None
                        refreshed_payload: dict[str, Any] = {
                            "result": fresh.get("result", ""),
                            "sources": fresh.get("sources", []),
                            "source_details": fresh.get("source_details", []),
                            "from_cache": False,
                            "freshness": "refreshed",
                            "refreshed": True,
                            "age_hours": 0.0,
                            "research_memory_id": fresh.get("research_memory_id"),
                        }
                        if cs == CitationStyle.compact:
                            refreshed_payload["sources_compact"] = fresh.get(
                                "sources_compact", []
                            )
                            refreshed_payload["source_details"] = []
                        return refreshed_payload

                    refresh_key = fingerprint or f"prompt:{prompt}"
                    refresh_task = research_memory.start_refresh(
                        refresh_key, _refresh_and_update
                    )

                    def _write_refreshed(_future: asyncio.Future[Any]) -> None:
                        try:
                            fresh_payload = _future.result()
                        except Exception:
                            logger.warning(
                                "Background research memory refresh failed for "
                                "agent %s",
                                job_id,
                                exc_info=True,
                            )
                            return
                        if fresh_payload:
                            store.overwrite_job_data(job_id, fresh_payload)

                    refresh_task.add_done_callback(_write_refreshed)
                    return
                else:
                    # Stale hit — run normal pipeline but note cached version
                    logger.info(
                        "Research memory %s hit for agent %s — running fresh research "
                        "(cached version exists)",
                        freshness,
                        job_id,
                    )
                    stale_cache_hit = cache_result
            else:
                logger.debug("Research memory miss for agent %s", job_id)
        except Exception:
            logger.warning(
                "Research memory lookup failed for agent %s — proceeding with "
                "normal pipeline",
                job_id,
                exc_info=True,
            )
    else:
        logger.info(
            "force_fresh=True for agent %s — bypassing research memory cache",
            job_id,
        )

    async def work_fn() -> dict[str, Any]:
        # Cooperative cancellation: DELETE /v2/agent/{job_id} marks the job
        # cancelled in the store. Honour it before (and after) the research
        # pipeline so a cancelled agent job raises JobCancelledError and is
        # never recorded as completed — mirroring batch_scrape/plan_execute.
        job_meta = store.get_job(job_id)
        if job_meta and job_meta.get("status") == "cancelled":
            logger.info("Agent job %s cancelled before research", job_id)
            raise JobCancelledError("agent job cancelled via DELETE")

        result = await run_research(
            prompt=prompt,
            urls=urls,
            schema=schema_,
            searxng_url=searxng_url,
            scraper_url=scraper_url,
            llm_base_url=llm_base_url,
            llm_api_key=llm_api_key,
            llm_model=llm_model,
            requested_model=requested_model,
            include_images=include_images,
            include_source_content=include_source_content,
            citation_style=cs,
            search_type=search_type,
            max_searches_per_request=max_searches_per_request,
            max_credits=max_credits,
        )

        job_meta = store.get_job(job_id)
        if job_meta and job_meta.get("status") == "cancelled":
            logger.info("Agent job %s cancelled during research", job_id)
            raise JobCancelledError("agent job cancelled via DELETE")

        # Apply citation style to transform bare [N] markers to [N](url)
        # for compact style, or leave them unchanged for inline style, then
        # admit the post-transform artifact to research memory.
        result = await finalize_and_admit(
            research_memory,
            prompt=prompt,
            result=result,
            llm_model=llm_model,
            citation_style=cs,
            requested_model=requested_model,
            user_id=user_id,
            fingerprint=fingerprint,
        )

        # Note stale cache existence if applicable
        if stale_cache_hit:
            result["cached_version_exists"] = True
            result["cached_version_age_hours"] = stale_cache_hit.get("age_hours", 0)
            result["cached_version_freshness"] = stale_cache_hit.get(
                "freshness", "stale"
            )

        return result

    await _run_job_with_observability(job_id, "agent", store, webhook_config, work_fn)


def _record_crawl_cancelled_metrics(start: float) -> None:
    """Record crawl-specific cancelled metrics (never as completed/failed)."""
    METRICS.counter(
        "groktocrawl_crawl_jobs_total", "Total crawl jobs by status", ["status"]
    ).inc({"status": "cancelled"})
    METRICS.histogram(
        "groktocrawl_crawl_duration_seconds",
        "Crawl job duration in seconds",
        ["status"],
    ).observe({"status": "cancelled"}, time.monotonic() - start)


async def _deliver_crawl_completed_webhook(
    job_id: str,
    webhook_config: dict[str, Any] | None,
    task_tracker: Any,
) -> None:
    """Deliver ``crawl.completed`` (empty data) for terminal crawl states."""
    if task_tracker is not None:
        task_tracker.create_background_task(
            deliver_webhook(
                webhook_config,
                "crawl.completed",
                job_id,
                data=[],
                task_tracker=task_tracker,
            )
        )
    else:
        await deliver_webhook(
            webhook_config,
            "crawl.completed",
            job_id,
            data=[],
        )


async def _process_crawl_async(
    job_id: str,
    url: str,
    max_pages: int,
    max_depth: int,
    scraper_url: str,
    webhook_config: dict[str, Any] | None = None,
    task_tracker: Any = None,
    ignore_query_parameters: bool = False,
    include_paths: list[str] | None = None,
    exclude_paths: list[str] | None = None,
    regex_on_full_url: bool = False,
    verbose: bool = False,
    sitemap_mode: str = "include",
    crawl_entire_domain: bool = False,
    allow_subdomains: bool = False,
    allow_external_links: bool = False,
    max_concurrency: int = 3,
    delay: float | None = None,
    ignore_robots_txt: bool = False,
    robots_user_agent: str | None = None,
    scrape_options: dict | None = None,
) -> None:
    """Process a crawl job with full lifecycle support.

    Lifecycle webhooks (when configured):
        - ``crawl.started``: fired before the crawl begins
        - ``crawl.page``: fired after each individual page is scraped
        - ``crawl.completed``: fired on terminal states (completed, cancelled)
        - ``crawl.failed``: fired on unexpected exception

    Cancellation is cooperative: DELETE /v2/crawl/{id} sets the job meta
    status to ``cancelled`` in Redis; the engine checks this between
    page scrapes and stops early. The job store is NOT overwritten when
    the engine detects cancellation (``cancel_job()`` already set it).
    """
    settings = _get_worker_settings()
    store = JobStore(
        f"redis://{settings.valkey_host}:{settings.valkey_port}/{settings.valkey_db}"
    )
    if task_tracker is not None:
        set_token(task_tracker.cancel_token(job_id))
    scraper = ScraperClient(scraper_url)
    start = time.monotonic()
    job_type = "crawl"

    METRICS.counter("jobs_submitted_total", "Total jobs submitted", ["type"]).inc(
        {"type": job_type}
    )
    record_job_start(job_type)

    try:
        # ── Fire crawl.started webhook ────────────────────────
        # Per VAL-PARITY-030: crawl.started fires BEFORE any page scraping.
        # The data field is an empty list (no pages yet), and metadata is
        # echoed from the webhook config (VAL-PARITY-009).
        if task_tracker is not None:
            task_tracker.create_background_task(
                deliver_webhook(
                    webhook_config,
                    "crawl.started",
                    job_id,
                    data=[],
                    task_tracker=task_tracker,
                )
            )
        else:
            await deliver_webhook(
                webhook_config,
                "crawl.started",
                job_id,
                data=[],
            )

        from .crawl_cache import CrawlCache
        from .crawler import CrawlEngine, CrawlOptions

        crawl_cache = CrawlCache(
            f"redis://{settings.valkey_host}:{settings.valkey_port}/{settings.valkey_db}"
        )

        options = CrawlOptions(
            max_pages=max_pages,
            max_depth=max_depth,
            ignore_query_parameters=ignore_query_parameters,
            include_paths=include_paths,
            exclude_paths=exclude_paths,
            regex_on_full_url=regex_on_full_url,
            verbose=verbose,
            sitemap_mode=sitemap_mode,
            allow_subdomains=allow_subdomains,
            allow_external_links=allow_external_links,
            crawl_entire_domain=crawl_entire_domain,
            max_concurrency=max_concurrency,
            delay=delay,
            ignore_robots_txt=ignore_robots_txt,
            robots_user_agent=robots_user_agent,
            max_duration_seconds=settings.crawl_max_duration_seconds,
            idle_timeout_seconds=settings.crawl_idle_timeout_seconds,
            scrape_options=scrape_options,
        )
        engine = CrawlEngine(
            scraper, store=store, options=options, crawl_cache=crawl_cache
        )

        # Per-page webhook callback using task_tracker (VAL-CONC-049)
        async def _page_callback(_job_id: str, page: dict[str, Any]) -> None:
            # Deliver webhook as a tracked background task to avoid
            # blocking the crawl loop on webhook delivery latency.
            # Per VAL-PARITY-006: data is an array containing one page document.
            if task_tracker is not None:
                task_tracker.create_background_task(
                    deliver_webhook(
                        webhook_config,
                        "crawl.page",
                        _job_id,
                        data=[page],
                        task_tracker=task_tracker,
                    )
                )
            else:
                await deliver_webhook(
                    webhook_config,
                    "crawl.page",
                    _job_id,
                    data=[page],
                )

        result = await engine.run(url, job_id=job_id, page_callback=_page_callback)

        # Fire-and-forget indexing for each page using task_tracker (VAL-CONC-049)
        for page in result.pages:
            page_url = page.get("url", "")
            markdown = page.get("markdown", "")
            idx_task = _index_page_async(page_url, "", markdown[:2000])
            if task_tracker is not None:
                task_tracker.create_background_task(idx_task)
            else:
                asyncio.create_task(idx_task)

        payload: dict[str, Any] = {
            "completed": result.completed,
            "total": result.total,
            "pages": result.pages,
            "errors": result.errors,
            "robots_blocked": result.robots_blocked,
        }
        if verbose:
            payload["filtered_out"] = result.filtered_out

        # ── Check if job was cancelled via DELETE ─────────────
        job_meta = store.get_job(job_id)
        was_cancelled = job_meta is not None and job_meta.get("status") == "cancelled"

        if was_cancelled:
            # Store is already marked cancelled by cancel_job();
            # do NOT overwrite with complete_job().
            record_job_cancelled(job_type)
            logger.info("Crawl %s was cancelled — preserving cancelled status", job_id)
            await _deliver_crawl_completed_webhook(job_id, webhook_config, task_tracker)
            _record_crawl_cancelled_metrics(start)
        else:
            store.complete_job(job_id, payload)
            await _deliver_crawl_completed_webhook(job_id, webhook_config, task_tracker)

            elapsed = time.monotonic() - start
            METRICS.histogram(
                "job_duration_seconds", "Job processing duration", ["type", "status"]
            ).observe({"type": job_type, "status": "completed"}, elapsed)
            METRICS.counter(
                "jobs_completed_total", "Total completed jobs", ["type"]
            ).inc({"type": job_type})
            METRICS.counter(
                "groktocrawl_crawl_jobs_total", "Total crawl jobs by status", ["status"]
            ).inc({"status": "completed"})
            METRICS.histogram(
                "groktocrawl_crawl_duration_seconds",
                "Crawl job duration in seconds",
                ["status"],
            ).observe({"status": "completed"}, elapsed)
            METRICS.counter(
                "groktocrawl_crawl_pages_scraped_total",
                "Total pages scraped by crawl jobs",
            ).inc(value=float(result.completed))

            logger.info("Crawl job %s completed in %.2fs", job_id, elapsed)

    except JobCancelledError:
        # Cooperative cancellation: the token was set (DELETE). The store is
        # already marked cancelled; record cancellation and the lifecycle
        # webhook without overwriting status or recording completed metrics.
        record_job_cancelled(job_type)
        logger.info("Crawl %s cancelled", job_id)
        await _deliver_crawl_completed_webhook(job_id, webhook_config, task_tracker)
        _record_crawl_cancelled_metrics(start)
    except asyncio.CancelledError:
        # Forced cancellation of the owning task (DELETE cancels the task).
        # The crawler's run() finally already awaited child tasks and closed
        # the HTML client; record cancellation and re-raise to unwind.
        record_job_cancelled(job_type)
        logger.info("Crawl %s cancelled (forced)", job_id)
        await _deliver_crawl_completed_webhook(job_id, webhook_config, task_tracker)
        _record_crawl_cancelled_metrics(start)
        raise
    except Exception as e:
        logger.exception("Crawl job %s failed", job_id)
        store.fail_job(job_id, str(e))
        if task_tracker is not None:
            task_tracker.create_background_task(
                deliver_webhook(
                    webhook_config,
                    "crawl.failed",
                    job_id,
                    data=[],
                    success=False,
                    error=str(e),
                    task_tracker=task_tracker,
                )
            )
        else:
            await deliver_webhook(
                webhook_config,
                "crawl.failed",
                job_id,
                data=[],
                success=False,
                error=str(e),
            )
        elapsed = time.monotonic() - start

        # ── Existing job-type-agnostic metrics ─────────────────────────────
        METRICS.histogram(
            "job_duration_seconds", "Job processing duration", ["type", "status"]
        ).observe({"type": job_type, "status": "failed"}, elapsed)
        METRICS.counter("jobs_failed_total", "Total failed jobs", ["type"]).inc(
            {"type": job_type}
        )

        # ── Crawl-specific metrics ─────────────────────────────────────────
        METRICS.counter(
            "groktocrawl_crawl_jobs_total", "Total crawl jobs by status", ["status"]
        ).inc({"status": "failed"})
        METRICS.histogram(
            "groktocrawl_crawl_duration_seconds",
            "Crawl job duration in seconds",
            ["status"],
        ).observe({"status": "failed"}, elapsed)
    finally:
        record_job_end(job_type)
        await scraper.close()


async def _process_batch_scrape_async(
    job_id: str,
    urls: list[str],
    scraper_url: str,
    webhook_config: dict[str, Any] | None = None,
    task_tracker: Any = None,
    max_concurrency: int = 3,
) -> None:
    settings = _get_worker_settings()
    store = JobStore(
        f"redis://{settings.valkey_host}:{settings.valkey_port}/{settings.valkey_db}"
    )
    if task_tracker is not None:
        set_token(task_tracker.cancel_token(job_id))
    scraper = ScraperClient(scraper_url)

    # Inner scheduling bound: the per-request max_concurrency AND the global
    # lightweight-fetch admission budget (fetch weight = 1). The admission
    # controller in ScraperClient.scrape() is the outer cap across jobs.
    effective_concurrency = max(
        1, min(max_concurrency, get_admission().budget_for("lightweight_fetch"))
    )

    async def work_fn() -> dict[str, Any]:
        total = len(urls)

        # Cooperative cancellation via DELETE: the store status is checked
        # before any scrape (and per-URL) in addition to the cancel token.
        job_meta = store.get_job(job_id)
        if job_meta and job_meta.get("status") == "cancelled":
            logger.info("Batch scrape %s cancelled before scraping", job_id)
            raise JobCancelledError("batch scrape cancelled via DELETE")

        # Index-keyed results so pages/errors stay in input URL order even
        # though completion is out of order.
        pages_by_index: dict[int, dict] = {}
        errors_by_index: dict[int, dict] = {}
        index_batch_by_index: dict[int, dict] = {}

        # Bounded worker pool: only ``effective_concurrency`` coroutines are
        # ever created, and each pulls work from the queue as it becomes
        # available. This keeps a huge (unrate-limited) ``urls`` list from
        # instantiating one task per URL up front and from bursting
        # synchronous Valkey GETs ahead of the concurrency limit.
        queue: asyncio.Queue[tuple[int, str] | None] = asyncio.Queue()
        for i, u in enumerate(urls):
            queue.put_nowait((i, u))
        for _ in range(effective_concurrency):
            queue.put_nowait(None)  # sentinel: tell each worker to stop

        async def _scrape_worker() -> None:
            while True:
                item = await queue.get()
                if item is None:
                    return
                index, url = item

                # Cancellation + store-status checks live inside the bounded
                # worker so they never run as a synchronous pre-concurrency
                # burst for a large batch.
                raise_if_cancelled()
                job_meta = store.get_job(job_id)
                if job_meta and job_meta.get("status") == "cancelled":
                    raise JobCancelledError("batch scrape cancelled via DELETE")

                try:
                    result = await scraper.scrape(url)
                except JobCancelledError:
                    raise
                except Exception as e:
                    errors_by_index[index] = {
                        "url": url,
                        "error": str(e),
                        "error_type": "scrape_error",
                        "error_code": "SCRAPE_ERROR",
                        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    }
                else:
                    if is_barrier_flagged(result):
                        # Barrier-flagged success (#586): challenge text must
                        # not reach pages or index payloads.
                        log_refusal(url, result)
                        checks = ((result.get("data") or {}).get("quality") or {}).get(
                            "checks"
                        ) or {}
                        errors_by_index[index] = {
                            "url": url,
                            "error": (
                                f"Barrier/challenge content detected "
                                f"(warning={result.get('warning')!r}, "
                                f"block_detected={checks.get('block_detected')!r})"
                            ),
                            "error_type": "barrier_detected",
                            "error_code": "BARRIER_DETECTED",
                            "timestamp": time.strftime(
                                "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
                            ),
                        }
                    elif result.get("success"):
                        data = result["data"]
                        pages_by_index[index] = {
                            "url": url,
                            "markdown": data.get("markdown", ""),
                        }
                        metadata = data.get("metadata") or {}
                        og = metadata.get("og") or {}
                        meta = metadata.get("meta") or {}
                        title = (
                            og.get("title")
                            or meta.get("title")
                            or data.get("title", "")
                        )
                        index_batch_by_index[index] = {
                            "url": url,
                            "title": title,
                            "content": data.get("markdown", "")[:2000],
                        }
                        store.increment_completed(job_id)
                    else:
                        error_message = result.get("error", "Scrape failed")
                        error_code = result.get("error_code") or "SCRAPE_ERROR"
                        errors_by_index[index] = {
                            "url": url,
                            "error": error_message,
                            "error_type": (
                                "captcha_unresolved"
                                if error_code == "CAPTCHA_UNRESOLVED"
                                else "scrape_error"
                            ),
                            "error_code": error_code,
                            "timestamp": time.strftime(
                                "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
                            ),
                        }

                # Order-preserving progress update after each completion.
                store.update_job_progress(
                    job_id,
                    pages=[pages_by_index[i] for i in sorted(pages_by_index)],
                    errors=[errors_by_index[i] for i in sorted(errors_by_index)],
                    total=total,
                )

        workers = [
            asyncio.create_task(_scrape_worker()) for _ in range(effective_concurrency)
        ]
        pending = set(workers)
        try:
            while pending:
                done, pending = await asyncio.wait(
                    pending, return_when=asyncio.FIRST_COMPLETED
                )
                for task in done:
                    try:
                        task.result()
                    except (JobCancelledError, asyncio.CancelledError):
                        raise
                    except Exception:
                        logger.warning(
                            "Batch scrape worker failed unexpectedly", exc_info=True
                        )
        finally:
            # Await all remaining (possibly cancelled) workers so no
            # speculative scrape/browser/HTTP task is destroyed pending.
            if pending:
                for p in pending:
                    p.cancel()
                await asyncio.gather(*pending, return_exceptions=True)

        pages = [pages_by_index[i] for i in sorted(pages_by_index)]
        errors = [errors_by_index[i] for i in sorted(errors_by_index)]
        _index_batch = [index_batch_by_index[i] for i in sorted(index_batch_by_index)]

        if _index_batch:
            if task_tracker is not None:
                task_tracker.create_background_task(_index_batch_async(_index_batch))
            else:
                asyncio.create_task(_index_batch_async(_index_batch))

        return {
            "completed": store.get_completed(job_id),
            "total": total,
            "pages": pages,
            "errors": errors,
        }

    await _run_job_with_observability(
        job_id, "batch_scrape", store, webhook_config, work_fn, scraper.close
    )


async def _process_extract_async(
    job_id: str,
    urls: list[str],
    prompt: str | None,
    schema_: dict[str, Any] | None,
    llm_base_url: str,
    llm_api_key: str,
    llm_model: str,
    scraper_url: str,
    webhook_config: dict[str, Any] | None = None,
) -> None:
    settings = _get_worker_settings()
    store = JobStore(
        f"redis://{settings.valkey_host}:{settings.valkey_port}/{settings.valkey_db}"
    )

    async def work_fn() -> dict[str, Any]:
        return await run_extract(
            urls=urls,
            prompt=prompt,
            schema=schema_,
            scraper_url=scraper_url,
            llm_base_url=llm_base_url,
            llm_api_key=llm_api_key,
            llm_model=llm_model,
        )

    await _run_job_with_observability(job_id, "extract", store, webhook_config, work_fn)


async def _process_llmstxt_async(
    job_id: str,
    url: str,
    max_pages: int,
    scraper_url: str,
    webhook_config: dict[str, Any] | None = None,
) -> None:
    settings = _get_worker_settings()
    store = JobStore(
        f"redis://{settings.valkey_host}:{settings.valkey_port}/{settings.valkey_db}"
    )

    async def work_fn() -> dict[str, Any]:
        from .llmstxt import generate_llmstxt

        return await generate_llmstxt(url, max_pages, scraper_url)

    await _run_job_with_observability(job_id, "llmstxt", store, webhook_config, work_fn)


async def _index_page_async(url: str, title: str, content: str) -> None:
    """Fire-and-forget index a page in the vector index.

    Failure is logged but never propagated — indexing is best-effort.
    """
    try:
        from .semantic_client import SemanticClient

        settings = load_settings()
        semantic_url = settings.semantic_url
        client = SemanticClient(semantic_url)
        await client.index_page(url, title, content)
        await client.close()
        logger.debug("Indexed %s", url)
    except Exception:
        logger.debug("Failed to index %s (vector index unavailable or full)", url)


async def _index_batch_async(pages: list[dict]) -> None:
    """Fire-and-forget batch-index multiple pages.

    Ref: ADR-0030. For large crawls, this is ~200x faster than
    calling _index_page_async() per page.
    """
    if not pages:
        return
    try:
        from .semantic_client import SemanticClient

        settings = load_settings()
        semantic_url = settings.semantic_url
        client = SemanticClient(semantic_url)
        await client.index_batch(pages)
        await client.close()
        logger.debug("Batch-indexed %d pages", len(pages))
    except Exception:
        logger.debug(
            "Failed to batch-index %d pages (vector index unavailable)", len(pages)
        )


async def _process_plan_execution_async(
    job_id: str,
    prompt: str,
    plan: dict[str, Any],
    modifications: dict[str, Any] | None,
    llm_base_url: str,
    llm_api_key: str,
    llm_model: str,
    searxng_url: str,
    scraper_url: str,
    webhook_config: dict[str, Any] | None = None,
) -> None:
    """Process a plan execution job asynchronously (sync/polling path).

    Follows the plan phases: search → scrape → synthesize in sequence,
    guided by the plan structure and optional modifications.

    Args:
        job_id: The job UUID for status polling.
        prompt: The original user research prompt.
        plan: The plan dict with ``phases``, ``estimated_sources``, ``dimensions``.
        modifications: Optional unified modifications dict.
        llm_base_url: LLM API base URL.
        llm_api_key: LLM API key.
        llm_model: LLM model name.
        searxng_url: SearXNG base URL.
        scraper_url: Scraper service base URL.
        webhook_config: Optional webhook configuration.
    """
    settings = _get_worker_settings()
    redis_url = (
        f"redis://{settings.valkey_host}:{settings.valkey_port}/{settings.valkey_db}"
    )
    store = JobStore(redis_url)

    async def work_fn() -> dict[str, Any]:
        from .llm import LLMClient
        from .research import _scrape_urls
        from .scraper_client import ScraperClient
        from .searxng_client import SearXNGClient

        llm = LLMClient(
            base_url=llm_base_url,
            api_key=llm_api_key,
            model=llm_model,
        )
        searxng = SearXNGClient(searxng_url)
        scraper = ScraperClient(scraper_url)

        all_sources: list[dict] = []
        accumulated_context_parts: list[str] = []
        seen_urls: set[str] = set()
        full_synthesis = ""

        try:
            phases = plan.get("phases", [])
            for phase_idx, phase in enumerate(phases):
                # Check for cancellation between phases
                job_meta = store.get_job(job_id)
                if job_meta and job_meta.get("status") == "cancelled":
                    logger.info(
                        "Plan execution %s cancelled at phase %d/%d",
                        job_id,
                        phase_idx + 1,
                        len(phases),
                    )
                    raise JobCancelledError("plan execution cancelled via DELETE")

                action = phase.get("action", "search")
                description = phase.get("description", "")

                if action == "search":
                    query = description or prompt
                    if modifications and modifications.get("narrow"):
                        query = f"{modifications.get('narrow')} {query}"
                    # Apply modify_query modifications for this specific phase
                    if modifications and modifications.get("modify_queries"):
                        for mq in modifications["modify_queries"]:
                            if mq.get("phase_index") == phase_idx and mq.get(
                                "new_query"
                            ):
                                query = mq["new_query"]
                                break

                    try:
                        results, _health = await searxng.search(
                            query, limit=10, raise_on_rate_limit=True
                        )
                    except Exception as e:
                        from .exceptions import RetryableRateLimitError

                        if isinstance(e, RetryableRateLimitError):
                            # Downstream capacity condition: propagate so the
                            # worker schedules a bounded retry (ADR-0053).
                            raise
                        results = []

                    new_urls = []
                    for r in results:
                        url = r.get("url", "")
                        if url and url not in seen_urls:
                            seen_urls.add(url)
                            new_urls.append(url)
                            all_sources.append(
                                {
                                    "url": url,
                                    "title": r.get("title", ""),
                                    "relevance": r.get("description", ""),
                                }
                            )

                    if new_urls:
                        artifacts = await _scrape_urls(
                            new_urls[:5],
                            scraper,
                            min_sources=1,
                            max_attempts=min(5, len(new_urls)),
                        )
                        for artifact in artifacts:
                            accumulated_context_parts.append(artifact.to_document())

                elif action == "synthesize":
                    context = (
                        "\n\n---\n\n".join(accumulated_context_parts)
                        if accumulated_context_parts
                        else ""
                    )
                    synthesis_prompt = (
                        description or f"Synthesise findings for: {prompt}"
                    )

                    dimensions = list(
                        plan.get("comparison_dimensions", plan.get("dimensions", []))
                    )
                    if modifications:
                        if modifications.get("add_dimension"):
                            for d in modifications.get("add_dimension", []):
                                if d not in dimensions:
                                    dimensions.append(d)
                        if modifications.get("remove_dimension"):
                            dimensions = [
                                d
                                for d in dimensions
                                if d
                                not in (modifications.get("remove_dimension") or [])
                            ]

                    if dimensions:
                        dims_str = ", ".join(dimensions)
                        synthesis_prompt = (
                            f"{synthesis_prompt}\n\n"
                            f"CRITICAL: Address each of these analysis dimensions: {dims_str}. "
                            f"For each dimension, provide specific evidence from the sources below."
                        )

                    synthesis_prompt = (
                        f"{synthesis_prompt}\n\n"
                        f"Base your answer ONLY on the source content provided below. "
                        f"Cite sources by their URL. Be thorough and precise."
                    )

                    full_synthesis = await llm.generate(
                        system_prompt=(
                            "You are GroktoCrawl, a research synthesis agent. "
                            "Synthesise information from the provided sources into a "
                            "comprehensive, well-structured answer. Be thorough, precise, "
                            "and cite specific sources."
                        ),
                        user_prompt=synthesis_prompt,
                        context=context or None,
                        stage="plan_execute",
                    )
                    if full_synthesis:
                        accumulated_context_parts.append(full_synthesis)

            return {
                "result": full_synthesis,
                "sources": [s.get("url", "") for s in all_sources],
                "source_details": all_sources,
                "phases_completed": len(phases),
                "total_sources": len(all_sources),
            }

        finally:
            await llm.close()
            await searxng.close()
            await scraper.close()

    await _run_job_with_observability(
        job_id, "plan_execute", store, webhook_config, work_fn
    )
