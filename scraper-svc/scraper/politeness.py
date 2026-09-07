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

Thread-safety:
    Per-domain asyncio Lock ensures that concurrent tasks targeting the
    same domain are serialized during the check() call. Only one robots.txt
    fetch occurs per domain (VAL-CONC-037). The check → record cycle is
    atomic so two tasks cannot both see a stale last_request timestamp
    (VAL-CONC-031).

Distributed coordination:
    When Valkey is available, last-request timestamps are shared across
    instances via Valkey. This enables multi-instance crawl deployments
    to respect per-domain rate limits collectively (VAL-CONC-019).
"""

import asyncio
import contextlib
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

# Valkey key prefix for distributed rate limiting
_RATE_KEY_PREFIX = "politeness:rate:"

# Use Valkey's clock and one atomic reservation across all scraper replicas.
# A cancelled reservation is deliberately not reclaimed: later callers may
# already own subsequent slots. TTL bounds this conservative unused delay.
_RESERVE_SLOT = """
local clock = redis.call('TIME')
local now = clock[1] * 1000 + math.floor(clock[2] / 1000)
local delay = tonumber(ARGV[1])
local ceiling = tonumber(ARGV[2])
local next_slot = tonumber(redis.call('GET', KEYS[1]) or '0')
local slot = math.max(now, next_slot)
local wait = slot - now
if wait > ceiling then return -1 end
redis.call('PSETEX', KEYS[1], math.max(1000, wait + delay + 1000), slot + delay)
return wait
"""


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
    # Transient future-slot reservation used only to stagger concurrent
    # same-domain tasks. Kept separate from ``last_request`` (an actual
    # request timestamp) so an aborted delayed request never leaves a stale
    # future timestamp behind. In-memory only; not persisted to Valkey.
    reserved_slot: float = 0.0
    crawl_delay: float = DEFAULT_CRAWL_DELAY
    robots_cached_at: float = 0.0
    robots_disallowed_paths: list[re.Pattern] = field(default_factory=list)
    robots_sitemaps: list[str] = field(default_factory=list)


class PolitenessManager:
    """Per-domain politeness enforcement.

    Tracks robots.txt state and request timing per domain. Operates
    only when SCRAPER_POLITENESS_ENABLED=true. Designed as a singleton
    — instantiate via get_manager().

    Concurrency: Per-domain asyncio Locks ensure atomic check → record
    cycles and exactly-once robots.txt fetching per domain.
    """

    def __init__(self) -> None:
        self._domains: dict[str, _DomainState] = {}
        self._domain_locks: dict[str, asyncio.Lock] = {}
        self._enabled = POLITENESS_ENABLED
        self._distributed = _pol_settings.distributed_politeness
        self._rate_ttl: int = 60  # TTL for rate-limit keys in Valkey

    @property
    def enabled(self) -> bool:
        return self._enabled

    # ── Domain parsing ─────────────────────────────────────────

    @staticmethod
    def _domain_from_url(url: str) -> str:
        """Extract the hostname from a URL for rate-limiting key."""
        return extract_domain(url)

    # ── Per-domain lock for race safety ─────────────────────────

    def _get_domain_lock(self, domain: str) -> asyncio.Lock:
        """Get or create a per-domain asyncio Lock.

        Ensures atomic check → record cycles and exactly-once
        robots.txt fetching per domain (VAL-CONC-037).
        """
        if domain not in self._domain_locks:
            self._domain_locks[domain] = asyncio.Lock()
        return self._domain_locks[domain]

    # ── Robots.txt fetch + parse ────────────────────────────────

    def _robots_cache_key(self, domain: str) -> str:
        """Valkey key for caching a domain's robots.txt."""
        digest = hashlib.sha256(domain.encode("utf-8")).hexdigest()
        return f"politeness:robots:{digest}"

    @staticmethod
    def _rate_key(domain: str) -> str:
        """Valkey key for distributed rate-limit state."""
        digest = hashlib.sha256(domain.encode("utf-8")).hexdigest()
        return f"{_RATE_KEY_PREFIX}{digest}"

    async def _fetch_and_parse_robots(
        self, domain: str, user_agent: str | None = None
    ) -> None:
        """Fetch robots.txt for a domain and parse disallowed paths.

        Uses a per-domain asyncio Lock to ensure only one concurrent
        fetch per domain (VAL-CONC-037). Also checks Valkey cache
        before fetching.

        Args:
            domain: The domain to fetch robots.txt for.
            user_agent: Custom User-Agent string for the robots.txt
                request. If None, uses the default USER_AGENT.
        """
        state = self._domains.setdefault(domain, _DomainState())

        # Check Valkey cache first
        cached = await self._robots_cache_load(domain)
        if cached:
            self._parse_robots_txt(cached, state)
            state.robots_cached_at = time.time()
            logger.info("Robots.txt loaded from cache for %s", domain)
            return

        ua = user_agent or USER_AGENT
        robots_url = f"https://{domain}/robots.txt"

        try:
            import httpx

            async with httpx.AsyncClient(
                timeout=ROBOTS_TIMEOUT, follow_redirects=True
            ) as client:
                resp = await client.get(robots_url, headers={"User-Agent": ua})
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

        # No robots.txt available — assume all paths allowed
        state.robots_cached_at = time.time()
        logger.info(
            "No robots.txt for %s — assuming all paths allowed (status!=200 or error)",
            domain,
        )

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
                with contextlib.suppress(ValueError):
                    crawl_delay = float(line.split(":", 1)[1].strip())

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
            from .cache import _get_cache_client

            client = await _get_cache_client()
            if client:
                key = self._robots_cache_key(domain)
                await client.setex(key, ROBOTS_TTL, text)
        except Exception:
            pass

    async def _robots_cache_load(self, domain: str) -> str | None:
        """Load robots.txt content from Valkey cache."""
        try:
            from .cache import _get_cache_client

            client = await _get_cache_client()
            if client:
                key = self._robots_cache_key(domain)
                return await client.get(key)
        except Exception:
            pass
        return None

    # ── Valkey distributed rate limit helpers ───────────────────

    async def _get_valkey_last_request(self, domain: str) -> float:
        """Get the last-request timestamp from Valkey for a domain.

        Returns 0.0 if no Valkey entry exists or Valkey is unavailable.
        """
        try:
            from .cache import _get_cache_client

            client = await _get_cache_client()
            if client:
                key = self._rate_key(domain)
                val = await client.get(key)
                if val is not None:
                    return float(val)
        except Exception:
            pass
        return 0.0

    async def _set_valkey_last_request(self, domain: str, timestamp: float) -> None:
        """Set the last-request timestamp in Valkey for a domain.

        Fail-open: if Valkey is unreachable, the rate limit falls back
        to in-memory state only (VAL-CONC-020).
        """
        try:
            from .cache import _get_cache_client

            client = await _get_cache_client()
            if client:
                key = self._rate_key(domain)
                await client.setex(key, self._rate_ttl, str(timestamp))
        except Exception:
            pass

    # ── Main check interface ────────────────────────────────────

    async def _reserve_distributed(self, domain: str, delay: float) -> PolitenessResult:
        """Reserve a shared slot, failing closed when coordination is unavailable."""
        from .cache import _get_cache_client

        try:
            client = await _get_cache_client()
            if client is None:
                raise RuntimeError("Valkey is unavailable")
            # A distinct key cannot be overwritten by legacy last-request
            # recording. Keep excessive same-origin queues bounded to 30s.
            wait_ms = await asyncio.wait_for(
                client.eval(
                    _RESERVE_SLOT,
                    1,
                    self._rate_key(domain) + ":slots",
                    max(0, int(delay * 1000)),
                    30000,
                ),
                timeout=2,
            )
            if wait_ms < 0:
                reason = "Distributed origin queue exceeds 30 seconds"
            else:
                return PolitenessResult(
                    action="delay" if wait_ms else "proceed",
                    delay_seconds=wait_ms / 1000,
                    domain=domain,
                )
        except Exception:
            logger.warning("Distributed politeness coordination unavailable")
            reason = "Distributed politeness coordination unavailable"
        return PolitenessResult(action="blocked", reason=reason, domain=domain)

    async def check(
        self,
        url: str,
        ignore_robots_txt: bool = False,
        robots_user_agent: str | None = None,
        rate_limit: bool = True,
    ) -> PolitenessResult:
        """Check whether a URL can be scraped under the current politeness policy.

        Acquires a per-domain asyncio Lock to provide atomicity for the
        check → record cycle under concurrency (VAL-CONC-031, VAL-CONC-037).
        Also coordinates with Valkey for distributed rate limiting across
        instances (VAL-CONC-019).

        When ``ignore_robots_txt`` is True, robots.txt disallow directives
        are skipped, but per-domain rate limiting (crawl-delay) still
        applies. This supports crawl-level ``ignoreRobotsTxt: true`` while
        still respecting the domain's Crawl-delay.

        Args:
            url: The URL to check.
            ignore_robots_txt: If True, skip robots.txt enforcement.
            robots_user_agent: Custom User-Agent string for robots.txt
                evaluation. If None, uses the default USER_AGENT.
            rate_limit: If False, only robots.txt enforcement runs and the
                rate-limiting state is left untouched. Per-tier checks pass
                ``rate_limit=False`` so a single scrape does not reserve a
                new future slot (and sleep) before every tier.

        Returns:
            PolitenessResult with action "proceed", "delay", or "blocked".
        """
        if not self._enabled:
            return PolitenessResult(action="proceed", reason="politeness disabled")

        domain = self._domain_from_url(url)
        if not domain:
            return PolitenessResult(action="proceed", reason="no domain in URL")

        # Acquire per-domain lock for atomic check → record
        lock = self._get_domain_lock(domain)
        async with lock:
            state = self._domains.setdefault(domain, _DomainState())

            # ── Ensure robots.txt is loaded ────────────────────────
            if state.robots_cached_at == 0.0:
                await self._fetch_and_parse_robots(domain, user_agent=robots_user_agent)

            # ── Check robots.txt (skip if ignore_robots_txt) ──────
            if not ignore_robots_txt:
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

            # ── Rate limiting (Valkey-coordinated) ─────────────────
            # Read-only checks (per-tier) only enforce robots.txt above and
            # must not advance the rate-limit state, otherwise a single scrape
            # would reserve a fresh future slot (and sleep) before every tier.
            if not rate_limit:
                return PolitenessResult(
                    action="proceed",
                    reason="",
                    robots_allowed=True,
                    domain=domain,
                )

            if self._distributed:
                return await self._reserve_distributed(domain, state.crawl_delay)

            # Check both in-memory and Valkey last_request, use the most recent
            in_memory_last = state.last_request
            valkey_last = await self._get_valkey_last_request(domain)
            effective_last = max(in_memory_last, valkey_last)

            now = time.time()
            elapsed = now - effective_last

            if elapsed < state.crawl_delay and effective_last > 0:
                # Atomically reserve the next-available slot under the
                # per-domain lock so N concurrent same-domain tasks stagger
                # their wake-ups instead of waking as a burst. The reservation
                # is kept in-memory only (``reserved_slot``) — never written
                # into ``last_request`` or Valkey — so an aborted delayed
                # request does not leave a stale future timestamp behind.
                base = max(effective_last, state.reserved_slot)
                reserved = base + state.crawl_delay
                wait = max(0.0, reserved - now)
                state.reserved_slot = reserved
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
                    reason=f"Rate limit: {wait:.1f}s remaining on {domain} "
                    f"(delay={state.crawl_delay:.1f}s)",
                    domain=domain,
                )

            # ── Record this request atomically ─────────────────────
            # Update both in-memory and Valkey state before releasing the
            # lock so concurrent tasks see the updated timestamp.
            state.last_request = now
            state.reserved_slot = 0.0
            await self._set_valkey_last_request(domain, now)

        return PolitenessResult(
            action="proceed",
            reason="",
            robots_allowed=True,
            domain=domain,
        )

    def rollback_reservation(self, url: str) -> None:
        """Clear a transient future-slot reservation for a domain.

        Called when a delayed request is aborted before its actual fetch so
        the abandoned reservation does not inflate the next request's delay.
        """
        if not self._enabled:
            return
        domain = self._domain_from_url(url)
        if domain:
            state = self._domains.get(domain)
            if state is not None:
                state.reserved_slot = 0.0

    def record_request(self, url: str) -> None:
        """Record that a request was made to a URL.

        Called after a successful (or attempted) fetch so the rate
        limiter can track timing. Only meaningful when politeness is
        enabled.

        Note: The check() method already records the request atomically
        under the per-domain lock. This method is an additional safety
        net for code paths that bypass check(). The in-memory update is
        idempotent — it will set last_request to approximately the same
        value that check() already set.
        """
        if not self._enabled:
            return
        domain = self._domain_from_url(url)
        if domain:
            state = self._domains.setdefault(domain, _DomainState())
            state.last_request = time.time()
            state.reserved_slot = 0.0

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
