"""TaskTracker: graceful shutdown for fire-and-forget background tasks.

Replaces bare ``asyncio.create_task()`` with tracked tasks that are
automatically removed from tracking on completion. On shutdown,
any remaining tasks get a grace period to finish before cancellation.
"""

import asyncio
import functools
import logging
from collections.abc import Coroutine
from typing import Any

logger = logging.getLogger(__name__)


class TaskTracker:
    """Tracks background tasks for graceful shutdown and per-job cancellation.

    Each task created via ``create_background_task`` is added to a
    tracking set and automatically removed when it completes. On
    ``shutdown()``, any remaining tasks get a configurable grace
    period before being cancelled and awaited.

    Jobs registered with a ``job_id`` also get an ``asyncio.Event`` cancel
    token and their owning task is remembered so ``DELETE`` endpoints can
    set the token and cancel the owning task end-to-end.
    """

    def __init__(self) -> None:
        self._tasks: set[asyncio.Task[Any]] = set()
        self._shutdown_event = asyncio.Event()
        self._job_tokens: dict[str, asyncio.Event] = {}
        self._job_tasks: dict[str, asyncio.Task[Any]] = {}

    def create_background_task(
        self,
        coro: Coroutine[Any, Any, Any],
        job_id: str | None = None,
    ) -> asyncio.Task[Any]:
        """Create, track, and return a background task.

        The task is automatically removed from tracking when it
        completes (whether successful, failed, or cancelled). When
        ``job_id`` is provided, the owning task is remembered for
        per-job cancellation.
        """
        if job_id is not None:
            existing = self._job_tasks.get(job_id)
            if existing is not None and not existing.done():
                # Startup recovery and the request path can observe the same
                # queued record. Keep one worker and preserve its cancel owner.
                coro.close()
                return existing
        task: asyncio.Task[Any] = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        if job_id is not None:
            self._job_tasks[job_id] = task
            task.add_done_callback(functools.partial(self._job_task_done, job_id))
        return task

    def _job_task_done(self, job_id: str, _task: asyncio.Task[Any]) -> None:
        """Remove a completed/cancelled job's owning task and token."""
        self._job_tasks.pop(job_id, None)
        self._job_tokens.pop(job_id, None)

    def cancel_token(self, job_id: str) -> asyncio.Event:
        """Return (creating if needed) the per-job cancel token."""
        token = self._job_tokens.get(job_id)
        if token is None:
            token = asyncio.Event()
            self._job_tokens[job_id] = token
        return token

    def cancel_job(self, job_id: str) -> bool:
        """Set the cancel token (if any) and cancel the owning task.

        Returns ``True`` if an owning task existed and was cancelled,
        ``False`` otherwise (e.g. a streaming/inline job with no
        background owner).
        """
        token = self._job_tokens.get(job_id)
        if token is not None:
            token.set()
        task = self._job_tasks.get(job_id)
        if task is not None and not task.done():
            task.cancel()
            return True
        return False

    @property
    def shutdown_requested(self) -> bool:
        return self._shutdown_event.is_set()

    @property
    def shutdown_event(self) -> asyncio.Event:
        return self._shutdown_event

    async def shutdown(self, grace_period: float = 5.0) -> None:
        """Signal shutdown, cancel tracked tasks after grace period.

        Best-effort: tasks get up to *grace_period* seconds to finish
        normally. After that, remaining tasks are cancelled and awaited
        with ``return_exceptions=True`` to avoid unhandled exceptions
        during shutdown.
        """
        self._shutdown_event.set()
        if not self._tasks:
            return

        logger.info(
            "Shutting down %d background tasks (grace=%ss)",
            len(self._tasks),
            grace_period,
        )

        _, pending = await asyncio.wait(self._tasks, timeout=grace_period)

        if pending:
            logger.warning(
                "Cancelling %d tasks after %ss grace period",
                len(pending),
                grace_period,
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
