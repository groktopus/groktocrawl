"""HTTP client to the scraper service."""

import contextlib
import logging
import time

import httpx

from .admission import AdmissionRejectedError, get_admission
from .cancel import JobCancelledError, raise_if_cancelled
from .metrics import METRICS

logger = logging.getLogger(__name__)


class ScraperClient:
    """Client for the scraper-svc HTTP API.

    Connection pool limits are configured to support high concurrency
    (up to ``max_connections=100``, per VAL-CONC-048). This prevents
    ``PoolTimeout`` or "connection pool exhausted" errors when many
    concurrent scrape tasks are in flight.
    """

    def __init__(
        self,
        base_url: str = "http://scraper-svc:8001",
        admission=None,
    ):
        self.base_url = base_url.rstrip("/")
        self._admission = admission if admission is not None else get_admission()
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
        lightweight_only: bool = False,
        contents: dict | None = None,
    ) -> dict:
        """Scrape a URL via the scraper service.

        Returns dict with keys: success, data (with markdown, source), error.
        Records scrape latency metrics by source tier.

        When ``force_browser`` is True, the scraper-svc skips lightweight
        tiers and goes straight to Playwright render (Tier 3).

        When ``lightweight_only`` is True, the scraper-svc runs only the
        lightweight tiers and short-circuits before the browser tier.

        When ``ignore_robots_txt`` is True, the scraper-svc bypasses
        robots.txt enforcement but still applies per-domain rate limiting.

        When ``robots_user_agent`` is set, it is used as the User-Agent for
        robots.txt evaluation instead of the default bot UA.

        When ``scrape_options`` is set, it is forwarded as-is to the
        scraper-svc ``/scrape`` endpoint so that per-page extraction
        options (formats, content filtering, viewport, etc.) are applied.
        """
        raise_if_cancelled()
        resource_class = "browser" if force_browser else "lightweight_fetch"
        weight = self._admission.weight_for(resource_class)
        try:
            await self._admission.acquire(resource_class, weight=weight)
        except AdmissionRejectedError as exc:
            return {
                "success": False,
                "error": str(exc),
                "error_code": "CAPACITY_EXCEEDED",
            }

        start = time.monotonic()
        try:
            body: dict = {"url": url}
            if force_browser:
                body["force_browser"] = True
            if lightweight_only:
                body["lightweight_only"] = True
            if ignore_robots_txt:
                body["ignore_robots_txt"] = True
            if robots_user_agent is not None:
                body["robots_user_agent"] = robots_user_agent
            if scrape_options:
                body["scrape_options"] = scrape_options
            if contents:
                body["contents"] = contents
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
        except JobCancelledError:
            raise
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
        finally:
            self._admission.release(resource_class, weight=weight)

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

        # Cancel remaining speculative tasks and await them so no pending
        # task is destroyed or races a later scrape lifecycle.
        for t in pending:
            t.cancel()
        if pending:
            await _asyncio.gather(*pending, return_exceptions=True)

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

        The generic stage runs with ``lightweight_only=True`` so it can never
        silently enter the browser tier; a timed-out generic task is cancelled
        and awaited before the forced-browser retry starts.

        A generic result that carries a ``warning`` key is degraded content
        (below the scraper's quality threshold, e.g. JS-only shells, cookie
        walls, or nav-only pages) and is treated as insufficient so the
        forced-browser retry still runs. The browser stage applies the same
        guard (#586): a warned browser result (e.g. a challenge interstitial)
        is never returned as a successful scrape.

        Barrier-flagged results are refused at both stages (#586): a payload
        with a ``warning`` key OR ``data.quality.checks.block_detected`` in
        {"warn", "fail"} never passes as success — if both stages yield only
        flagged content, an explicit failure dict is returned instead of the
        flagged payload (barrier text must not reach LLM-feeding consumers).

        Returns the first successful result dict (with ``success``, ``data`` keys)
        or a failure dict.
        """
        import asyncio as _asyncio

        from .barrier_guard import is_barrier_flagged

        last_failure: dict | None = None

        # ── Try generic (lightweight fast path) ────────────────
        generic_task = _asyncio.create_task(
            self.scrape(
                url,
                force_browser=False,
                lightweight_only=True,
                scrape_options=scrape_options,
            )
        )
        try:
            result = await _asyncio.wait_for(generic_task, timeout=generic_timeout)
            data = result.get("data") or {}
            if (
                result.get("success")
                and not is_barrier_flagged(result)
                and (data.get("markdown", "").strip() or data.get("download"))
            ):
                return result
            # A clean (unwarned) CAPTCHA_UNRESOLVED payload is a typed refusal
            # — pass it through so callers see the structured error rather than
            # a generic failure. A warned payload must NOT surface as success:
            # the ``not warning`` guard keeps it in the failure path (#586).
            if result.get("error_code") == "CAPTCHA_UNRESOLVED" and not result.get(
                "warning"
            ):
                return result
            last_failure = result
        except TimeoutError:
            logger.info("Generic scrape timed out for %s, trying browser fallback", url)
            # Ensure the timed-out generic request is fully cancelled and
            # awaited before a second (forced-browser) request starts.
            generic_task.cancel()
            await _asyncio.gather(generic_task, return_exceptions=True)
        except Exception as e:
            logger.warning(
                "Generic scrape failed for %s: %s, trying browser fallback", url, e
            )

        METRICS.counter(
            "scrape_retries_total",
            "Total explicit scrape retries by stage transition",
            ["stage"],
        ).inc({"stage": "generic_to_browser"})

        # ── Try browser (slow path, longer timeout) ────────────
        try:
            result = await _asyncio.wait_for(
                self.scrape(
                    url,
                    force_browser=True,
                    lightweight_only=False,
                    scrape_options=scrape_options,
                ),
                timeout=browser_timeout,
            )
            data = result.get("data") or {}
            # The same ``not warning`` guard the generic stage applies (#586):
            # a browser-rendered challenge interstitial must not be surfaced
            # as a successful result. The block-fail check catches payloads
            # flagged via quality only (no warning key).
            if (
                result.get("success")
                and not is_barrier_flagged(result)
                and (data.get("markdown", "").strip() or data.get("download"))
            ):
                return result
            if result.get("error_code") == "CAPTCHA_UNRESOLVED" and not result.get(
                "warning"
            ):
                return result
            last_failure = result
        except TimeoutError:
            logger.warning("Browser fallback also timed out for %s", url)
        except Exception as e:
            logger.warning("Browser fallback failed for %s: %s", url, e)

        if last_failure is not None and is_barrier_flagged(last_failure):
            # Both stages yielded only barrier-flagged content (#586): refuse
            # with an explicit failure rather than returning the challenge
            # payload as a (flagged) success.
            logger.info(
                "All scrape methods returned barrier-flagged content for %s "
                "(warning=%r) — refusing",
                url,
                last_failure.get("warning"),
            )
            return {
                "success": False,
                "error": (
                    f"Barrier/challenge content detected for {url} "
                    f"(warning={last_failure.get('warning')!r})"
                ),
                "error_code": "BARRIER_DETECTED",
            }

        return last_failure or {
            "success": False,
            "error": f"All scrape methods failed for {url}",
        }
