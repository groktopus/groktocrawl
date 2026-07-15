"""HTTP client to the scraper service."""

import contextlib
import logging
import time

import httpx

from .metrics import METRICS

logger = logging.getLogger(__name__)


class ScraperClient:
    """Client for the scraper-svc HTTP API.

    Connection pool limits are configured to support high concurrency
    (up to ``max_connections=100``, per VAL-CONC-048). This prevents
    ``PoolTimeout`` or "connection pool exhausted" errors when many
    concurrent scrape tasks are in flight.
    """

    def __init__(self, base_url: str = "http://scraper-svc:8001"):
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            timeout=60,
            limits=httpx.Limits(
                max_connections=100,
                max_keepalive_connections=50,
                keepalive_expiry=30.0,
            ),
        )

    async def scrape(
        self,
        url: str,
        force_browser: bool = False,
        ignore_robots_txt: bool = False,
        robots_user_agent: str | None = None,
        scrape_options: dict | None = None,
    ) -> dict:
        """Scrape a URL via the scraper service.

        Returns dict with keys: success, data (with markdown, source), error.
        Records scrape latency metrics by source tier.

        When ``force_browser`` is True, the scraper-svc skips lightweight
        tiers and goes straight to Playwright render (Tier 3).

        When ``ignore_robots_txt`` is True, the scraper-svc bypasses
        robots.txt enforcement but still applies per-domain rate limiting.

        When ``robots_user_agent`` is set, it is used as the User-Agent for
        robots.txt evaluation instead of the default bot UA.

        When ``scrape_options`` is set, it is forwarded as-is to the
        scraper-svc ``/scrape`` endpoint so that per-page extraction
        options (formats, content filtering, viewport, etc.) are applied.
        """
        start = time.monotonic()
        try:
            body: dict = {"url": url}
            if force_browser:
                body["force_browser"] = True
            if ignore_robots_txt:
                body["ignore_robots_txt"] = True
            if robots_user_agent is not None:
                body["robots_user_agent"] = robots_user_agent
            if scrape_options:
                body["scrape_options"] = scrape_options
            resp = await self._client.post(
                f"{self.base_url}/scrape",
                json=body,
            )
            result = resp.json()
            elapsed = time.monotonic() - start
            source = (result.get("data") or {}).get("source", "unknown")
            METRICS.histogram(
                "scrape_duration_seconds",
                "Scrape latency by source tier",
                ["tier"],
            ).observe({"tier": source}, elapsed)
            METRICS.counter("scrapes_total", "Total scrapes by tier", ["tier"]).inc(
                {"tier": source}
            )
            return result  # type: ignore[no-any-return]
        except httpx.TimeoutException:
            elapsed = time.monotonic() - start
            logger.warning("Scraper timed out for %s", url)
            METRICS.histogram(
                "scrape_duration_seconds", "Scrape latency by source tier", ["tier"]
            ).observe({"tier": "timeout"}, elapsed)
            return {"success": False, "error": f"Scraper timed out for {url}"}
        except Exception as e:
            elapsed = time.monotonic() - start
            logger.error("Scraper client error for %s: %s", url, e)
            METRICS.histogram(
                "scrape_duration_seconds", "Scrape latency by source tier", ["tier"]
            ).observe({"tier": "error"}, elapsed)
            return {"success": False, "error": str(e)}

    async def close(self) -> None:
        await self._client.aclose()

    async def scrape_urls_batch(
        self,
        urls: list[str],
        max_concurrent: int = 5,
        url_timeout: float = 20.0,
        min_sources: int = 10,
    ) -> list[dict]:
        """Scrape multiple URLs concurrently with bounded concurrency.

        Returns list of result dicts (with success, data keys) for completed scrapes.
        Stops early when ``min_sources`` successful results are collected.
        Records metrics for concurrent scrape operations.
        """
        import asyncio as _asyncio

        semaphore = _asyncio.Semaphore(max_concurrent)
        documents: list[dict] = []
        completed_urls: set[str] = set()

        async def _scrape_one(url: str) -> dict | None:
            async with semaphore:
                try:
                    result = await _asyncio.wait_for(
                        self.scrape(url), timeout=url_timeout
                    )
                    if result.get("success") and result.get("data", {}).get("markdown"):
                        return result
                    return None
                except TimeoutError:
                    logger.warning("Timeout scraping %s after %ss", url, url_timeout)
                    return None
                except Exception as e:
                    logger.warning("Error scraping %s: %s", url, e)
                    return None

        async def _collect_one(url: str) -> None:
            if url in completed_urls:
                return
            result = await _scrape_one(url)
            if result:
                documents.append(result)
                completed_urls.add(url)

        # Launch tasks, collect results, stop early at min_sources
        tasks = [_asyncio.create_task(_collect_one(u)) for u in urls]
        pending = set(tasks)
        while pending and len(documents) < min_sources:
            done, pending = await _asyncio.wait(
                pending, return_when=_asyncio.FIRST_COMPLETED
            )
            for t in done:
                with contextlib.suppress(Exception):
                    t.result()

        # Cancel remaining tasks
        for t in pending:
            t.cancel()

        logger.info(
            "Batch scraped %d/%d URLs (min_sources=%d)",
            len(documents),
            len(urls),
            min_sources,
        )
        return documents

    async def scrape_with_fallback(
        self,
        url: str,
        generic_timeout: float = 20.0,
        browser_timeout: float = 45.0,
        scrape_options: dict | None = None,
    ) -> dict:
        """Try generic scrape first, fall back to browser-tier on failure/empty.

        Returns the first successful result dict (with ``success``, ``data`` keys)
        or a failure dict.
        """
        import asyncio as _asyncio

        last_failure: dict | None = None

        # ── Try generic (fast path) ───────────────────────────
        try:
            result = await _asyncio.wait_for(
                self.scrape(url, force_browser=False, scrape_options=scrape_options),
                timeout=generic_timeout,
            )
            data = result.get("data") or {}
            if result.get("success") and (
                data.get("markdown", "").strip() or data.get("download")
            ):
                return result
            if result.get("error_code") == "CAPTCHA_UNRESOLVED":
                return result
            last_failure = result
        except TimeoutError:
            logger.info("Generic scrape timed out for %s, trying browser fallback", url)
        except Exception as e:
            logger.warning(
                "Generic scrape failed for %s: %s, trying browser fallback", url, e
            )

        # ── Try browser (slow path, longer timeout) ────────────
        try:
            result = await _asyncio.wait_for(
                self.scrape(url, force_browser=True, scrape_options=scrape_options),
                timeout=browser_timeout,
            )
            data = result.get("data") or {}
            if result.get("success") and (
                data.get("markdown", "").strip() or data.get("download")
            ):
                return result
            if result.get("error_code") == "CAPTCHA_UNRESOLVED":
                return result
            last_failure = result
        except TimeoutError:
            logger.warning("Browser fallback also timed out for %s", url)
        except Exception as e:
            logger.warning("Browser fallback failed for %s: %s", url, e)

        return last_failure or {
            "success": False,
            "error": f"All scrape methods failed for {url}",
        }
