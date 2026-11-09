"""Own browser work before startup and retain uncertain teardown fail-closed."""

import asyncio
import logging
from collections.abc import Awaitable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)
CREATE_TIMEOUT = 20.0
CLOSE_TIMEOUT = 2.0
MAX_SESSIONS = 2


@dataclass
class BrowserOwner:
    """A reservation lasts through startup, use, and verified teardown."""

    manager: Any = None
    playwright: Any = None
    browser: Any = None
    context: Any = None
    page: Any = None
    phase: str = "creating"
    startup: asyncio.Task | None = None
    creation: asyncio.Task | None = None
    cleanup: asyncio.Task | None = None
    pending: set[asyncio.Task] = field(default_factory=set)
    late_cleanup: asyncio.Task | None = None

    async def start(self) -> None:
        async def acquire():
            self.playwright = await self.manager.start()

        self.startup = asyncio.create_task(acquire())
        self.startup.add_done_callback(self._consume_cleanup)
        # Cancelling start before the subprocess transport connects makes
        # Playwright.__aexit__ race its untracked connection.run task. Retain
        # startup until it yields a controller, then stop that controller.
        await asyncio.shield(self.startup)

    async def bounded(self, operation: Awaitable, timeout: float) -> bool:
        """Do not let cancellation-resistant dependencies extend a deadline."""
        task = asyncio.ensure_future(operation)
        self.pending.add(task)
        task.add_done_callback(self._finished)
        done, _ = await asyncio.wait({task}, timeout=timeout)
        if not done:
            task.cancel()
            return False
        if task.cancelled():
            return False
        return task.exception() is None

    def _finished(self, task: asyncio.Task) -> None:
        self.pending.discard(task)
        if not task.cancelled():
            task.exception()  # Retrieve late failures without losing task ownership.

    async def close(self) -> bool:
        """Attempt every close; uncertainty keeps the reservation quarantined."""
        complete = True
        if self.creation is not None and not self.creation.done():
            self.creation.cancel()
            done, _ = await asyncio.wait({self.creation}, timeout=CLOSE_TIMEOUT)
            if not done:
                # The initializer can still acquire resources. Its owner remains
                # registered, and the completion callback performs a late close.
                self.creation.add_done_callback(self._late_creation)
                complete = False
        if self.startup is not None and not self.startup.done():
            done, _ = await asyncio.wait({self.startup}, timeout=CLOSE_TIMEOUT)
            if not done:
                self.startup.add_done_callback(self._late_creation)
                # Do not call __aexit__ before its transport exists. Retain
                # ownership and fail closed until late startup can be stopped.
                return False
        for resource, method in (
            (self.page, "close"),
            (self.context, "close"),
            (self.browser, "close"),
            (self.playwright, "stop"),
        ):
            if resource is not None:
                try:
                    complete = (
                        await self.bounded(getattr(resource, method)(), CLOSE_TIMEOUT)
                        and complete
                    )
                except Exception:
                    logger.exception("Browser resource cleanup failed")
                    complete = False
        if self.playwright is None and self.manager is not None:
            try:
                complete = (
                    await self.bounded(
                        self.manager.__aexit__(None, None, None), CLOSE_TIMEOUT
                    )
                    and complete
                )
            except Exception:
                logger.exception("Partial Playwright startup cleanup failed")
                complete = False
        if self.pending:
            complete = False
        return complete

    def _late_creation(self, task: asyncio.Task) -> None:
        if not task.cancelled():
            task.exception()
        # Never free uncertain ownership automatically: a timed-out third-party
        # operation may have spawned additional work. Close late handles while
        # leaving readiness degraded until an operator restarts the service.
        if self.late_cleanup is None:
            self.late_cleanup = asyncio.create_task(self._close_late())
            self.late_cleanup.add_done_callback(self._consume_cleanup)

    async def _close_late(self) -> None:
        if self.cleanup is not None:
            await asyncio.shield(self.cleanup)
        await self.close()

    @staticmethod
    def _consume_cleanup(task: asyncio.Task) -> None:
        if not task.cancelled():
            task.exception()
