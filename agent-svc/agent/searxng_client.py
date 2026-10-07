"""SearXNG JSON API client."""

import logging
import math
import os
import time
from dataclasses import dataclass

import httpx

from common.stage_metrics import inc_counter, observe_elapsed

logger = logging.getLogger(__name__)

_SEARCH_QUERY_SECONDS = "groktocrawl_search_query_seconds"
_SEARCH_QUERY_SECONDS_HELP = "Search query latency by engine"
_SEARCH_QUERIES_TOTAL = "groktocrawl_search_queries_total"
_SEARCH_QUERIES_TOTAL_HELP = "Total search queries by engine and outcome"


@dataclass
class SearchHealth:
    """Health information about a SearXNG search request.

    Attributes:
        engines_total: Number of engines queried by SearXNG.
        engines_responding: Number of engines that returned at least one result.
        empty_result: True when engines responded but no results were returned
            (distinct from an infrastructure failure).
        degraded: True when fewer than half of queried engines responded.
        detail: Human-readable summary of engine status.
    """

    engines_total: int = 0
    engines_responding: int = 0
    empty_result: bool = False
    degraded: bool = False
    detail: str = ""
    result_coverage: dict | None = None


# ── Firecrawl v2 → SearXNG category translation ────────────────
# Maps Firecrawl v2 search dimensions (sources, categories) to
# SearXNG-native category names. Unknown values pass through for
# forward compatibility. See ADR-0013 and issue #85.

_SOURCES_MAP = {
    "news": "news",
    "images": "images",
    "web": "general",
    "video": "videos",
    "social": "general",
}

_CATEGORIES_MAP = {
    "research": "science",
    "github": "it",
    "pdf": "general",
    "news": "news",
    "science": "science",
    "it": "it",
    "general": "general",
}


def _parse_retry_after(value: str | None) -> float | None:
    """Parse a ``Retry-After`` header as seconds, or ``None`` when absent/invalid.

    Only numeric seconds are accepted (HTTP-date values are treated as
    absent so the caller falls back to its bounded policy).
    """
    if not value:
        return None
    try:
        seconds = float(value.strip())
    except (TypeError, ValueError):
        return None
    if not math.isfinite(seconds) or seconds < 0:
        return None
    return seconds


class SearXNGClient:
    """Client for the SearXNG search engine JSON API."""

    def __init__(self, base_url: str = "http://searxng:8080", max_searches: int = 5):
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            timeout=15,
            headers={
                "User-Agent": "GroktoCrawl/0.1",
                "Accept": "text/html,application/json",
                "X-Forwarded-For": "127.0.0.1",
            },
        )
        self._search_count = 0
        self._max_searches = max_searches

    @staticmethod
    def _translate(
        sources: list[str] | None,
        categories: list[str] | None,
    ) -> list[str]:
        """Translate Firecrawl v2 sources/categories to SearXNG category names.

        Merges both dimensions into a single SearXNG categories list.
        Unknown values pass through for forward compatibility.
        Returns ``[\"general\"]`` if no mapping produces a category.
        """
        result: list[str] = []
        if sources:
            for s in sources:
                mapped = _SOURCES_MAP.get(s, s)
                if mapped and mapped not in result:
                    result.append(mapped)
        if categories:
            for c in categories:
                mapped = _CATEGORIES_MAP.get(c, c)
                if mapped and mapped not in result:
                    result.append(mapped)
        return result or ["general"]

    @staticmethod
    def _parse_engine_health(data: dict, results: list[dict]) -> SearchHealth:
        """Parse SearXNG engine status from the API response.

        Inspects the ``engines`` key in the SearXNG JSON response to
        determine how many engines were queried and how many returned
        results, building a ``SearchHealth`` summary.
        """
        engines = data.get("engines", [])
        engines_total = len(engines)
        engines_responding = sum(1 for e in engines if e.get("results", 0) > 0)

        empty_result = bool(
            engines_responding > 0 and not any(r.get("url") for r in results)
        )
        degraded = bool(engines_total > 0 and engines_responding < engines_total / 2)

        # Build a human-readable detail string
        if engines_total == 0:
            detail = "No engine status available from SearXNG"
        elif degraded:
            detail = (
                f"Degraded: {engines_responding}/{engines_total} engines "
                f"returned results"
            )
        elif empty_result:
            detail = f"All {engines_total} engines responded but returned no results"
        else:
            detail = (
                f"Healthy: {engines_responding}/{engines_total} engines "
                f"returned results"
            )

        return SearchHealth(
            engines_total=engines_total,
            engines_responding=engines_responding,
            empty_result=empty_result,
            degraded=degraded,
            detail=detail,
        )

    async def search(
        self,
        query: str,
        limit: int = 10,
        categories: list[str] | None = None,
        sources: list[str] | None = None,
        *,
        raise_on_rate_limit: bool = False,
        scenario: str | None = None,
        page: int = 1,
        offset: int = 0,
    ) -> tuple[list[dict], SearchHealth]:
        """Search the web and return structured results with health info.

        Uses SearXNG's JSON API. When categories is None, defaults to "general".
        ``sources`` and ``categories`` are merged via ``_translate()`` before
        being passed to SearXNG.

        Enforces a per-request search budget. Raises ``RateLimitedError``
        when the budget is exhausted.

        ``raise_on_rate_limit`` opts into the retryable downstream
        classification (ADR-0053): when the upstream answers HTTP 429, the
        caller receives ``RetryableRateLimitError`` so sync routes render a
        retryable 429 and the worker schedules a bounded job retry. Call
        sites that degrade gracefully (session steps, ``/v2/search``,
        monitor, find-similar) leave it ``False`` and keep the legacy
        behavior: HTTP 429 returns an empty result set with a health
        detail, never a hard failure.

        Returns a tuple of (results, health) where:
        - results: list of dicts with keys: url, title, description, engine.
        - health: SearchHealth dataclass with engine status information.
        """
        from .exceptions import RateLimitedError

        started = time.monotonic()
        if (
            type(limit) is not int
            or not 1 <= limit <= 1000
            or type(page) is not int
            or not 1 <= page <= 1000
            or type(offset) is not int
            or not 0 <= offset <= 100000
        ):
            raise ValueError(
                "search limit/page/offset outside supported resource bounds"
            )
        outcome = "success"

        try:
            # ── Per-request search budget check ────────────────────
            if self._search_count >= self._max_searches:
                outcome = "rate_limited"
                raise RateLimitedError(
                    detail=(
                        f"Search budget exceeded: {self._search_count}/{self._max_searches}"
                    ),
                    details={
                        "budget_used": self._search_count,
                        "budget_max": self._max_searches,
                    },
                )
            self._search_count += 1

            effective_categories = self._translate(sources, categories)
            params = {
                "q": query,
                "format": "json",
                "language": "en",
                "pageno": page,
            }
            params["categories"] = ",".join(effective_categories)
            run_id = os.getenv("TWIN_RUN_ID")
            if run_id:
                params["run_id"] = run_id
            if scenario is not None:
                params["scenario"] = scenario

            resp = await self._client.get(
                f"{self.base_url}/search",
                params=params,  # type: ignore[arg-type]
            )
            if resp.status_code == 429:
                # Downstream capacity condition (ADR-0053): opt-in call
                # sites raise a retryable error so sync routes render a
                # retryable 429 and the worker schedules a bounded retry.
                # Degrading call sites keep the legacy empty-result
                # behavior — a session search step or /v2/search must not
                # hard-fail because the upstream search capacity is
                # temporarily exhausted.
                if raise_on_rate_limit:
                    from .exceptions import RetryableRateLimitError

                    outcome = "rate_limited"
                    retry_after = _parse_retry_after(resp.headers.get("Retry-After"))
                    logger.warning(
                        "SearXNG rate limited (429) — retry_after=%s",
                        retry_after if retry_after is not None else "unknown",
                    )
                    raise RetryableRateLimitError(
                        detail=(
                            "Downstream search capacity is temporarily exhausted "
                            "(SearXNG returned HTTP 429)"
                        ),
                        retry_after_seconds=retry_after,
                    )
                outcome = "degraded"
                logger.warning(
                    "SearXNG rate limited (429) — returning empty results "
                    "(caller did not opt into retryable classification)"
                )
                return [], SearchHealth(
                    detail="SearXNG returned HTTP 429 (rate limited)"
                )
            if resp.status_code != 200:
                outcome = "error"
                logger.warning(
                    "SearXNG returned %d: %s", resp.status_code, resp.text[:200]
                )
                return [], SearchHealth(
                    detail=f"SearXNG returned HTTP {resp.status_code}"
                )

            data = resp.json()
            results = []
            for item in data.get("results", []):
                results.append(
                    {
                        "url": item.get("url", ""),
                        "title": item.get("title", ""),
                        "description": item.get("content", ""),
                        "engine": item.get("engine", ""),
                    }
                )

            captured_count = len(results)
            results = results[offset : offset + limit]

            # ── Parse engine health ────────────────────────────────────
            health = self._parse_engine_health(data, results)
            health.result_coverage = {
                "scope": "upstream_page",
                "page": page,
                "offset": offset,
                "captured_results": captured_count,
                "returned_results": len(results),
                "omitted_results": max(0, captured_count - len(results)),
                "has_more_in_page": offset + len(results) < captured_count,
                "upstream_has_more": None,
                "complete": False,
                "pagination_enforcement": data.get("meta", {})
                .get("enforcement", {})
                .get("pagination"),
            }

            return results, health

        except RateLimitedError:
            raise
        except httpx.TimeoutException:
            outcome = "timeout"
            logger.warning("SearXNG search timed out")
            return [], SearchHealth(detail="SearXNG request timed out")
        except Exception as e:
            outcome = "error"
            logger.error("SearXNG search failed: %s", type(e).__name__)
            return [], SearchHealth(detail="SearXNG search failed")
        finally:
            observe_elapsed(
                _SEARCH_QUERY_SECONDS,
                _SEARCH_QUERY_SECONDS_HELP,
                {"engine": "searxng"},
                started,
            )
            inc_counter(
                _SEARCH_QUERIES_TOTAL,
                _SEARCH_QUERIES_TOTAL_HELP,
                {"engine": "searxng", "outcome": outcome},
            )

    async def close(self) -> None:
        await self._client.aclose()
