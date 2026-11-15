"""Sanitized local process-budget checks for the embedded browser tier."""

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path

MIN_TASK_HEADROOM = 100
CGROUP_ROOTS = (Path("/sys/fs/cgroup"), Path("/sys/fs/cgroup/pids"))
_launch_lock = asyncio.Lock()


class ProcessCapacityLowError(RuntimeError):
    """The cgroup lacks the measured headroom reserved for a browser launch."""


def process_capacity(roots: tuple[Path, ...] = CGROUP_ROOTS) -> dict[str, object]:
    """Read cgroup task capacity without starting a subprocess or exposing paths."""
    for root in roots:
        try:
            with (root / "pids.current").open(encoding="ascii") as stream:
                current_text = stream.read(128)
            with (root / "pids.max").open(encoding="ascii") as stream:
                limit_text = stream.read(128)
            if len(current_text) >= 128 or len(limit_text) >= 128:
                continue
            current = int(current_text.strip())
            raw_limit = limit_text.strip()
            limit = None if raw_limit == "max" else int(raw_limit)
            if current < 0 or (limit is not None and limit < 0):
                continue
        except (OSError, UnicodeError, ValueError):
            continue
        headroom = None if limit is None else max(0, limit - current)
        low = headroom is not None and headroom < MIN_TASK_HEADROOM
        return {
            "state": "degraded" if low else "ok",
            "reason": "process_capacity_low" if low else "process_budget_available",
            "current_tasks": current,
            "task_limit": limit,
            "available_tasks": headroom,
            "minimum_task_headroom": MIN_TASK_HEADROOM,
            "scope": "container_cgroup",
        }
    return {"state": "unknown", "reason": "process_budget_unavailable"}


async def launch_with_process_headroom[T](
    launch: Callable[[], Awaitable[T]],
) -> T:
    """Serialize browser launches and recheck cgroup headroom at launch time."""
    async with _launch_lock:
        capacity = process_capacity()
        available = capacity.get("available_tasks")
        if capacity["state"] == "degraded" or (
            isinstance(available, int) and available < MIN_TASK_HEADROOM
        ):
            raise ProcessCapacityLowError("process_capacity_low")
        return await launch()
