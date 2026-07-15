"""Worker entrypoint and processing functions for GroktoCrawl jobs."""

import asyncio
import logging
import os
import time
from collections.abc import Callable, Coroutine
from typing import Any

from .metrics import METRICS
from .research import run_extract, run_research
from .scraper_client import ScraperClient
from .settings import load_settings
from .store import JobStore
from .webhook import deliver_webhook

logger = logging.getLogger(__name__)


def _get_worker_settings() -> Any:
    return load_settings()


async def _run_job_with_observability(
    job_id: str,
    job_type: str,
    store: JobStore,
    webhook_config: dict[str, Any] | None,
    work_fn: Callable[[], Coroutine[Any, Any, Any]],
    cleanup_fn: Callable[[], Coroutine[Any, Any, None]] | None = None,
) -> None:
    """Execute work_fn with standard observability scaffolding.

    Encapsulates metrics recording, store completion/failure, webhook
    delivery, and cleanup — the identical scaffolding shared by all
    worker processing functions.
    """
    start = time.monotonic()
    METRICS.counter("jobs_submitted_total", "Total jobs submitted", ["type"]).inc(
        {"type": job_type}
    )
    try:
        result = await work_fn()
        store.complete_job(job_id, result)
        await deliver_webhook(webhook_config, "completed", job_id, result)
        elapsed = time.monotonic() - start
        METRICS.histogram(
            "job_duration_seconds", "Job processing duration", ["type", "status"]
        ).observe({"type": job_type, "status": "completed"}, elapsed)
        METRICS.counter("jobs_completed_total", "Total completed jobs", ["type"]).inc(
            {"type": job_type}
        )
        logger.info("%s job %s completed in %.2fs", job_type, job_id, elapsed)
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
    citation_style: Any = None,
    force_fresh: bool = False,
    user_id: str | None = None,
    research_memory: Any = None,
    search_type: str = "deep",
) -> None:
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
    if not force_fresh:
        try:
            cache_result = await research_memory.query(
                prompt=prompt,
                user_id=user_id if memory_scope == "per_user" else None,
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
            citation_style=cs,
            search_type=search_type,
        )
        # Apply citation style to transform bare [N] markers to [N](url)
        # for compact style, or leave them unchanged for inline style.
        source_details = result.get("source_details", [])
        from .research import _apply_citation_style

        result_text, _ = _apply_citation_style(result["result"], source_details, cs)
        result["result"] = result_text

        # Save rich source_details for memory storage BEFORE compactifying
        # the API response. Cache-hit paths expect sources as list[dict].
        rich_source_details = source_details or result.get("source_details", [])

        # When citation_style is compact, replace full source_details with
        # a compact citations mapping (index → {url}) to reduce
        # response payload size.
        if cs == CitationStyle.compact:
            compact_sources: list[dict[str, str | int]] = []
            for i, src in enumerate(source_details, start=1):
                compact_sources.append(
                    {
                        "index": i,
                        "url": src.get("url", ""),
                    }
                )
            result["sources_compact"] = compact_sources
            # Drop the full source_details from the API response to save payload size
            result["source_details"] = []

        # ── Store fresh result in research memory ──────────────────
        try:
            answer = result.get("result", "")
            # Use rich_source_details (preserved before compactification) for memory
            store_sources = rich_source_details or result.get("sources", [])
            if not store_sources:
                store_sources = result.get("sources", [])

            # ── Cache-admission guard ──────────────────────────
            # Skip store for empty answers, handled LLM errors,
            # or sourceless results (#432).
            if answer and not answer.startswith("Error:") and store_sources:
                metadata: dict[str, Any] = {
                    "model": llm_model,
                    "citation_style": cs.value,
                }
                if requested_model and requested_model != "default":
                    metadata["requested_model"] = requested_model
                if hasattr(result, "get"):
                    metadata["latency_ms"] = result.get("latency_ms", 0)

                memory_user_id = user_id if memory_scope == "per_user" else None
                artifact_id = await research_memory.store(
                    prompt=prompt,
                    artifact=answer,
                    sources=store_sources,
                    model=llm_model,
                    user_id=memory_user_id,
                    metadata=metadata,
                )
                logger.info(
                    "Stored research memory artifact %s for agent %s (scope=%s)",
                    artifact_id,
                    job_id,
                    memory_scope,
                )
                result["research_memory_id"] = artifact_id
            else:
                logger.info(
                    "Skipping research memory store for agent %s "
                    "(empty=%s, error=%s, no_sources=%s)",
                    job_id,
                    not answer,
                    answer.startswith("Error:") if answer else False,
                    not store_sources,
                )
        except Exception:
            logger.warning(
                "Failed to store research memory for agent %s (service may be down)",
                job_id,
                exc_info=True,
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
    scraper = ScraperClient(scraper_url)
    start = time.monotonic()
    job_type = "crawl"

    METRICS.counter("jobs_submitted_total", "Total jobs submitted", ["type"]).inc(
        {"type": job_type}
    )

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

        elapsed = time.monotonic() - start

        # ── Existing job-type-agnostic metrics (keep for backward compat) ──
        METRICS.histogram(
            "job_duration_seconds", "Job processing duration", ["type", "status"]
        ).observe({"type": job_type, "status": "completed"}, elapsed)
        METRICS.counter("jobs_completed_total", "Total completed jobs", ["type"]).inc(
            {"type": job_type}
        )

        # ── Crawl-specific metrics ──────────────────────────────────────────
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

        logger.info("Crawl job %s completed in %.2fs", job_id, elapsed)

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
        await scraper.close()


async def _process_batch_scrape_async(
    job_id: str,
    urls: list[str],
    scraper_url: str,
    webhook_config: dict[str, Any] | None = None,
    task_tracker: Any = None,
) -> None:
    settings = _get_worker_settings()
    store = JobStore(
        f"redis://{settings.valkey_host}:{settings.valkey_port}/{settings.valkey_db}"
    )
    scraper = ScraperClient(scraper_url)

    async def work_fn() -> dict[str, Any]:
        pages: list[dict] = []
        errors: list[dict] = []
        _index_batch: list[dict] = []
        total = len(urls)
        for url in urls:
            # Check for cancellation between URLs
            job_meta = store.get_job(job_id)
            if job_meta and job_meta.get("status") == "cancelled":
                logger.info(
                    "Batch scrape %s cancelled after %d/%d URLs",
                    job_id,
                    len(pages),
                    total,
                )
                break

            try:
                result = await scraper.scrape(url)
            except Exception as e:
                errors.append(
                    {
                        "url": url,
                        "error": str(e),
                        "error_type": "scrape_error",
                        "error_code": "SCRAPE_ERROR",
                        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    }
                )
                store.update_job_progress(
                    job_id,
                    pages=list(pages),
                    errors=list(errors),
                    total=total,
                )
                continue

            if result.get("success"):
                data = result["data"]
                pages.append({"url": url, "markdown": data.get("markdown", "")})
                metadata = data.get("metadata") or {}
                og = metadata.get("og") or {}
                meta = metadata.get("meta") or {}
                title = og.get("title") or meta.get("title") or data.get("title", "")
                _index_batch.append(
                    {
                        "url": url,
                        "title": title,
                        "content": data.get("markdown", "")[:2000],
                    }
                )
                store.increment_completed(job_id)
            else:
                error_message = result.get("error", "Scrape failed")
                error_code = result.get("error_code") or "SCRAPE_ERROR"
                errors.append(
                    {
                        "url": url,
                        "error": error_message,
                        "error_type": (
                            "captcha_unresolved"
                            if error_code == "CAPTCHA_UNRESOLVED"
                            else "scrape_error"
                        ),
                        "error_code": error_code,
                        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    }
                )

            # Update progress after each URL for real-time status polling
            store.update_job_progress(
                job_id,
                pages=list(pages),
                errors=list(errors),
                total=total,
            )

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
                    break

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
                        results, _health = await searxng.search(query, limit=10)
                    except Exception:
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
                        scraped_docs, scraped_details = await _scrape_urls(
                            new_urls[:5],
                            scraper,
                            min_sources=1,
                            max_attempts=min(5, len(new_urls)),
                        )
                        for doc, _detail in zip(
                            scraped_docs, scraped_details, strict=False
                        ):
                            accumulated_context_parts.append(doc)

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
