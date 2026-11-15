"""Fail closed and recycle the dedicated scraper container after uncertain cleanup."""

import asyncio
import logging
import os
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger(__name__)
RECYCLE_GRACE_SECONDS = 30.0


def dedicated_container() -> bool:
    """Require the supported Docker init + single application process topology."""
    return Path("/.dockerenv").exists() and os.getppid() == 1


class ContainerRecycler:
    """Schedule one fixed-delay process exit; never clear uncertain ownership."""

    def __init__(
        self,
        *,
        exit_process: Callable[[int], object] = os._exit,
        grace_seconds: float = RECYCLE_GRACE_SECONDS,
    ) -> None:
        self.task: asyncio.Task[None] | None = None
        self._stopped = False
        self._exit_process = exit_process
        self._grace_seconds = grace_seconds

    def schedule(self) -> None:
        if self._stopped or self.task is not None:
            return
        if os.environ.get("SCRAPER_CONTAINER_RECYCLE_ON_QUARANTINE") != "1":
            return
        if not dedicated_container():
            raise RuntimeError("Scraper recycling requires a dedicated container")
        logger.error(
            "Scraper container recycle scheduled; browser admission remains closed; grace_seconds=%s",
            self._grace_seconds,
        )
        self.task = asyncio.create_task(
            self._recycle(), name="scraper-container-recycle"
        )

    async def _recycle(self) -> None:
        await asyncio.sleep(self._grace_seconds)
        logger.error(
            "Scraper container recycling after uncertain browser cleanup; exit_code=75"
        )
        self._exit_process(75)

    async def stop(self) -> None:
        self._stopped = True
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)


_recycler = ContainerRecycler()


def schedule_container_recycle() -> None:
    _recycler.schedule()


async def stop_container_recycle() -> None:
    await _recycler.stop()
