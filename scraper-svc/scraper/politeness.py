"""Politeness protocol — optional per-domain rate limiting with robots.txt respect.

Gated behind SCRAPER_POLITENESS_ENABLED=true in the Docker .env file.
Off by default. Designed as a stateless policy layer that layers on top of
the existing fetch pipeline without breaking changes.

Architecture:
    PolitenessManager — singleton that holds per-domain state (robots.txt
    cache + last-request timestamps). On each scrape, the caller invokes
    check() which returns an action: PROCEED, DELAY (with seconds to wait),
    or BLOCKED (with reason). The caller is responsible for actually sleeping
    before proceeding.
"""

import hashlib
import logging
import re
import time
from dataclasses import dataclass, field

from common.url import extract_domain

from .settings import load_settings

logger = logging.getLogger(__name__)

# ── Env toggle ──────────────────────────────────────────────────
_pol_settings = load_settings()
POLITENESS_ENABLED = _pol_settings.politeness_enabled

# ── Defaults (overridable via env) ──────────────────────────────
DEFAULT_CRAWL_DELAY = _pol_settings.politeness_crawl_delay
ROBOTS_TTL = _pol_settings.politeness_robots_ttl
ROBOTS_TIMEOUT = _pol_settings.politeness_robots_timeout

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


@dataclass
class PolitenessResult:
    """Result of a politeness check for a given URL."""

    action: str  # "proceed", "delay", "blocked"
    delay_seconds: float = 0.0
    reason: str = ""
    robots_allowed: bool = True
    domain: str = ""


@dataclass
class _DomainState:
    """Per-domain state tracked by the politeness manager."""

    last_request: float = 0.0
    crawl_delay: float = DEFAULT_CRAWL_DELAY
    robots_cached_at: float = 0.0
    robots_disallowed_paths: list[re.Pattern] = field(default_factory=list)
    robots_sitemaps: list[str] = field(default_factory=list)


class PolitenessManager:
    """Per-domain politeness enforcement.

    Tracks robots.txt state and request timing per domain. Operates
    only when SCRAPER_POLITENESS_ENABLED=true. Designed as a singleton
    — instantiate via get_manager().
    """

    def __init__(self) -> None:
        self._domains: dict[str, _DomainState] = {}
        self._enabled = POLITENESS_ENABLED

    @property
    def enabled(self) -> bool:
        return self._enabled

    # ── Domain parsing ─────────────────────────────────────────

    @staticmethod
    def _domain_from_url(url: str) -> str:
        """Extract the hostname from a URL for rate-limiting key."""
        return extract_domain(url)

    # ── Robots.txt fetch + parse ────────────────────────────────

    def _robots_cache_key(self, domain: str) -> str:
        """Valkey key for caching a domain's robots.txt."""
        digest = hashlib.sha256(domain.encode("utf-8")).hexdigest()
        return f"politeness:robots:{digest}"

    async def _fetch_and_parse_robots(self, domain: str) -> None:
        """Fetch robots.txt for a domain and parse disallowed paths.

        Stores parsed rules in the in-memory domain state. Results are
        also cached in Valkey (when available) for persistence across
        container restarts.
        """
        state = self._domains.setdefault(domain, _DomainState())
        robots_url = f"https://{domain}/robots.txt"

        try:
            import httpx

            async with httpx.AsyncClient(
                timeout=ROBOTS_TIMEOUT, follow_redirects=True
            ) as client:
                resp = await client.get(robots_url, headers={"User-Agent": USER_AGENT})
                if resp.status_code == 200 and resp.text.strip():
                    self._parse_robots_txt(resp.text, state)
                    state.robots_cached_at = time.time()

                    # Cache in Valkey for cross-instance persistence
                    await self._robots_cache_store(domain, resp.text)
                    logger.info(
                        "Robots.txt fetched for %s: %d disallowed paths, %d sitemaps",
                        domain,
                        len(state.robots_disallowed_paths),
                        len(state.robots_sitemaps),
                    )
                    return
        except Exception as e:
            logger.debug("Robots.txt fetch failed for %s: %s", domain, e)

        # Fallback: try Valkey cache
        cached = await self._robots_cache_load(domain)
        if cached:
            self._parse_robots_txt(cached, state)
            state.robots_cached_at = time.time()
            logger.info("Robots.txt loaded from cache for %s", domain)
            return

        # No robots.txt available — assume all paths allowed
        state.robots_cached_at = time.time()
        logger.info("No robots.txt for %s — assuming all paths allowed", domain)

    def _parse_robots_txt(self, text: str, state: _DomainState) -> None:
        """Parse robots.txt content into disallowed path patterns.

        Handles User-agent directives with wildcards (*, $).
        Respects Crawl-delay and Sitemap directives.
        """
        disallowed: list[str] = []
        crawl_delay: float | None = None
        sitemaps: list[str] = []
        applicable = False

        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            # User-agent directive
            if line.lower().startswith("user-agent:"):
                ua = line.split(":", 1)[1].strip().lower()
                # We match wildcard (*) or any user-agent that contains our name
                applicable = ua == "*" or ua == "groktocrawl" or ua == "bot"

            if not applicable:
                continue

            # Disallow
            if line.lower().startswith("disallow:"):
                path = line.split(":", 1)[1].strip()
                if path:
                    disallowed.append(path)

            # Crawl-delay
            if line.lower().startswith("crawl-delay:"):
                try:
                    crawl_delay = float(line.split(":", 1)[1].strip())
                except ValueError:
                    pass

            # Sitemap
            if line.lower().startswith("sitemap:"):
                sitemaps.append(line.split(":", 1)[1].strip())

        # Convert disallowed paths to compiled regex patterns
        # Wildcard * → .*, $ → end-of-string
        patterns = []
        for path in disallowed:
            # Convert robots.txt pattern to regex
            regex = re.escape(path)
            regex = regex.replace(r"\*", ".*")  # wildcard → regex
            if path.endswith("$"):
                regex = regex[:-2] + "$"  # trailing $
            patterns.append(re.compile(f"^{regex}"))

        state.robots_disallowed_paths = patterns
        if crawl_delay is not None:
            state.crawl_delay = crawl_delay
        if sitemaps:
            state.robots_sitemaps = sitemaps

    # ── Valkey cache helpers ────────────────────────────────────

    async def _robots_cache_store(self, domain: str, text: str) -> None:
        """Store robots.txt content in Valkey cache."""
        try:
            from .fetch import _get_cache_client

            client = await _get_cache_client()
            if client:
                key = self._robots_cache_key(domain)
                await client.setex(key, ROBOTS_TTL, text)
        except Exception:
            pass

    async def _robots_cache_load(self, domain: str) -> str | None:
        """Load robots.txt content from Valkey cache."""
        try:
            from .fetch import _get_cache_client

            client = await _get_cache_client()
            if client:
                key = self._robots_cache_key(domain)
                return await client.get(key)
        except Exception:
            pass
        return None

    # ── Main check interface ────────────────────────────────────

    async def check(self, url: str) -> PolitenessResult:
        """Check whether a URL can be scraped under the current politeness policy.

        Returns a PolitenessResult indicating proceed, delay (with seconds),
        or blocked (with reason).
        """
        if not self._enabled:
            return PolitenessResult(action="proceed", reason="politeness disabled")

        domain = self._domain_from_url(url)
        if not domain:
            return PolitenessResult(action="proceed", reason="no domain in URL")

        state = self._domains.setdefault(domain, _DomainState())

        # ── Ensure robots.txt is loaded ────────────────────────
        if state.robots_cached_at == 0.0:
            await self._fetch_and_parse_robots(domain)

        # ── Check robots.txt ──────────────────────────────────
        from urllib.parse import urlparse

        parsed = urlparse(url)
        path = parsed.path or "/"
        for pattern in state.robots_disallowed_paths:
            if pattern.search(path):
                logger.info(
                    "Blocked by robots.txt: %s (disallowed path pattern: %s)",
                    url,
                    pattern.pattern,
                )
                return PolitenessResult(
                    action="blocked",
                    reason=f"Disallowed by robots.txt: {pattern.pattern}",
                    robots_allowed=False,
                    domain=domain,
                )

        # ── Rate limiting ──────────────────────────────────────
        elapsed = time.time() - state.last_request
        if elapsed < state.crawl_delay and state.last_request > 0:
            wait = state.crawl_delay - elapsed
            logger.debug(
                "Rate limiting %s: %.2fs elapsed, need %.2fs, waiting %.2fs",
                domain,
                elapsed,
                state.crawl_delay,
                wait,
            )
            return PolitenessResult(
                action="delay",
                delay_seconds=wait,
                reason=f"Rate limit: {wait:.1f}s remaining on {domain} (delay={state.crawl_delay:.1f}s)",
                domain=domain,
            )

        return PolitenessResult(
            action="proceed",
            reason="",
            robots_allowed=True,
            domain=domain,
        )

    def record_request(self, url: str) -> None:
        """Record that a request was made to a URL.

        Called after a successful (or attempted) fetch so the rate
        limiter can track timing. Only meaningful when politeness is
        enabled.
        """
        if not self._enabled:
            return
        domain = self._domain_from_url(url)
        if domain:
            state = self._domains.setdefault(domain, _DomainState())
            state.last_request = time.time()

    def get_politeness_metadata(self, url: str) -> dict:
        """Return politeness metadata for a scrape response.

        Includes domain, configured delay, and whether robots.txt
        was loaded. Only meaningful when politeness is enabled.
        """
        if not self._enabled:
            return {"enabled": False}

        domain = self._domain_from_url(url)
        state = self._domains.get(domain)
        if not state:
            return {"enabled": True, "domain": domain}

        return {
            "enabled": True,
            "domain": domain,
            "crawl_delay_seconds": state.crawl_delay,
            "robots_loaded": state.robots_cached_at > 0,
            "disallowed_paths": len(state.robots_disallowed_paths),
        }


# ── Module-level singleton ──────────────────────────────────────
_manager: PolitenessManager | None = None


def get_manager() -> PolitenessManager:
    """Get the module-level PolitenessManager singleton."""
    global _manager
    if _manager is None:
        _manager = PolitenessManager()
    return _manager
