"""Sliding-window rate limiter using Valkey/Redis.

Usage::

    limiter = SlidingWindowRateLimiter(redis, limit=10, window_seconds=60)
    allowed, remaining = await limiter.check("client_ip:search")
"""

import logging
import time

logger = logging.getLogger(__name__)


class SlidingWindowRateLimiter:
    """Sliding-window rate limiter backed by Valkey INCR/EXPIRE.

    Tracks request counts per key within a fixed time window. Counts
    for the current window slot are maintained atomically via Redis
    INCR. The key expires after ``window * 2`` seconds to avoid
    lingering keys.

    The limiter **fails open** — if Redis is unreachable, ``check()``
    returns ``(True, self.limit)`` so that transient infrastructure
    issues do not block legitimate traffic.
    """

    enabled = True

    def __init__(self, redis: object, limit: int, window_seconds: int) -> None:
        self.redis = redis
        self.limit = limit
        self.window = window_seconds

    async def check(self, key: str) -> tuple[bool, int]:
        """Check whether *key* is within the rate limit.

        Args:
            key: Unique identifier for the client (e.g. ``client_ip:search``).

        Returns:
            Tuple of ``(allowed, remaining)`` where *allowed* is
            ``True`` if the request is within the limit, and *remaining*
            is the number of requests still available in the current
            window.
        """
        now = int(time.time())
        window_key = f"rate_limit:search:{key}:{now // self.window}"
        try:
            count = self.redis.incr(window_key)  # type: ignore[attr-defined]
            if count == 1:
                self.redis.expire(window_key, self.window * 2)  # type: ignore[attr-defined]
            remaining = max(0, self.limit - count)
            return count <= self.limit, remaining
        except Exception as e:
            logger.warning("Rate limiter check failed: %s", e)
            return True, self.limit  # Fail open

    def retry_after_seconds(self, now: float | None = None) -> int:
        """Seconds until the current fixed window rolls over.

        The limiter buckets requests by ``now // window``, so a request
        rejected at time *now* is admitted again at the next bucket
        boundary: ``window - (now % window)`` seconds from now.

        Args:
            now: Optional epoch timestamp (injected by tests for
                determinism); defaults to ``time.time()``.

        Returns:
            Whole seconds until the next window boundary (always >= 1
            for a rejected request).
        """
        now = int(now if now is not None else time.time())
        return max(1, self.window - (now % self.window))

    def reset_at_iso(self, now: float | None = None) -> str | None:
        """ISO 8601 UTC timestamp of the next window boundary.

        This is the actual reset instant — ``now + retry_after_seconds`` —
        not the current time. Returns ``None`` when the deployment cannot
        derive a reset time (never the case for this limiter, but kept
        nullable for contract compatibility).
        """
        from datetime import UTC, datetime

        now_ts = now if now is not None else time.time()
        boundary = now_ts + self.retry_after_seconds(now=now_ts)
        return datetime.fromtimestamp(boundary, tz=UTC).isoformat()

    @staticmethod
    def parse_limit(limit_str: str) -> tuple[int, int]:
        """Parse a limit string like ``"10/60s"`` into ``(limit, window_seconds)``.

        Supports suffixes ``s`` (seconds). If no suffix is present,
        the value is treated as seconds.

        Raises:
            ValueError: If the string cannot be parsed.
        """
        parts = limit_str.split("/")
        if len(parts) != 2:
            raise ValueError(
                f"Invalid rate limit format: {limit_str!r} — expected 'count/window' (e.g. '10/60s')"
            )
        limit = int(parts[0])
        window_str = parts[1].strip()
        window_str = window_str.removesuffix("s")
        window = int(window_str)
        if limit <= 0 or window <= 0:
            raise ValueError(
                f"Rate limit values must be positive: limit={limit}, window={window}"
            )
        return limit, window


#: Values of ``AGENT_SEARCH_RATE_LIMIT`` that disable admission limiting.
DISABLED_RATE_LIMIT_VALUES = frozenset({"", "off", "none", "disabled", "0"})


class DisabledRateLimiter:
    """No-op limiter used when per-client admission limiting is turned off.

    Exposes the same surface as :class:`SlidingWindowRateLimiter` so route
    handlers do not branch on limiter type: ``check()`` always admits, and
    callers suppress the ``X-*-Rate-Remaining`` headers by testing
    ``enabled``.
    """

    enabled = False
    limit = 0
    window = 0

    async def check(self, key: str) -> tuple[bool, int]:
        return True, 0

    def retry_after_seconds(self, now: float | None = None) -> int:
        return 0

    def reset_at_iso(self, now: float | None = None) -> str | None:
        return None


def build_rate_limiter(
    redis: object, limit_str: str
) -> SlidingWindowRateLimiter | DisabledRateLimiter:
    """Build a limiter from a ``AGENT_SEARCH_RATE_LIMIT``-style spec.

    An empty spec (or ``off``/``none``/``disabled``/``0``) disables admission
    limiting. Rate limiting exists to protect metered provider budgets, not an
    internal capacity bound, so it is opt-in (ADR-0077).
    """
    normalized = (limit_str or "").strip().lower()
    if normalized in DISABLED_RATE_LIMIT_VALUES:
        return DisabledRateLimiter()
    limit, window = SlidingWindowRateLimiter.parse_limit(normalized)
    return SlidingWindowRateLimiter(redis, limit, window)
