"""Own browser work before startup and retain uncertain teardown fail-closed."""

import asyncio
import logging
import time
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

    async def bounded(
        self, operation: Awaitable, timeout: float, stage: str = "resource"
    ) -> bool:
        """Do not let cancellation-resistant dependencies extend a deadline."""
        started = time.monotonic()
        task = asyncio.ensure_future(operation)
        self.pending.add(task)
        task.add_done_callback(self._finished)
        done, _ = await asyncio.wait({task}, timeout=timeout)
        error_type = "none"
        if not done:
            task.cancel()
            outcome = "timeout"
        elif task.cancelled():
            outcome = "cancelled"
        elif (error := task.exception()) is not None:
            outcome = "exception"
            error_type = type(error).__name__
        else:
            return True
        # Exception messages/tracebacks can contain URLs, cookies or page data.
        logger.warning(
            "Browser cleanup stage=%s outcome=%s error_type=%s elapsed_ms=%.1f",
            stage,
            outcome,
            error_type,
            (time.monotonic() - started) * 1000,
        )
        return False

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
                logger.warning("Browser cleanup stage=creation outcome=timeout")
                complete = False
        if self.startup is not None and not self.startup.done():
            done, _ = await asyncio.wait({self.startup}, timeout=CLOSE_TIMEOUT)
            if not done:
                self.startup.add_done_callback(self._late_creation)
                logger.warning("Browser cleanup stage=startup outcome=timeout")
                # Do not call __aexit__ before its transport exists. Retain
                # ownership and fail closed until late startup can be stopped.
                return False
        for stage, resource, method in (
            ("page", self.page, "close"),
            ("context", self.context, "close"),
            ("browser", self.browser, "close"),
            ("playwright", self.playwright, "stop"),
        ):
            if resource is not None:
                try:
                    complete = (
                        await self.bounded(
                            getattr(resource, method)(), CLOSE_TIMEOUT, stage
                        )
                        and complete
                    )
                except Exception as error:
                    logger.warning(
                        "Browser cleanup stage=%s outcome=invoke_exception error_type=%s",
                        stage,
                        type(error).__name__,
                    )
                    complete = False
        if self.playwright is None and self.manager is not None:
            try:
                complete = (
                    await self.bounded(
                        self.manager.__aexit__(None, None, None),
                        CLOSE_TIMEOUT,
                        "manager",
                    )
                    and complete
                )
            except Exception as error:
                logger.warning(
                    "Browser cleanup stage=manager outcome=invoke_exception error_type=%s",
                    type(error).__name__,
                )
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
