"""Cheap, sanitized process-budget diagnostics; never starts a subprocess."""

from pathlib import Path

# Conservative reserve for a Playwright driver plus a Chromium session. This
# detects capacity pressure, not guaranteed readiness or OS-wide exhaustion.
MIN_TASK_HEADROOM = 16
CGROUP_ROOTS = (Path("/sys/fs/cgroup"), Path("/sys/fs/cgroup/pids"))


def process_capacity(roots: tuple[Path, ...] = CGROUP_ROOTS) -> dict[str, object]:
    """Read bounded v2/v1 controller values without exposing host details."""
    for root in roots:
        try:
            with (root / "pids.current").open(encoding="ascii") as stream:
                current_text = stream.read(128).strip()
            with (root / "pids.max").open(encoding="ascii") as stream:
                limit_text = stream.read(128).strip()
            if len(current_text) >= 128 or len(limit_text) >= 128:
                continue
            current = int(current_text)
            limit = None if limit_text == "max" else int(limit_text)
            if current < 0 or (limit is not None and limit < 0):
                continue
        except (OSError, UnicodeError, ValueError):
            continue
        headroom = None if limit is None else max(0, limit - current)
        pressured = headroom is not None and headroom < MIN_TASK_HEADROOM
        return {
            "state": "degraded" if pressured else "ok",
            "reason": "process_capacity_low"
            if pressured
            else "process_budget_available",
            "current_tasks": current,
            "task_limit": limit,
            "available_tasks": headroom,
            "minimum_task_headroom": MIN_TASK_HEADROOM,
            "scope": "container_cgroup",
        }
    return {"state": "unknown", "reason": "process_budget_unavailable"}
