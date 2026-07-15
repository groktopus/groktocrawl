"""CrawlEngine — BFS crawl orchestrator for GroktoCrawl.

Manages a queue of (url, depth) tuples, tracks seen URLs for dedup,
enforces ``max_pages`` and ``max_depth`` limits, uses the shared
``LinkExtractor`` to discover child links, integrates with
``ScraperClient`` for per-page fetching, and writes progress to the
job store for status polling.
"""

from __future__ import annotations

import asyncio
import collections.abc
import contextlib
import logging
import posixpath
import re
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.parse import urlparse, urlunparse

import httpx

from common.url import is_private_host

from .crawl_cache import CrawlCache
from .dedup import DedupManager
from .link_extractor import classify_links, extract_links, filter_links
from .scraper_client import ScraperClient
from .sitemap_parser import SitemapParser
from .store import JobStore

logger = logging.getLogger(__name__)

# ── Sitemap URL filter ──────────────────────────────────────────────
# Path patterns that universally indicate index/aggregation pages across
# Ghost, Hugo, WordPress, Drupal, and similar CMS platforms.
# These pages produce thin or duplicate content and are skipped rather
# than queued.
_SITEMAP_SKIP_PATTERNS: list[re.Pattern] = [
    re.compile(r"/tags?/", re.IGNORECASE),
    re.compile(r"/categories?/", re.IGNORECASE),
    re.compile(r"/authors?/", re.IGNORECASE),
    re.compile(r"/page/\d+", re.IGNORECASE),
    re.compile(r"/search/?$", re.IGNORECASE),
]


def _is_low_value_sitemap_url(url: str) -> bool:
    """Check whether a sitemap URL points to an index/aggregation page.

    These pages (tags, categories, authors, pagination) universally
    produce thin or duplicate content across CMS platforms. Skipping
    them at queue time prevents the output cap from filling with
    near-identical index pages before real content is crawled.
    """
    try:
        path = urlparse(url).path
    except Exception:
        return False
    return any(pattern.search(path) for pattern in _SITEMAP_SKIP_PATTERNS)


def _now_timestamp() -> str:
    """Return the current UTC time as an ISO 8601 timestamp string."""
    return datetime.now(UTC).isoformat()


def _classify_scrape_error(error_msg: str, error_code: str = "") -> str:
    """Classify a scrape error message into a machine-readable error type.

    Maps error strings from the scraper-svc and httpx to standardised
    error types used by the ``/v2/crawl/{id}/errors`` endpoint:

    - ``dns_error``: DNS resolution failures
    - ``connection_error``: Connection refused / reset / closed
    - ``http_error``: HTTP 4xx/5xx status codes
    - ``timeout``: Request-level timeouts (not per-page scrape timeouts,
      which are caught separately as ``asyncio.TimeoutError``)
    - ``scrape_error``: Generic/fallback scrape failures

    Args:
        error_msg: The error message string from the scraper response.
        error_code: Optional machine-readable error code from the scraper.

    Returns:
        A standardised error type string.
    """
    # If the scraper-svc provided a structured error_code, use it.
    code_map = {
        "TIMEOUT": "timeout",
        "UPSTREAM_ERROR": "http_error",
        "INVALID_REQUEST": "scrape_error",
        "AUTH_ERROR": "http_error",
        "CAPTCHA_UNRESOLVED": "captcha_unresolved",
    }
    if error_code in code_map:
        return code_map[error_code]

    msg_lower = error_msg.lower()

    # DNS resolution failures
    if any(
        pattern in msg_lower
        for pattern in [
            "name resolution",
            "getaddrinfo",
            "dns",
            "no address found",
            "nodename nor servname provided",
            "temporary name error",
            "name or service not known",
        ]
    ):
        return "dns_error"

    # Connection-level errors
    if any(
        pattern in msg_lower
        for pattern in [
            "connection refused",
            "connection reset",
            "connection closed",
            "connection aborted",
            "connect call failed",
            "no route to host",
            "network is unreachable",
            "connection timed out",
        ]
    ):
        return "connection_error"

    # HTTP errors
    if any(
        pattern in msg_lower
        for pattern in [
            "http 4",
            "http 5",
            "status code 4",
            "status code 5",
            "400 bad request",
            "401 unauthorized",
            "403 forbidden",
            "404 not found",
            "500 internal",
            "502 bad gateway",
            "503 service unavailable",
        ]
    ):
        return "http_error"

    # Scraper-level timeouts (caught in fetch tiers)
    if "timed out" in msg_lower or "timeout" in msg_lower:
        return "timeout"

    return "scrape_error"


# ── Data types ───────────────────────────────────────────────────


@dataclass
class CrawlOptions:
    """Options controlling crawl behavior.

    Attributes:
        max_pages: Maximum number of pages to scrape (hard stop).
        max_depth: Maximum link-follow depth. Pages at max_depth are
            scraped but their links are not followed.
        include_paths: If set, only URLs whose path matches at least one
            of these glob/regex patterns are scraped.
        exclude_paths: URLs whose path matches any of these glob/regex
            patterns are excluded (takes precedence over include).
        ignore_query_parameters: If True, query-string variants of the
            same base URL are collapsed to one fetch.
        regex_on_full_url: If True, include/exclude paths are treated as
            regex patterns (default: glob).
        verbose: If True, track filtered-out URLs with reasons.
        allow_subdomains: If True, follow links to subdomains.
        allow_external_links: If True, follow links to external domains.
        max_concurrency: Maximum concurrent page scrapes (1-50, capped).
            When delay is set, this is forced to 1.
        delay: Seconds to wait between successive scrapes. When set,
            forces max_concurrency to 1.
        ignore_robots_txt: If True, bypass robots.txt enforcement. All
            discovered URLs are scraped regardless of robots.txt Disallow
            rules. Politeness rate limiting (crawl-delay) still applies.
        robots_user_agent: Custom User-Agent string for robots.txt
            evaluation. When set, robots.txt rules are evaluated against
            this User-Agent instead of the default bot UA.
        scrape_options: Optional dict of per-page scrape options (formats,
            only_main_content, include_tags, exclude_tags, wait_for, mobile,
            timeout, headers, remove_base64_images). Forwarded to scraper-svc
            on every page fetch.
    """

    max_pages: int = 10
    max_depth: int = 2
    include_paths: list[str] | None = None
    exclude_paths: list[str] | None = None
    ignore_query_parameters: bool = False
    regex_on_full_url: bool = False
    verbose: bool = False
    sitemap_mode: str = "include"  # "include" | "skip" | "only"
    allow_subdomains: bool = False
    allow_external_links: bool = False
    crawl_entire_domain: bool = False
    max_duration_seconds: int = 1800
    idle_timeout_seconds: int = 300
    max_concurrency: int = 3
    delay: float | None = None
    ignore_robots_txt: bool = False
    robots_user_agent: str | None = None
    scrape_options: dict | None = None


@dataclass
class CrawlResult:
    """Result of a crawl run."""

    pages: list[dict] = field(default_factory=list)
    total: int = 0
    completed: int = 0
    errors: list[dict] = field(default_factory=list)
    robots_blocked: list[dict] = field(default_factory=list)
    filtered_out: list[dict] = field(default_factory=list)


# ── Exceptions ──────────────────────────────────────────────────


class StartUrlScrapeError(Exception):
    """Raised when the start URL cannot be scraped."""

    def __init__(self, url: str, error: str):
        self.url = url
        self.error = error
        super().__init__(f"Start URL scrape failed for {url}: {error}")


# ── Public functions ─────────────────────────────────────────────


def _clean_path(path: str) -> str:
    """Clean up a URL path by collapsing dot segments and normalizing.

    Collapses ``/./`` and ``/../`` segments, and reduces ``/.`` at the
    end to ``/``.
    """
    if not path:
        return "/"

    # posixpath.normpath collapses dot segments
    cleaned = posixpath.normpath(path)

    # normpath strips trailing slash, but root should remain /
    if not cleaned:
        return "/"

    return cleaned


def normalize_url(url: str, ignore_query_parameters: bool = False) -> str:
    """Normalize a URL for dedup within a crawl run.

    Steps:
        1. Strip fragment identifier.
        2. Lowercase scheme and host.
        3. Remove default ports (80 for http, 443 for https).
        4. Normalize trailing slash (remove for non-root paths).
        5. Collapse ``/./`` and ``/../`` path segments.
        6. Optionally strip query string entirely.
        7. Sort query parameters if keeping them.

    Args:
        url: The URL to normalize.
        ignore_query_parameters: If True, strip the query string.

    Returns:
        Normalized URL string.
    """
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    hostname = parsed.hostname.lower() if parsed.hostname else ""
    port = parsed.port
    path = _clean_path(parsed.path or "/")

    # Build netloc without default ports
    netloc = hostname
    if port is not None and not (
        (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    ):
        netloc = f"{hostname}:{port}"

    # Normalize trailing slash for non-root paths
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")

    # Handle query parameters
    if ignore_query_parameters:
        query = ""
    else:
        # Sort query params for consistency
        parsed_qs = parsed.query
        if parsed_qs:
            params = sorted(parsed_qs.split("&"))
            query = "&".join(params)
        else:
            query = ""

    normalized = urlunparse((scheme, netloc, path, parsed.params, query, ""))
    return normalized


async def fetch_html(url: str, timeout: float = 15.0) -> str | None:
    """Fetch raw HTML from a URL for link extraction purposes.

    This is a lightweight HTTP GET that follows redirects and returns
    the response text. It is used by ``CrawlEngine`` to discover child
    links from scraped pages, separate from the content scraping done
    via ``ScraperClient``.

    Args:
        url: The URL to fetch.
        timeout: HTTP request timeout in seconds.

    Returns:
        The response text (HTML), or ``None`` if the fetch failed.
    """
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                return resp.text
            logger.debug("fetch_html got status %d for %s", resp.status_code, url)
            return None
    except Exception as e:
        logger.debug("fetch_html failed for %s: %s", url, e)
        return None


def _match_path(
    url: str,
    include_paths: list[str] | None,
    exclude_paths: list[str] | None,
    regex_on_full_url: bool = False,
) -> bool:
    """Check whether a URL passes include/exclude path filters.

    Args:
        url: The full URL to check.
        include_paths: If set, URL must match at least one pattern.
        exclude_paths: If set, URL must not match any pattern
            (takes precedence).
        regex_on_full_url: If True, patterns are regex; else glob.

    Returns:
        True if the URL should be crawled (passes all filters).
    """
    target = url if regex_on_full_url else urlparse(url).path

    if regex_on_full_url:
        # Regex mode
        if exclude_paths:
            for pattern in exclude_paths:
                try:
                    if re.search(pattern, target):
                        return False
                except re.error:
                    continue
        if include_paths:
            for pattern in include_paths:
                try:
                    if re.search(pattern, target):
                        return True
                except re.error:
                    continue
            return False  # include_paths set but none matched
    else:
        # Glob mode — convert glob patterns to regex
        if exclude_paths:
            for pattern in exclude_paths:
                regex = _glob_to_regex(pattern)
                try:
                    if re.search(regex, target):
                        return False
                except re.error:
                    continue
        if include_paths:
            for pattern in include_paths:
                regex = _glob_to_regex(pattern)
                try:
                    if re.search(regex, target):
                        return True
                except re.error:
                    continue
            return False  # include_paths set but none matched

    return True  # No include_paths constraint, or passed all


def _matches_pattern(
    target: str, pattern: str, regex_on_full_url: bool = False
) -> bool:
    """Check whether ``target`` matches a single path pattern.

    Args:
        target: The string (path or full URL) to check.
        pattern: The glob or regex pattern to match against.
        regex_on_full_url: If True, ``pattern`` is a regex; else glob.

    Returns:
        True if the target matches the pattern.
    """
    if regex_on_full_url:
        try:
            return bool(re.search(pattern, target))
        except re.error:
            return False
    else:
        regex = _glob_to_regex(pattern)
        try:
            return bool(re.search(regex, target))
        except re.error:
            return False


def _glob_to_regex(pattern: str) -> str:
    """Convert a simple glob pattern to an anchored regex pattern.

    Supports ``*`` (match any chars except ``/``), ``**`` (match any
    chars including ``/``), and ``?`` (match single char).

    The returned pattern is anchored with ``^`` and ``$`` so that
    ``re.search`` matches the full target string, not a substring.
    """
    i, n = 0, len(pattern)
    inner: list[str] = []
    while i < n:
        c = pattern[i]
        if c == "*" and i + 1 < n and pattern[i + 1] == "*":
            inner.append(".*")
            i += 2
        elif c == "*":
            inner.append("[^/]*")
            i += 1
        elif c == "?":
            inner.append(".")
            i += 1
        elif c in ".^$+{}[]\\|()":
            inner.append("\\" + c)
            i += 1
        else:
            inner.append(c)
            i += 1
    return "^" + "".join(inner) + "$"


# ── CrawlEngine ──────────────────────────────────────────────────


class CrawlEngine:
    """BFS crawl orchestrator with configurable concurrency.

    Usage::

        engine = CrawlEngine(scraper_client, options=CrawlOptions(max_pages=5))
        result = await engine.run("https://example.com", job_id="...")

    Concurrency is managed with an ``asyncio.Semaphore``. When ``delay``
    is set, ``max_concurrency`` is forced to 1 and an
    ``asyncio.sleep(delay)`` is inserted between scrapes.
    """

    # Maximum value to cap max_concurrency at (prevents resource exhaustion).
    MAX_CONCURRENCY_CAP: int = 50

    def __init__(
        self,
        scraper_client: ScraperClient,
        store: JobStore | None = None,
        options: CrawlOptions | None = None,
        crawl_cache: CrawlCache | None = None,
    ):
        self.scraper = scraper_client
        self.store = store
        self.options = options or CrawlOptions()
        self.crawl_cache = crawl_cache

        # Resolve effective concurrency: delay forces sequential
        self._effective_concurrency = self._resolve_concurrency()

        self._semaphore = asyncio.Semaphore(self._effective_concurrency)
        self._seen: set[str] = set()
        self._queue: deque[tuple[str, int, bool]] = deque()
        self._pages: list[dict] = []
        self._errors: list[dict] = []
        self._robots_blocked: list[dict] = []
        self._filtered_out: list[dict] = []
        self._cancel_flag: bool = False
        self._update_interval: float = 1.0
        self._html_client: httpx.AsyncClient | None = None
        self._last_scrape_start: float | None = None
        self._scraped_count: int = 0
        # Track in-flight tasks for cancellation
        self._pending_tasks: set[asyncio.Task] = set()
        # Content-level dedup (canonical + content hash)
        self.dedup_manager = DedupManager()
        self._dedup_skipped: int = 0  # Pages skipped by content-level dedup
        self._retry_count: dict[str, int] = {}  # Track retry attempts per URL

    def _resolve_concurrency(self) -> int:
        """Determine the effective concurrency value.

        When ``delay`` is set (non-zero), concurrency is forced to 1
        so that pages are scraped sequentially with pacing.
        Otherwise, ``max_concurrency`` is used, capped at
        ``MAX_CONCURRENCY_CAP``.
        """
        delay = self.options.delay
        if delay is not None and delay > 0:
            return 1
        return min(self.options.max_concurrency, self.MAX_CONCURRENCY_CAP)

    async def _get_html(self, url: str) -> str | None:
        """Get HTML for a URL, using an internal persistent client."""
        if self._html_client is None:
            self._html_client = httpx.AsyncClient(follow_redirects=True, timeout=15.0)
        try:
            resp = await self._html_client.get(url)
            if resp.status_code == 200:
                return resp.text
            return None
        except Exception:
            return None

    async def close(self) -> None:
        """Close the internal HTTP client and cancel pending tasks."""
        self._cancel_all_tasks()
        if self._html_client is not None:
            await self._html_client.aclose()
            self._html_client = None

    async def run(
        self,
        start_url: str,
        job_id: str | None = None,
        page_callback: collections.abc.Callable[
            [str, dict], collections.abc.Awaitable[None]
        ]
        | None = None,
        error_callback: collections.abc.Callable[
            [str, dict], collections.abc.Awaitable[None]
        ]
        | None = None,
    ) -> CrawlResult:
        """Execute a BFS crawl starting from ``start_url``.

        When ``max_concurrency > 1``, multiple pages are scraped
        concurrently using an ``asyncio.Semaphore``. When ``delay`` is
        set, concurrency is forced to 1 and ``asyncio.sleep(delay)`` is
        inserted between scrapes.

        Args:
            start_url: The URL to begin crawling from.
            job_id: Optional job ID for periodic store updates and
                cancellation checking.
            page_callback: Optional async callback invoked after each
                successful page scrape, receiving (job_id, page_dict).

        Returns:
            A ``CrawlResult`` with scraped pages, totals, and errors.
        """
        parsed_start = urlparse(start_url)
        base_domain = parsed_start.hostname.lower() if parsed_start.hostname else ""

        # Seed the BFS queue with start URL (DO NOT add to _seen — the task
        # adds to _seen when it acquires the URL. Adding here would cause
        # the task's dedup check to skip the start URL.)
        self._queue.append((start_url, 0, False))
        start_normalized = self.normalize_url(start_url)

        # Seed with sitemap URLs if sitemap_mode is not "skip"
        sitemap_seeded_count = 0
        if self.options.sitemap_mode != "skip":
            try:
                sitemap_urls = await self._fetch_sitemap_urls(base_domain)
            except Exception as exc:
                logger.warning(
                    "Sitemap fetch failed for %s: %s — falling back to HTML-only discovery",
                    base_domain,
                    exc,
                )
                sitemap_urls = []
            if sitemap_urls:
                # Truncate sitemap URLs to respect max_pages (reserve 1 for start URL)
                remaining = self.options.max_pages - 1
                if remaining > 0:
                    sitemap_urls = sitemap_urls[:remaining]
                sitemap_dedup: set[str] = set()
                sitemap_skipped = 0
                for sm_url in sitemap_urls:
                    if _is_low_value_sitemap_url(sm_url):
                        sitemap_skipped += 1
                        continue
                    sm_normalized = self.normalize_url(sm_url)
                    if (
                        sm_normalized in sitemap_dedup
                        or sm_normalized == start_normalized
                    ):
                        continue
                    sitemap_dedup.add(sm_normalized)
                    self._queue.appendleft((sm_url, 0, True))
                    sitemap_seeded_count += 1
                logger.info(
                    "Seeded %d sitemap URLs into crawl queue for %s (%d skipped as low-value)",
                    sitemap_seeded_count,
                    base_domain,
                    sitemap_skipped,
                )

        last_store_update = time.monotonic()
        crawl_start_time = time.monotonic()

        logger.info(
            "Starting crawl of %s (max_pages=%d, max_depth=%d, "
            "concurrency=%d, delay=%s)",
            start_url,
            self.options.max_pages,
            self.options.max_depth,
            self._effective_concurrency,
            self.options.delay,
        )

        while self._queue or self._pending_tasks:
            if (
                self.options.max_pages > 0
                and len(self._pages) >= self.options.max_pages
            ):
                logger.info(
                    "Reached max_pages=%d — stopping crawl",
                    self.options.max_pages,
                )
                break
            if self._cancel_flag:
                logger.info("Crawl cancelled via cancel flag")
                break

            # Cooperative cancellation: check Redis for cancelled status
            if self.store is not None and job_id is not None:
                job_meta = self.store.get_job(job_id)
                if job_meta and job_meta.get("status") == "cancelled":
                    self._cancel_flag = True
                    logger.info("Crawl %s cancelled via Redis status check", job_id)
                    break

            # Maximum duration timeout check
            now = time.monotonic()
            elapsed = now - crawl_start_time
            if elapsed > self.options.max_duration_seconds:
                logger.warning(
                    "Crawl %s exceeded max duration of %ds (elapsed=%.1fs)",
                    job_id,
                    self.options.max_duration_seconds,
                    elapsed,
                )
                self._cancel_all_tasks()
                await self.close()
                raise TimeoutError(
                    f"Crawl exceeded max duration of {self.options.max_duration_seconds}s"
                )

            # Dispatch tasks from the queue.
            # We limit by both concurrency AND remaining capacity so that
            # in-flight + completed pages don't exceed max_pages.
            while (
                self._queue
                and len(self._pending_tasks) < self._effective_concurrency
                and (
                    self.options.max_pages <= 0
                    or len(self._pages) + len(self._pending_tasks)
                    < self.options.max_pages
                )
            ):
                task = self._create_scrape_task(
                    page_callback, error_callback, job_id, base_domain
                )
                if task is None:
                    # Queue exhausted or all items filtered
                    break

            # Wait for at least one task to complete (or queue to be non-empty)
            if self._pending_tasks:
                _completed, _remaining = await asyncio.wait(
                    self._pending_tasks,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                self._pending_tasks = _remaining
                # Process completed tasks (any exceptions were handled within)
                for task in _completed:
                    try:
                        task.result()
                    except asyncio.CancelledError:
                        logger.debug("Scrape task was cancelled")
                    except StartUrlScrapeError:
                        logger.error("Start URL scrape failed — aborting crawl")
                        self._cancel_all_tasks()
                        result_obj = CrawlResult(
                            pages=self._pages,
                            total=0,
                            completed=len(self._pages),
                            errors=self._errors,
                            robots_blocked=self._robots_blocked,
                        )
                        await self.close()
                        return result_obj
                    except Exception as exc:
                        logger.warning(
                            "Scrape task raised unexpected exception: %s", exc
                        )
            elif not self._queue:
                # Nothing pending and nothing left in queue — crawl is done
                break

            # Periodic job store update
            if self.store is not None and job_id is not None:
                now = time.monotonic()
                if now - last_store_update >= self._update_interval:
                    self._update_store(job_id)
                    last_store_update = now

        # Wait for any remaining pending tasks to finish
        if self._pending_tasks:
            _done, _still_pending = await asyncio.wait(
                self._pending_tasks, return_when=asyncio.ALL_COMPLETED
            )
            self._pending_tasks = _still_pending
            for task in _done:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    task.result()

        # Final store update
        if self.store is not None and job_id is not None:
            self._update_store(job_id)

        await self.close()

        return CrawlResult(
            pages=self._pages,
            total=len(self._pages) + len(self._queue) + self._dedup_skipped,
            completed=len(self._pages),
            errors=self._errors,
            robots_blocked=self._robots_blocked,
            filtered_out=self._filtered_out,
        )

    def _create_scrape_task(
        self,
        page_callback: collections.abc.Callable[
            [str, dict], collections.abc.Awaitable[None]
        ]
        | None,
        error_callback: collections.abc.Callable[
            [str, dict], collections.abc.Awaitable[None]
        ]
        | None,
        job_id: str | None,
        base_domain: str,
    ) -> asyncio.Task | None:
        """Pop one URL from the queue and create a scrape task for it.

        Performs dedup and path filtering checks. Returns ``None`` if the
        queue is empty or the URL is skipped.
        """
        if not self._queue:
            return None

        url, depth, from_sitemap = self._queue.popleft()
        normalized = self.normalize_url(url)

        # Dedup check
        if normalized in self._seen:
            logger.debug("Skipping already-seen URL: %s", url)
            return None

        # Max depth check: pages at max_depth are scraped but not
        # followed; pages beyond max_depth are skipped entirely.
        if depth > self.options.max_depth:
            logger.debug("Skipping URL beyond max_depth: %s (depth=%d)", url, depth)
            return None

        # Mark as seen immediately to prevent re-enqueue
        self._seen.add(normalized)

        # Path filtering
        filter_url = (
            urlparse(url)._replace(query="").geturl()
            if self.options.ignore_query_parameters
            else url
        )
        if not _match_path(
            filter_url,
            self.options.include_paths,
            self.options.exclude_paths,
            self.options.regex_on_full_url,
        ):
            if self.options.verbose:
                filter_reason = self._get_filter_reason(filter_url)
                if filter_reason:
                    self._filtered_out.append(filter_reason)
            logger.debug("Skipping URL excluded by path filter: %s", url)
            return None

        # Create the async task for this URL
        task = asyncio.create_task(
            self._scrape_url(
                url=url,
                depth=depth,
                from_sitemap=from_sitemap,
                base_domain=base_domain,
                page_callback=page_callback,
                error_callback=error_callback,
                job_id=job_id,
            )
        )
        self._pending_tasks.add(task)
        return task

    async def _scrape_url(
        self,
        url: str,
        depth: int,
        from_sitemap: bool,
        base_domain: str,
        page_callback: collections.abc.Callable[
            [str, dict], collections.abc.Awaitable[None]
        ]
        | None,
        error_callback: collections.abc.Callable[
            [str, dict], collections.abc.Awaitable[None]
        ]
        | None,
        job_id: str | None,
    ) -> None:
        """Scrape a single URL with semaphore protection and delay pacing.

        This is the core unit of concurrent work. The semaphore ensures
        that at most ``self._effective_concurrency`` scrapes run
        simultaneously.

        A per-scrape timeout (VAL-CONC-043) prevents a hanging scrape
        from permanently occupying a semaphore slot. On timeout, the
        URL is recorded in ``errors`` and the slot is released.
        """
        async with self._semaphore:
            if self._cancel_flag:
                return

            # Apply delay between scrapes (only when delay > 0)
            delay = self.options.delay
            if delay is not None and delay > 0 and self._last_scrape_start is not None:
                elapsed_since_last = time.monotonic() - self._last_scrape_start
                wait = max(0.0, delay - elapsed_since_last)
                if wait > 0:
                    try:
                        await asyncio.sleep(wait)
                    except asyncio.CancelledError:
                        logger.debug(
                            "Delay sleep cancelled for %s (job %s)", url, job_id
                        )
                        return

            self._last_scrape_start = time.monotonic()

            # ── Cache check ──────────────────────────────────────
            cache_max_age_ms: int | None = None
            cache_min_age_ms: int | None = None
            if self.options.scrape_options:
                cache_max_age_ms = self.options.scrape_options.get("max_age")
                cache_min_age_ms = self.options.scrape_options.get("min_age")

            _result_from_cache: dict | None = None
            if self.crawl_cache is not None and (
                cache_max_age_ms is not None or cache_min_age_ms is not None
            ):
                use_cached, cached_data, cache_err = self.crawl_cache.check_cache(
                    url,
                    max_age_ms=cache_max_age_ms,
                    min_age_ms=cache_min_age_ms,
                )
                if cache_err:
                    # minAge cache miss → record error, skip scrape
                    self._errors.append(
                        {
                            "url": url,
                            "error": cache_err,
                            "error_type": "cache_miss",
                            "error_code": "CACHE_MISS",
                            "timestamp": _now_timestamp(),
                        }
                    )
                    logger.info(
                        "Cache miss (minAge) for %s (job %s): %s",
                        url,
                        job_id,
                        cache_err,
                    )
                    if error_callback is not None:
                        await error_callback(
                            job_id or "",
                            {
                                "url": url,
                                "error": cache_err,
                                "error_type": "cache_miss",
                            },
                        )
                    return
                if use_cached and cached_data is not None:
                    _result_from_cache = cached_data

            if _result_from_cache is not None:
                # Use cached result — skip the scraper call entirely.
                result = _result_from_cache
                scrape_duration_ms = 0  # Cache hit — no network latency
                logger.debug(
                    "Cache HIT for %s (job %s) — skipping scraper call",
                    url,
                    job_id,
                )
            else:
                # ── Fresh scrape — full scraper call ─────────────
                scrape_start = time.monotonic()
                per_scrape_timeout = 60.0
                # Derive user-configured timeout from scrape_options (in ms → s)
                user_timeout_ms: int | None = None
                if self.options.scrape_options:
                    user_timeout_ms = self.options.scrape_options.get("timeout")
                try:
                    result = await asyncio.wait_for(
                        self.scraper.scrape(
                            url,
                            ignore_robots_txt=self.options.ignore_robots_txt,
                            robots_user_agent=self.options.robots_user_agent,
                            scrape_options=self.options.scrape_options,
                        ),
                        timeout=per_scrape_timeout,
                    )
                except TimeoutError:
                    elapsed = time.monotonic() - scrape_start
                    timeout_entry: dict = {
                        "url": url,
                        "error": f"Scrape timed out after {elapsed:.1f}s",
                        "error_type": "timeout",
                        "error_code": "TIMEOUT",
                        "timestamp": _now_timestamp(),
                        "timeout_ms": user_timeout_ms or int(per_scrape_timeout * 1000),
                        "elapsed_ms": int(elapsed * 1000),
                    }
                    self._errors.append(timeout_entry)
                    logger.warning(
                        "Scrape timed out for %s after %.1fs (job %s)",
                        url,
                        elapsed,
                        job_id,
                    )
                    if error_callback is not None:
                        await error_callback(
                            job_id or "",
                            {
                                "url": url,
                                "error": f"Scrape timed out after {elapsed:.1f}s",
                            },
                        )
                    return
                except asyncio.CancelledError:
                    logger.debug("Scrape cancelled for %s (job %s)", url, job_id)
                    return
                except Exception as exc:
                    error_entry = {
                        "url": url,
                        "error": str(exc),
                        "error_type": "scrape_error",
                        "error_code": "SCRAPE_ERROR",
                        "timestamp": _now_timestamp(),
                    }
                    self._errors.append(error_entry)
                    logger.warning("Scrape exception for %s: %s", url, exc)
                    if depth == 0:
                        raise  # let the caller handle start URL failure
                    if error_callback is not None:
                        await error_callback(
                            job_id or "",
                            {"url": url, "error": str(exc)},
                        )
                    return

                scrape_duration_ms = int((time.monotonic() - scrape_start) * 1000)

                # Cache the fresh scrape result if maxAge > 0
                if (
                    self.crawl_cache is not None
                    and cache_max_age_ms is not None
                    and cache_max_age_ms > 0
                    and result.get("success")
                ):
                    self.crawl_cache.set(url, result, ttl_ms=cache_max_age_ms)

            if not result.get("success"):
                error_msg = result.get("error", "Unknown scrape error")
                error_code = result.get("error_code", "")

                # Detect politeness-blocked results (VAL-CONC-025)
                if (
                    error_code == "ROBOTS_BLOCKED"
                    or "Blocked by politeness" in error_msg
                    or "ROBOTS_BLOCKED" in error_msg
                ):
                    now_ts = _now_timestamp()
                    robots_entry = {
                        "url": url,
                        "error": error_msg,
                        "error_type": "robots_blocked",
                        "error_code": "ROBOTS_BLOCKED",
                        "timestamp": now_ts,
                    }
                    self._robots_blocked.append(robots_entry)
                    self._errors.append(robots_entry)
                    logger.info(
                        "Politeness blocked %s (job %s): %s", url, job_id, error_msg
                    )
                    if error_callback is not None:
                        await error_callback(
                            job_id or "",
                            {
                                "url": url,
                                "error": error_msg,
                                "error_type": "robots_blocked",
                            },
                        )
                    # Blocked start URL is not a fatal error — it's expected
                    return
                else:
                    # Classify error type based on error message content
                    cls_error_type = _classify_scrape_error(error_msg, error_code)
                    scrape_error_entry: dict = {
                        "url": url,
                        "error": error_msg,
                        "error_type": cls_error_type,
                        "error_code": error_code or "SCRAPE_ERROR",
                        "timestamp": _now_timestamp(),
                    }
                    # Include http_status for HTTP errors if available
                    http_status = result.get("http_status")
                    if http_status is not None:
                        scrape_error_entry["http_status"] = http_status
                    # Include error_detail from scraper-svc if available
                    error_detail = result.get("data", {}).get("error_detail")
                    if error_detail:
                        scrape_error_entry["error_detail"] = error_detail
                    self._errors.append(scrape_error_entry)
                    logger.warning("Scrape failed for %s: %s", url, error_msg)

                if error_callback is not None:
                    await error_callback(
                        job_id or "",
                        {"url": url, "error": error_msg, "error_type": cls_error_type},
                    )

                # Retry with backoff for non-critical failures.
                # Depth > 0: always retry (child pages).
                # Depth == 0 and from_sitemap: retry (sitemap-discovered page).
                # Depth == 0 and not from_sitemap: abort (user-provided start URL).
                if depth > 0 or from_sitemap:
                    normalized = self.normalize_url(url)
                    retries = self._retry_count.get(normalized, 0)
                    if retries < 2:
                        self._retry_count[normalized] = retries + 1
                        backoff_delay = 2.0 if retries == 0 else 5.0
                        logger.info(
                            "Retrying %s (attempt %d/3, %.0fs backoff) — %s",
                            url,
                            retries + 1,
                            backoff_delay,
                            error_msg,
                        )
                        # Remove the error entry we just added — retry will handle it
                        if self._errors and self._errors[-1].get("url") == url:
                            self._errors.pop()
                        # Remove from _seen so the retry can pass dedup
                        self._seen.discard(normalized)
                        await asyncio.sleep(backoff_delay)
                        self._queue.appendleft((url, depth, from_sitemap))
                    else:
                        logger.info("Failed after 2 retries — skipping %s", url)
                    return

                # Critical failure: user-provided start URL cannot be scraped
                raise StartUrlScrapeError(url, error_msg)

            # Record successful page with enriched metadata
            data = result.get("data", {})
            metadata = data.get("metadata") or {}
            og = metadata.get("og") or {}
            meta = metadata.get("meta") or {}
            title = (
                og.get("title")
                or meta.get("title")
                or data.get("title")
                or metadata.get("title")
                or ""
            )
            description = (
                og.get("description")
                or meta.get("description")
                or metadata.get("description")
                or ""
            )
            language = (
                metadata.get("language")
                or metadata.get("lang")
                or meta.get("language")
                or ""
            )
            source_url = url  # The source URL is the page URL itself
            status_code = metadata.get("statusCode") or data.get("status_code") or 200
            content_type = (
                metadata.get("content-type")
                or metadata.get("contentType")
                or "text/html"
            )
            scraped_at = datetime.now(UTC).isoformat()

            page = {
                "url": url,
                "markdown": data.get("markdown", ""),
                "metadata": {
                    "title": title,
                    "description": description,
                    "language": language,
                    "sourceURL": source_url,
                    "statusCode": status_code,
                },
                "title": title,
                "status_code": status_code,
                "content_type": content_type,
                "scraped_at": scraped_at,
                "duration_ms": scrape_duration_ms,
            }

            # ── Content-level dedup checks ──────────────────────────
            # Layer 2: Canonical tag check (runs first).
            # Layer 3: Content hash check (runs second, only if canonical
            #          check did not skip the page).
            markdown = data.get("markdown", "")
            canonical_dup_url: str | None = None
            content_is_dup: bool = False

            # Fetch HTML for canonical checking (also reused for link
            # extraction below if needed).
            html = await self._get_html(url)

            if html:
                canonical_dup_url = self.dedup_manager.check_canonical(html, url)

            if canonical_dup_url:
                # Canonical duplicate — skip this page (VAL-SCRAPE-021,
                # VAL-SCRAPE-026).
                error_entry = {
                    "url": url,
                    "error": f"Duplicate canonical URL: {canonical_dup_url}",
                    "error_type": "duplicate_canonical",
                    "error_code": "DUPLICATE_CANONICAL",
                    "timestamp": _now_timestamp(),
                    "canonical_url": canonical_dup_url,
                }
                self._errors.append(error_entry)
                self._dedup_skipped += 1
                logger.info(
                    "Canonical dedup skipped %s → canonical %s already scraped (job %s)",
                    url,
                    canonical_dup_url,
                    job_id,
                )
                if error_callback is not None:
                    await error_callback(
                        job_id or "",
                        {
                            "url": url,
                            "error": f"Duplicate canonical URL: {canonical_dup_url}",
                            "error_type": "duplicate_canonical",
                        },
                    )
                # Skip link extraction and page storage for dedup'd pages
                return
            else:
                # Layer 3: Content hash dedup (VAL-SCRAPE-024, VAL-SCRAPE-026).
                content_hash = self.dedup_manager.compute_content_hash(markdown)
                if content_hash is not None and self.dedup_manager.is_duplicate_content(
                    content_hash
                ):
                    content_is_dup = True

                if content_is_dup:
                    error_entry = {
                        "url": url,
                        "error": "Duplicate content (identical markdown hash)",
                        "error_type": "duplicate_content",
                        "error_code": "DUPLICATE_CONTENT",
                        "timestamp": _now_timestamp(),
                    }
                    self._errors.append(error_entry)
                    self._dedup_skipped += 1
                    logger.info(
                        "Content dedup skipped %s — identical content already scraped (job %s)",
                        url,
                        job_id,
                    )
                    if error_callback is not None:
                        await error_callback(
                            job_id or "",
                            {
                                "url": url,
                                "error": "Duplicate content (identical markdown hash)",
                                "error_type": "duplicate_content",
                            },
                        )
                    return

                # Page passed all dedup checks — register it.
                self.dedup_manager.mark_scraped(
                    url, canonical_url=canonical_dup_url, content_hash=content_hash
                )

            self._pages.append(page)
            self._scraped_count += 1

            # Atomically increment the completed count (VAL-CONC-042)
            if self.store is not None and job_id is not None:
                self.store.increment_completed(job_id)

            # Fire per-page webhook callback
            if page_callback is not None and job_id is not None:
                try:
                    await page_callback(job_id, page)
                except Exception:
                    logger.warning(
                        "Page callback failed for %s (job %s)",
                        url,
                        job_id,
                        exc_info=True,
                    )

            # Discover child links if within max_depth
            # In "only" mode, sitemap URLs are the exclusive source
            if depth < self.options.max_depth and self.options.sitemap_mode != "only":
                # Reuse the HTML fetched for canonical check if available;
                # otherwise fetch it now for link extraction only.
                if html is None and depth < self.options.max_depth:
                    html = await self._get_html(url)
                if html:
                    child_links = extract_links(html, url)
                    child_links = filter_links(
                        child_links,
                        base_domain=base_domain,
                        allow_subdomains=self.options.allow_subdomains,
                        allow_external_links=self.options.allow_external_links,
                    )
                    # SSRF guard: always block private/internal hosts on
                    # EXTERNAL URLs regardless of allow_external_links setting.
                    classified = classify_links(child_links, url)
                    if classified.get("external"):
                        classified["external"] = self._filter_ssrf_blocked(
                            classified["external"]
                        )
                    child_links = (
                        classified.get("internal", [])
                        + classified.get("subdomain", [])
                        + classified.get("external", [])
                    )
                    if not self.options.crawl_entire_domain:
                        child_links = self._filter_child_paths(child_links, url)
                    for child_url in child_links:
                        child_normalized = self.normalize_url(child_url)
                        if child_normalized not in self._seen:
                            self._queue.append((child_url, depth + 1, False))

    def _cancel_all_tasks(self) -> None:
        """Cancel all in-flight scrape tasks and clear tracking.

        This ensures semaphore slots are released and pending tasks
        don't hold references after cancellation.
        """
        for task in self._pending_tasks:
            task.cancel()
        self._pending_tasks.clear()

    def normalize_url(self, url: str) -> str:
        """Normalize a URL using this engine's options."""
        return normalize_url(
            url, ignore_query_parameters=self.options.ignore_query_parameters
        )

    async def _fetch_sitemap_urls(self, domain: str) -> list[str]:
        """Fetch sitemap URLs for the given domain.

        Uses ``SitemapParser`` to discover and parse sitemaps from
        robots.txt and common locations.

        Args:
            domain: The domain (hostname) to fetch sitemaps for.

        Returns:
            A list of unique, absolute URLs discovered from sitemaps.
        """
        try:
            parser = SitemapParser(
                client=self._html_client,
                max_recursion_depth=3,
            )
            # Use the parser's own client management — it creates its
            # own client if we don't pass one (but we already have one).
            parser._client = self._html_client
            urls = await parser.get_urls(
                domain,
                limit=self.options.max_pages * 2,  # generous outer limit
            )
            return urls
        except Exception as exc:
            logger.warning(
                "Failed to fetch sitemaps for %s: %s — falling back to HTML-only discovery",
                domain,
                exc,
            )
            return []

    def cancel(self) -> None:
        """Signal the crawl to stop and cancel all pending tasks.

        This sets the cancel flag and cancels any in-flight scrape
        tasks. Pending tasks that are blocked on the semaphore will
        also be woken up and exit cleanly.
        """
        self._cancel_flag = True
        self._cancel_all_tasks()

    @staticmethod
    def _filter_child_paths(links: list[str], current_url: str) -> list[str]:
        """Filter links to only include child paths of the current URL.

        When ``crawl_entire_domain`` is False, only follow links whose path
        is a child (deeper) of the current page's path. Links on different
        domains (subdomains or external) are not affected by this constraint.

        Args:
            links: List of absolute URL strings to filter.
            current_url: The URL of the page the links were extracted from.

        Returns:
            Filtered list of URLs that are child paths (or different-domain).
        """
        current_path = urlparse(current_url).path.rstrip("/") + "/"
        current_domain = (urlparse(current_url).hostname or "").lower()

        result: list[str] = []
        for link in links:
            parsed = urlparse(link)
            link_path = parsed.path or "/"
            link_domain = (parsed.hostname or "").lower()

            # If the link is on a different domain (including subdomain when
            # already allowed by filter_links), skip the child-path constraint.
            if link_domain != current_domain:
                result.append(link)
                continue

            # On same domain: only allow child paths (deeper in hierarchy)
            if link_path.startswith(current_path) and link_path != current_path.rstrip(
                "/"
            ):
                result.append(link)
                continue

            # / page (not a child path of current)
            logger.debug(
                "Filtered out non-child path: %s (current=%s, crawl_entire_domain=False)",
                link,
                current_url,
            )

        return result

    @staticmethod
    def _filter_ssrf_blocked(links: list[str]) -> list[str]:
        """Filter out links to private/internal hosts (SSRF guard).

        Always active regardless of ``allow_external_links`` setting.
        Uses ``common.url.is_private_host()`` which checks RFC 1918
        private ranges, loopback, link-local, metadata IPs, and
        internal hostname suffixes.

        Args:
            links: List of absolute URL strings to filter.

        Returns:
            List of URLs that are NOT private/internal hosts.
        """
        result: list[str] = []
        for link in links:
            if is_private_host(link):
                logger.debug("SSRF guard blocked private host URL: %s", link)
                continue
            result.append(link)
        return result

    def _get_filter_reason(self, url: str) -> dict | None:
        """Determine which path filter excluded a URL and why.

        Only called when ``verbose`` is True and the URL has already
        been determined to NOT pass ``_match_path()``.

        Returns:
            A dict with ``url``, ``reason``, and ``pattern`` keys, or
            ``None`` if no filter reason could be determined.
        """
        target = url if self.options.regex_on_full_url else urlparse(url).path

        # Check exclude_paths first (they bypass include_paths)
        if self.options.exclude_paths:
            for pattern in self.options.exclude_paths:
                if _matches_pattern(target, pattern, self.options.regex_on_full_url):
                    return {
                        "url": url,
                        "reason": "exclude_paths",
                        "pattern": pattern,
                    }

        # Check include_paths
        if self.options.include_paths:
            for pattern in self.options.include_paths:
                if _matches_pattern(target, pattern, self.options.regex_on_full_url):
                    # Would match include, so not excluded by include
                    return {
                        "url": url,
                        "reason": "include_paths",
                        "pattern": None,
                    }
            # No include_paths matched
            return {
                "url": url,
                "reason": "include_paths",
                "pattern": None,
            }

        return None

    def _update_store(self, job_id: str) -> None:
        """Write current progress to the job data key using atomic updates.

        Uses ``JobStore.update_job_progress()`` which sources the
        ``completed`` count from a Valkey INCR counter (immune to
        read-modify-write races, per VAL-CONC-042). The job ``meta``
        status remains ``processing`` during the crawl.
        The caller's final ``store.complete_job()`` call sets both the
        final data and the ``completed`` status.
        """
        if self.store is None:
            return
        try:
            total = len(self._pages) + len(self._queue) + self._dedup_skipped
            self.store.update_job_progress(
                job_id=job_id,
                pages=self._pages,
                total=total,
                errors=self._errors,
                robots_blocked=self._robots_blocked,
                filtered_out=self._filtered_out if self.options.verbose else None,
            )
        except Exception:
            logger.warning("Failed to update job store", exc_info=True)
