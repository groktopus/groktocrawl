"""Recycle a dedicated container, never silently release uncertain resources."""

import asyncio
import logging
import os
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger(__name__)
RECYCLE_GRACE_SECONDS = 30.0


def dedicated_container() -> bool:
    """Guard an explicit deployment assertion against accidental host execution."""
    return Path("/.dockerenv").exists() and os.getppid() == 1


class ContainerRecycler:
    """One fixed grace period per process; quarantine stays closed throughout."""

    def __init__(
        self,
        *,
        exit_process: Callable[[int], object] = os._exit,
        grace_seconds: float = RECYCLE_GRACE_SECONDS,
    ) -> None:
        self.task: asyncio.Task | None = None
        self._stopped = False
        self._exit_process = exit_process
        self._grace_seconds = grace_seconds

    def schedule(self) -> None:
        if self._stopped or self.task is not None:
            return
        if os.environ.get("BROWSER_CONTAINER_RECYCLE_ON_QUARANTINE") != "1":
            return
        if not dedicated_container():
            raise RuntimeError("Browser recycling requires a dedicated container")
        logger.error(
            "Browser container recycle scheduled; admission remains closed; grace_seconds=%s",
            self._grace_seconds,
        )
        self.task = asyncio.create_task(self._recycle())

    async def _recycle(self) -> None:
        # asyncio uses the event loop's monotonic clock. Repeated failures cannot
        # extend this deadline. Do not await cancellation-resistant browser cleanup.
        await asyncio.sleep(self._grace_seconds)
        logger.error(
            "Browser container recycling after uncertain cleanup; exit_code=75"
        )
        # Supported only as the single application process under container init.
        # The container boundary, not an in-memory reset, terminates descendants.
        self._exit_process(75)

    async def stop(self) -> None:
        self._stopped = True
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
