"""Bounded native model inference execution for semantic-svc."""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from metrics import METRICS


def _positive_int_env(name: str, default: int, *, minimum: int = 1) -> int:
    """Read a bounded integer setting and fail fast on invalid config."""
    value = int(os.getenv(name, str(default)))
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


def _nonnegative_int_env(name: str, default: int) -> int:
    """Read a queue-size setting that may be zero (no waiting queue)."""
    value = int(os.getenv(name, str(default)))
    if value < 0:
        raise ValueError(f"{name} must be >= 0")
    return value


INFERENCE_WORKERS = _positive_int_env("SEMANTIC_INFERENCE_WORKERS", 1)
INFERENCE_QUEUE_SIZE = _nonnegative_int_env("SEMANTIC_INFERENCE_QUEUE_SIZE", 16)
INFERENCE_ADMISSION_TIMEOUT = float(
    os.getenv("SEMANTIC_INFERENCE_ADMISSION_TIMEOUT_SECONDS", "0.25")
)
if INFERENCE_ADMISSION_TIMEOUT <= 0:
    raise ValueError("SEMANTIC_INFERENCE_ADMISSION_TIMEOUT_SECONDS must be > 0")

_INFERENCE_OPERATIONS = frozenset(
    {
        "rerank",
        "embed",
        "vector_search",
        "index",
        "index_batch",
        "migration_backfill",
    }
)
_INFERENCE_PRIORITIES = {"interactive": 0, "maintenance": 10}


class InferenceOverloadedError(Exception):
    """Raised when bounded inference admission cannot accept a request."""

    def __init__(self, operation: str, retry_after: float) -> None:
        self.operation = operation
        self.retry_after = retry_after
        super().__init__(f"semantic inference capacity exhausted for {operation}")


InferenceOverloaded = InferenceOverloadedError


@dataclass
class _InferenceWork:
    """One admitted native call and its result future."""

    operation: str
    priority: int
    function: Callable[[], Any]
    result: asyncio.Future[Any]
    admitted_at: float
    abandoned: bool = False
    sequence: int = field(default=0)


class InferenceManager:
    """Run model calls in a bounded, priority-aware native worker pool."""

    def __init__(
        self,
        *,
        max_workers: int = INFERENCE_WORKERS,
        queue_size: int = INFERENCE_QUEUE_SIZE,
        admission_timeout: float = INFERENCE_ADMISSION_TIMEOUT,
    ) -> None:
        if max_workers < 1:
            raise ValueError("max_workers must be >= 1")
        if queue_size < 0:
            raise ValueError("queue_size must be >= 0")
        if admission_timeout <= 0:
            raise ValueError("admission_timeout must be > 0")
        self.max_workers = max_workers
        self.queue_size = queue_size
        self.admission_timeout = admission_timeout
        self._capacity = asyncio.Semaphore(max_workers + queue_size)
        self._queue: asyncio.PriorityQueue[tuple[int, int, _InferenceWork | None]] = (
            asyncio.PriorityQueue()
        )
        self._workers: list[asyncio.Task[None]] = []
        self._loop: asyncio.AbstractEventLoop | None = None
        self._sequence = 0
        self._closed = False
        self._queued = 0
        self._in_flight = 0

    @staticmethod
    def _operation_name(operation: str) -> str:
        return operation if operation in _INFERENCE_OPERATIONS else "other"

    def _observe_queue_depth(self) -> None:
        METRICS.gauge(
            "groktocrawl_semantic_inference_queue_depth",
            "Number of admitted model calls waiting for a native worker",
        ).set(value=float(self._queued))

    def _observe_in_flight(self) -> None:
        METRICS.gauge(
            "groktocrawl_semantic_inference_in_flight",
            "Number of native model calls currently running",
        ).set(value=float(self._in_flight))

    async def _ensure_started(self) -> None:
        if self._closed:
            raise InferenceOverloadedError("other", self.admission_timeout)
        loop = asyncio.get_running_loop()
        if self._loop is not None and self._loop is not loop:
            if self._in_flight or self._queued:
                raise InferenceOverloadedError("other", self.admission_timeout)
            self._workers = []
            self._capacity = asyncio.Semaphore(self.max_workers + self.queue_size)
            self._queue = asyncio.PriorityQueue()
        self._loop = loop
        if not self._workers:
            self._workers = [
                asyncio.create_task(self._worker()) for _ in range(self.max_workers)
            ]

    async def run(
        self,
        operation: str,
        function: Callable[[], Any],
        *,
        priority: str = "interactive",
    ) -> Any:
        """Admit and execute one blocking model call.

        Caller cancellation abandons only the result future. The manager-owned
        worker keeps the native call and its capacity slot until completion.
        """
        operation = self._operation_name(operation)
        priority_value = _INFERENCE_PRIORITIES.get(priority, 0)
        await self._ensure_started()
        admission_start = time.monotonic()
        try:
            await asyncio.wait_for(
                self._capacity.acquire(), timeout=self.admission_timeout
            )
        except TimeoutError as exc:
            METRICS.counter(
                "groktocrawl_semantic_inference_overloads_total",
                "Model calls rejected after bounded admission timeout",
                ["operation"],
            ).inc({"operation": operation})
            raise InferenceOverloadedError(operation, self.admission_timeout) from exc

        if self._closed:
            self._capacity.release()
            raise InferenceOverloadedError(operation, self.admission_timeout)

        loop = asyncio.get_running_loop()
        item = _InferenceWork(
            operation=operation,
            priority=priority_value,
            function=function,
            result=loop.create_future(),
            admitted_at=admission_start,
            sequence=self._sequence,
        )
        self._sequence += 1
        self._queued += 1
        self._observe_queue_depth()
        self._queue.put_nowait((item.priority, item.sequence, item))
        try:
            return await asyncio.shield(item.result)
        except asyncio.CancelledError:
            item.abandoned = True
            METRICS.counter(
                "groktocrawl_semantic_inference_cancellations_total",
                "HTTP callers canceled while model inference was admitted",
                ["operation"],
            ).inc({"operation": operation})
            raise

    async def _worker(self) -> None:
        while True:
            if self._closed and self._queue.empty():
                return
            _, _, item = await self._queue.get()
            if item is None:
                self._queue.task_done()
                return
            self._queued -= 1
            self._observe_queue_depth()
            if item.abandoned:
                self._capacity.release()
                self._queue.task_done()
                continue

            queue_wait = time.monotonic() - item.admitted_at
            METRICS.histogram(
                "groktocrawl_semantic_inference_queue_wait_seconds",
                "Time spent admitted before native model execution",
                ["operation", "priority"],
            ).observe(
                {
                    "operation": item.operation,
                    "priority": "maintenance" if item.priority else "interactive",
                },
                queue_wait,
            )
            self._in_flight += 1
            self._observe_in_flight()
            native_start = time.monotonic()
            native_future: asyncio.Future[Any] | None = None
            try:
                loop = asyncio.get_running_loop()
                native_future = loop.run_in_executor(None, item.function)
                result = await asyncio.shield(native_future)
            except asyncio.CancelledError:
                # Worker cancellation may come from event-loop teardown. Drain
                # the shielded native future before releasing its capacity.
                if native_future is not None:
                    try:
                        drained = await asyncio.shield(native_future)
                    except Exception as exc:
                        if not item.abandoned and not item.result.done():
                            item.result.set_exception(exc)
                    else:
                        if not item.abandoned and not item.result.done():
                            item.result.set_result(drained)
                raise
            except Exception as exc:
                if not item.abandoned and not item.result.done():
                    item.result.set_exception(exc)
                METRICS.counter(
                    "groktocrawl_semantic_inference_failures_total",
                    "Native model calls that raised an exception",
                    ["operation"],
                ).inc({"operation": item.operation})
            else:
                if not item.abandoned and not item.result.done():
                    item.result.set_result(result)
            finally:
                METRICS.histogram(
                    "groktocrawl_semantic_inference_duration_seconds",
                    "Native model inference latency",
                    ["operation"],
                ).observe(
                    {"operation": item.operation}, time.monotonic() - native_start
                )
                self._in_flight -= 1
                self._observe_in_flight()
                self._capacity.release()
                self._queue.task_done()

    async def shutdown(self) -> None:
        """Reject queued work, drain native calls, then stop workers."""
        if self._closed:
            return
        self._closed = True
        while not self._queue.empty():
            _, _, item = self._queue.get_nowait()
            if item is None:
                self._queue.task_done()
                continue
            self._queued -= 1
            item.abandoned = True
            if not item.result.done():
                item.result.cancel()
            self._capacity.release()
            self._queue.task_done()
        self._observe_queue_depth()
        if self._workers:
            for _ in self._workers:
                self._queue.put_nowait((100, self._sequence, None))
                self._sequence += 1
            await asyncio.gather(*self._workers, return_exceptions=True)


_inference_manager = InferenceManager()


def get_inference_manager() -> InferenceManager:
    """Return the process-wide manager used by application routes."""
    return _inference_manager


async def run_inference(
    operation: str,
    function: Callable[[], Any],
    *,
    priority: str = "interactive",
) -> Any:
    """Execute a model call through the process-wide bounded inference pool."""
    return await get_inference_manager().run(operation, function, priority=priority)
