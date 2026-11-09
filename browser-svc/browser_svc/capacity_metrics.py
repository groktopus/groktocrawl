"""Fresh per-scrape browser snapshots and finite lifecycle event counters."""

import time
from collections import Counter
from collections.abc import Iterable

from common.browser_metrics import (
    ADMISSION_REASONS,
    FAILURE_REASONS,
    PHASES,
    Samples,
    render_samples,
)

from .process_health import MIN_TASK_HEADROOM


class CapacityMetrics:
    def __init__(self) -> None:
        self.started = time.time()
        self.events: Counter[tuple[str, str]] = Counter()
        for name, labels in (
            ("admission_rejections_total", ADMISSION_REASONS),
            ("creation_failures_total", FAILURE_REASONS),
            ("cleanup_incomplete_total", ("",)),
        ):
            for label in labels:
                self.events[(name, label)] = 0

    def increment(self, name: str, label: str = "") -> None:
        key = (name, label)
        if key not in self.events:
            raise ValueError("unknown browser event")
        self.events[key] += 1

    def render(
        self, *, capacity: dict, phases: Iterable[str], limit: int, admission: bool
    ) -> str:
        counts = Counter(phases)
        samples: Samples = dict(self.events)
        for name, value in {
            "minimum_task_headroom": MIN_TASK_HEADROOM,
            "reserved_sessions": sum(counts.values()),
            "session_limit": limit,
            "admission_available": int(admission),
            "service_start_time_seconds": self.started,
            "cgroup_collection_success": int(capacity["state"] != "unknown"),
            "cgroup_limit_events_collection_success": int(
                capacity.get("limit_events") is not None
            ),
        }.items():
            samples[(name, "")] = value
        for phase in PHASES:
            samples[("owners", phase)] = counts[phase]
        for name, field in (
            ("cgroup_tasks", "current_tasks"),
            ("cgroup_task_limit", "task_limit"),
            ("cgroup_limit_events_total", "limit_events"),
        ):
            if capacity.get(field) is not None:
                samples[(name, "")] = capacity[field]
        return render_samples(samples)
