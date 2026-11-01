"""In-process weighted admission control across resource classes.

GroktoCrawl bounds concurrency inside individual operations (per-request
semaphores), but those limits do not compose into a service-wide capacity
budget. ``AdmissionController`` is a single-process (NOT distributed)
weighted limiter that composes per-class budgets across concurrent jobs.

See ADR-0051 for the design rationale.

- Three resource classes: ``lightweight_fetch``, ``browser``, ``llm``.
- Each in-flight operation consumes a positive integer ``weight`` from a
  per-class budget. Heavier classes cost more (fetch=1, llm=4, browser=8)
  so an expensive browser lifecycle is never treated as a cheap HTTP fetch.
- ``acquire`` admits immediately when capacity is available; otherwise it
  enqueues a FIFO waiter and waits up to ``timeout``. A bounded queue
  rejects overflow rather than growing without limit.
- ``release`` returns budget and wakes FIFO waiters while they fit.

Metrics (bounded ``class`` label; no URLs): ``admission_active``,
``admission_queue_depth``, ``admission_wait_seconds``,
``admission_rejected_total``, ``admission_cancelled_total``.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from common.metrics import METRICS

RESOURCE_CLASSES = ("lightweight_fetch", "browser", "llm")

# Weighted units consumed per in-flight operation.
DEFAULT_WEIGHTS: dict[str, int] = {
    "lightweight_fetch": 1,
    "llm": 4,
    "browser": 8,
}

# Maximum number of waiters allowed per class before overflow is rejected.
DEFAULT_QUEUE_CAPACITY = 100


class AdmissionRejectedError(Exception):
    """Raised when an admission request cannot be admitted."""


@dataclass
class _Waiter:
    """A queued acquire request."""

    weight: int
    future: asyncio.Future[None]
    queued_at: float


class AdmissionController:
    """Weighted in-process admission controller over resource classes.

    State mutation is synchronous-only (no ``await`` between a read and the
    matching write), so it is safe under the asyncio cooperative scheduler
    without an explicit lock. Callers must run ``acquire``/``release`` on
    the same event loop.
    """

    def __init__(
        self,
        limits: dict[str, int],
        weights: dict[str, int] | None = None,
        queue_capacity: int = DEFAULT_QUEUE_CAPACITY,
    ) -> None:
        self._limits: dict[str, int] = {
            c: max(0, int(limits.get(c, 0))) for c in RESOURCE_CLASSES
        }
        self._weights: dict[str, int] = dict(weights or DEFAULT_WEIGHTS)
        self._queue_capacity = queue_capacity
        self._active: dict[str, int] = dict.fromkeys(RESOURCE_CLASSES, 0)
        self._queues: dict[str, deque[_Waiter]] = {c: deque() for c in RESOURCE_CLASSES}

        self._active_gauge = METRICS.gauge(
            "admission_active",
            "Currently active weighted units by resource class",
            ["class"],
        )
        self._queue_gauge = METRICS.gauge(
            "admission_queue_depth",
            "Waiters queued for admission by resource class",
            ["class"],
        )
        self._wait_hist = METRICS.histogram(
            "admission_wait_seconds",
            "Admission wait time by resource class",
            ["class"],
        )
        self._rejected = METRICS.counter(
            "admission_rejected_total",
            "Admission rejections by resource class",
            ["class"],
        )
        self._cancelled = METRICS.counter(
            "admission_cancelled_total",
            "Admission wait cancellations by resource class",
            ["class"],
        )

    def weight_for(self, resource_class: str) -> int:
        return self._weights.get(resource_class, 1)

    def budget_for(self, resource_class: str) -> int:
        return self._limits.get(resource_class, 0)

    def active(self, resource_class: str) -> int:
        return self._active.get(resource_class, 0)

    def queue_depth(self, resource_class: str) -> int:
        return len(self._queues.get(resource_class, ()))

    async def acquire(
        self,
        resource_class: str,
        weight: int = 1,
        timeout: float | None = None,
    ) -> None:
        """Admit ``weight`` units of ``resource_class``, waiting if necessary.

        Raises ``AdmissionRejectedError`` when the request cannot be admitted
        (weight exceeds the budget, the bounded queue is full, or the wait
        times out). Raises ``asyncio.CancelledError`` when the caller is
        cancelled while waiting.
        """
        if resource_class not in RESOURCE_CLASSES:
            raise ValueError(f"unknown resource class: {resource_class!r}")
        if weight <= 0:
            raise ValueError("weight must be positive")

        limit = self._limits[resource_class]
        if limit == 0:
            # No budget configured: the class is unlimited (ADR-0077). Keep the
            # active counter and gauge symmetric with release() so operators can
            # still observe concurrency.
            self._active[resource_class] += weight
            self._set_active(resource_class)
            return
        if weight > limit:
            self._reject(resource_class)
            raise AdmissionRejectedError(
                f"admission rejected for {resource_class}: "
                f"weight {weight} exceeds budget {limit}"
            )

        queue = self._queues[resource_class]

        # Fast path: no waiters and enough headroom.
        if not queue and self._active[resource_class] + weight <= limit:
            self._active[resource_class] += weight
            self._set_active(resource_class)
            return

        if len(queue) >= self._queue_capacity:
            self._reject(resource_class)
            raise AdmissionRejectedError(
                f"admission rejected for {resource_class}: queue full"
            )

        loop = asyncio.get_running_loop()
        future: asyncio.Future[None] = loop.create_future()
        waiter = _Waiter(weight=weight, future=future, queued_at=time.monotonic())
        queue.append(waiter)
        self._set_queue_depth(resource_class)

        started = time.monotonic()
        try:
            if timeout is None:
                await future
            else:
                await asyncio.wait_for(future, timeout=timeout)
        except TimeoutError:
            self._drop_waiter(resource_class, waiter)
            self._reject(resource_class)
            raise AdmissionRejectedError(
                f"admission rejected for {resource_class}: "
                f"wait timed out after {timeout}s"
            )
        except asyncio.CancelledError:
            # Refund the weight if _drain already granted this waiter before the
            # cancellation was delivered (future resolved, waiter popped, _active
            # incremented); otherwise the budget unit leaks forever.
            if waiter.future.done() and not waiter.future.cancelled():
                self._active[resource_class] -= waiter.weight
                self._set_active(resource_class)
            self._drop_waiter(resource_class, waiter)
            self._cancelled.inc({"class": resource_class})
            raise
        finally:
            self._wait_hist.observe(
                {"class": resource_class}, time.monotonic() - started
            )

    def release(self, resource_class: str, weight: int = 1) -> None:
        """Return ``weight`` units of ``resource_class`` and wake waiters."""
        if resource_class not in RESOURCE_CLASSES or weight <= 0:
            return
        self._active[resource_class] = max(0, self._active[resource_class] - weight)
        self._drain(resource_class)

    @asynccontextmanager
    async def resource(
        self,
        resource_class: str,
        weight: int = 1,
        timeout: float | None = None,
    ) -> AsyncIterator[None]:
        """Async context manager wrapping :meth:`acquire`/:meth:`release`."""
        await self.acquire(resource_class, weight=weight, timeout=timeout)
        try:
            yield
        finally:
            self.release(resource_class, weight=weight)

    def _drain(self, resource_class: str) -> None:
        """Grant budget to queued waiters in FIFO order while they fit."""
        queue = self._queues[resource_class]
        limit = self._limits[resource_class]
        while queue:
            waiter = queue[0]
            if waiter.future.done():
                # Waiter was cancelled or timed out before being granted.
                queue.popleft()
                continue
            if self._active[resource_class] + waiter.weight > limit:
                break
            queue.popleft()
            self._active[resource_class] += waiter.weight
            waiter.future.set_result(None)
        self._set_active(resource_class)
        self._set_queue_depth(resource_class)

    def _drop_waiter(self, resource_class: str, waiter: _Waiter) -> None:
        """Remove a waiter that timed out or was cancelled."""
        queue = self._queues[resource_class]
        if waiter in queue:
            queue.remove(waiter)
            self._set_queue_depth(resource_class)

    def _reject(self, resource_class: str) -> None:
        self._rejected.inc({"class": resource_class})

    def _set_active(self, resource_class: str) -> None:
        self._active_gauge.set(
            {"class": resource_class}, float(self._active[resource_class])
        )

    def _set_queue_depth(self, resource_class: str) -> None:
        self._queue_gauge.set(
            {"class": resource_class}, float(len(self._queues[resource_class]))
        )
