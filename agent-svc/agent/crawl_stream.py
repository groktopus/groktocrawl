"""SSE streaming for crawl progress.

Defines the async generator that powers the ``POST /v2/crawl?stream=true``
endpoint. The generator runs the crawl engine inline, intercepting each
scraped page via the ``page_callback`` hook and yielding SSE events as
they happen.

SSE events delivered:
    - ``page``: per-page data with ``url``, ``markdown``, ``metadata``
    - ``progress``: periodic progress with ``completed``/``total`` counts
    - ``done``: final result with summary stats
    - ``error``: per-page failure or overall crawl failure
    - ``: heartbeat``: keepalive comment during idle periods (every 15s)

Each event includes an ``id:`` field (monotonically increasing) for SSE
reconnection support.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncGenerator
from typing import Any

from common.stage_metrics import StreamTiming

from .crawler import CrawlEngine, CrawlOptions, CrawlResult
from .metrics import METRICS
from .scraper_client import ScraperClient
from .store import JobStore
from .webhook import deliver_webhook
from .workload_metrics import record_job_cancelled, record_job_end, record_job_start

logger = logging.getLogger(__name__)

_HEARTBEAT_INTERVAL = 15.0  # seconds between heartbeat/progress events
_PROGRESS_INTERVAL = 5  # minimum pages between progress events


async def crawl_event_stream(
    job_id: str,
    url: str,
    max_pages: int,
    max_depth: int,
    scraper_url: str,
    store: JobStore,
    task_tracker: Any | None = None,
    webhook_config: dict[str, Any] | None = None,
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
    max_duration_seconds: int = 0,
    idle_timeout_seconds: int = 300,
) -> AsyncGenerator[str, None]:
    """Run a crawl and yield SSE-formatted event strings.

    This generator runs the crawl engine inline, using an ``asyncio.Queue``
    to bridge the engine's ``page_callback`` with the SSE event stream.
    Progress and heartbeat events are emitted during idle periods.

    Args:
        job_id: The crawl job ID (already created in Valkey).
        url: The start URL to crawl.
        max_pages: Maximum pages to scrape.
        max_depth: Maximum link-follow depth.
        scraper_url: Base URL for the scraper service.
        store: JobStore instance for progress updates.
        task_tracker: Optional TaskTracker for fire-and-forget tasks.
        webhook_config: Optional webhook configuration.
        **kwargs: Additional crawl options forwarded to CrawlOptions.

    Yields:
        SSE-formatted event strings::

            id: <n>
            data: {json payload}

        And heartbeat comments during idle periods::

            : heartbeat

    Raises:
        Any exception from the crawl engine is caught and yielded as an
        ``error`` SSE event. The generator then terminates.
    """
    event_id = 0
    start_time = time.monotonic()
    timing = StreamTiming("crawl")

    # Queue bridging crawl engine page_callback → SSE generator
    page_queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

    scraper = ScraperClient(scraper_url)
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
        max_duration_seconds=max_duration_seconds,
        idle_timeout_seconds=idle_timeout_seconds,
        scrape_options=scrape_options,
    )

    # ── Fire crawl.started webhook ────────────────────────────
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

    # ── Page callback: push into queue ────────────────────────
    async def _page_callback(_job_id: str, page: dict[str, Any]) -> None:
        await page_queue.put(page)

    # ── Error callback: push error into queue with sentinel ───
    async def _error_callback(_job_id: str, error: dict[str, Any]) -> None:
        """Push a scrape error into the page queue with a sentinel key.

        The main event loop distinguishes error events from page events
        by checking for the ``_error`` sentinel key.
        """
        error["_error"] = True
        await page_queue.put(error)

    engine = CrawlEngine(scraper, store=store, options=options)

    crawl_task: asyncio.Task[CrawlResult] | None = None
    pages_yielded = 0
    last_progress_count = 0

    # Workload-class telemetry: stream mode is the primary crawl path (CLI
    # default), so it must touch the same active-jobs gauge the sync polling
    # path does in _process_crawl_async.
    record_job_start("crawl")

    try:
        # Start the crawl as a background task
        crawl_task = asyncio.create_task(
            engine.run(
                url,
                job_id=job_id,
                page_callback=_page_callback,
                error_callback=_error_callback,
            )
        )

        # Main event loop: read from page_queue with heartbeat timeout
        while not crawl_task.done() or not page_queue.empty():
            try:
                page_data = await asyncio.wait_for(
                    page_queue.get(), timeout=_HEARTBEAT_INTERVAL
                )
            except TimeoutError:
                # No page event — send heartbeat and/or progress
                # Always send heartbeat comment to keep connection alive
                yield ": heartbeat\n\n"

                # Also send a progress event if we have newer data
                completed = pages_yielded
                # Estimate total from store if available
                job_meta = store.get_job(job_id) if store else None
                total_est = max(completed, max_pages)
                if job_meta:
                    data_payload = job_meta.get("data") or {}
                    total_est = data_payload.get("total", max_pages)

                progress_payload = {
                    "type": "progress",
                    "completed": completed,
                    "total": total_est,
                    "status": "scraping",
                }
                timing.on_first_event()
                event_id += 1
                yield f"id: {event_id}\ndata: {json.dumps(progress_payload)}\n\n"
                continue

            if page_data is None:
                # Sentinel value (not currently used, but available for future)
                continue

            if page_data.get("_error"):
                # ── Yield error event ──────────────────────────
                error_payload = {
                    "type": "error",
                    "url": page_data.get("url", ""),
                    "error": page_data.get("error", "Unknown scrape error"),
                }
                timing.on_first_event()
                event_id += 1
                yield f"id: {event_id}\ndata: {json.dumps(error_payload)}\n\n"
                continue

            # ── Yield page event ────────────────────────────────
            page_payload = {
                "type": "page",
                "url": page_data.get("url", ""),
                "markdown": page_data.get("markdown", ""),
                "metadata": page_data.get("metadata", {}),
            }
            timing.on_first_event()
            event_id += 1
            yield f"id: {event_id}\ndata: {json.dumps(page_payload)}\n\n"
            pages_yielded += 1

            # Periodically send progress event (every _PROGRESS_INTERVAL pages)
            if pages_yielded - last_progress_count >= _PROGRESS_INTERVAL:
                last_progress_count = pages_yielded
                progress_payload = {
                    "type": "progress",
                    "completed": pages_yielded,
                    "total": max(
                        pages_yielded, engine._scraped_count + len(engine._queue)
                    ),
                    "status": "scraping",
                }
                event_id += 1
                yield f"id: {event_id}\ndata: {json.dumps(progress_payload)}\n\n"

        # ── Crawl completed — get result ──────────────────────
        result = await crawl_task
        elapsed = time.monotonic() - start_time
        elapsed_ms = int(elapsed * 1000)

        payload: dict[str, object] = {
            "completed": result.completed,
            "total": result.total,
            "pages": result.pages,
            "errors": result.errors,
            "robots_blocked": result.robots_blocked,
        }

        # Mirror the sync path (_process_crawl_async): a crawl cancelled via
        # DELETE has status="cancelled" in the store, while a normal run is
        # still "processing" here. Branch on store status rather than the
        # exception type so both cancellation triggers are handled the same.
        job_meta = store.get_job(job_id)
        was_cancelled = job_meta is not None and job_meta.get("status") == "cancelled"

        if was_cancelled:
            # Store is already marked cancelled by cancel_job(); do NOT
            # overwrite it with complete_job(). Fire crawl.completed and
            # record the workload-class cancellation signal.
            record_job_cancelled("crawl")
            logger.info("Crawl %s was cancelled — preserving cancelled status", job_id)
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
        else:
            store.complete_job(job_id, payload)
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

        # ── Job-type-agnostic metrics (backward compat) ─────────
        # A cancelled crawl must not be double-counted as completed.
        if not was_cancelled:
            METRICS.histogram(
                "job_duration_seconds", "Job processing duration", ["type", "status"]
            ).observe({"type": "crawl", "status": "completed"}, elapsed)
            METRICS.counter(
                "jobs_completed_total", "Total completed jobs", ["type"]
            ).inc({"type": "crawl"})

        # ── Crawl-specific metrics ──────────────────────────────
        crawl_status = "cancelled" if was_cancelled else "completed"
        METRICS.counter(
            "groktocrawl_crawl_jobs_total", "Total crawl jobs by status", ["status"]
        ).inc({"status": crawl_status})
        METRICS.histogram(
            "groktocrawl_crawl_duration_seconds",
            "Crawl job duration in seconds",
            ["status"],
        ).observe({"status": crawl_status}, elapsed)
        METRICS.counter(
            "groktocrawl_crawl_pages_scraped_total",
            "Total pages scraped by crawl jobs",
        ).inc(value=float(result.completed))

        done_payload = {
            "type": "done",
            "id": job_id,
            "status": crawl_status,
            "pages": result.pages,
            "total": result.total,
            "completed": result.completed,
            "latency_ms": elapsed_ms,
        }
        timing.on_first_event()
        event_id += 1
        yield f"id: {event_id}\ndata: {json.dumps(done_payload)}\n\n"

    except asyncio.CancelledError:
        logger.info("Crawl SSE stream cancelled for job %s", job_id)
        # A client disconnect can arrive either mid-crawl (store still
        # "processing") or between store.complete_job() and the done yield
        # (store already terminal). Only the former is a real cancellation:
        # re-read the store and skip the cancelled counter/webhook when the
        # job has already reached a terminal status so a completed crawl is
        # never double-counted as cancelled.
        job_meta = store.get_job(job_id)
        already_terminal = job_meta is not None and job_meta.get("status") in (
            "completed",
            "cancelled",
            "failed",
        )
        if not already_terminal:
            record_job_cancelled("crawl")
            store.cancel_job(job_id)
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
        if crawl_task and not crawl_task.done():
            crawl_task.cancel()
            import contextlib as _ctxlib

            with _ctxlib.suppress(Exception):
                await crawl_task
        raise

    except Exception as exc:
        logger.exception("Crawl SSE stream failed for job %s", job_id)
        # Mark job as failed and fire webhook (matching sync path)
        store.fail_job(job_id, str(exc))
        if task_tracker is not None:
            task_tracker.create_background_task(
                deliver_webhook(
                    webhook_config,
                    "crawl.failed",
                    job_id,
                    data=[],
                    success=False,
                    error=str(exc),
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
                error=str(exc),
            )
        error_payload = {
            "type": "error",
            "url": url,
            "error": str(exc),
        }
        timing.on_first_event()
        event_id += 1
        yield f"id: {event_id}\ndata: {json.dumps(error_payload)}\n\n"
    finally:
        record_job_end("crawl")
        if crawl_task and not crawl_task.done():
            crawl_task.cancel()
            import contextlib as _ctxlib

            with _ctxlib.suppress(Exception):
                await crawl_task
        await scraper.close()
        await engine.close()
