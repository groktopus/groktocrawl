"""Closed browser telemetry contract shared by the source and safe relay.

Only enum-labelled numeric samples cross the renderer boundary. Source HELP,
labels, timestamps, exemplars and unrelated metrics are never forwarded.
"""

import math
import re
from dataclasses import dataclass

PREFIX = "groktocrawl_browser_"
PHASES = ("creating", "active", "closing", "quarantined")
ADMISSION_REASONS = (
    "shutting_down",
    "cleanup_incomplete",
    "creation_in_progress",
    "session_capacity_reached",
    "process_capacity_low",
)
FAILURE_REASONS = ("timeout", "error", "cancelled")
MAX_METRICS_BYTES = 65536


@dataclass(frozen=True)
class Metric:
    help: str
    label: str = ""
    values: tuple[str, ...] = ("",)
    optional: bool = False


METRICS = {
    "cgroup_tasks": Metric(
        "Tasks including threads in the local cgroup", optional=True
    ),
    "cgroup_task_limit": Metric("Finite local cgroup task limit", optional=True),
    "cgroup_limit_events_total": Metric(
        "Local cgroup cumulative task limit hits", optional=True
    ),
    "minimum_task_headroom": Metric("Tasks reserved for one browser launch"),
    "active_sessions": Metric("Currently active browser sessions"),
    "owners": Metric("Reserved browser owners by lifecycle phase", "phase", PHASES),
    "reserved_sessions": Metric("Browser reservations including unfinished cleanup"),
    "session_limit": Metric("Maximum browser session reservations"),
    "admission_available": Metric(
        "Current admission decision, not a readiness guarantee"
    ),
    "admission_rejections_total": Metric(
        "Rejected browser creation requests", "reason", ADMISSION_REASONS
    ),
    "creation_failures_total": Metric(
        "Failed browser creation attempts", "reason", FAILURE_REASONS
    ),
    "cleanup_incomplete_total": Metric(
        "Browser owners quarantined after incomplete cleanup"
    ),
    "cgroup_collection_success": Metric(
        "Whether current task and limit values were read"
    ),
    "cgroup_limit_events_collection_success": Metric(
        "Whether the local task limit hit counter was read"
    ),
    "service_start_time_seconds": Metric(
        "Browser service process start time, Unix seconds"
    ),
}
Samples = dict[tuple[str, str], float]


def render_samples(samples: Samples) -> str:
    """Render canonical metadata and bounded samples, without an EOF marker."""
    lines = []
    for name, spec in METRICS.items():
        values = [
            (value, samples[(name, value)])
            for value in spec.values
            if (name, value) in samples
        ]
        if not values:
            continue
        family = PREFIX + name.removesuffix("_total")
        kind = "counter" if name.endswith("_total") else "gauge"
        lines += [f"# HELP {family} {spec.help}", f"# TYPE {family} {kind}"]
        for label, value in values:
            suffix = f'{{{spec.label}="{label}"}}' if spec.label else ""
            lines.append(f"{PREFIX}{name}{suffix} {value}")
    return "\n".join(lines) + "\n"


def parse_samples(body: str) -> Samples:
    """Validate a complete snapshot; reject known samples with unsafe shape."""
    if len(body.encode("utf-8")) > MAX_METRICS_BYTES:
        raise ValueError("browser metrics body too large")
    samples: Samples = {}
    for line in body.splitlines():
        if not line or line.startswith("#"):
            continue
        name = re.split(r"[\s{]", line, maxsplit=1)[0].removeprefix(PREFIX)
        if name not in METRICS or not line.startswith(PREFIX):
            continue
        spec = METRICS[name]
        labels = rf'\{{{spec.label}="([a-z_]+)"\}}' if spec.label else ""
        match = re.fullmatch(rf"{PREFIX}{name}{labels} ([0-9.eE+\-]+)", line)
        if match is None:
            raise ValueError("invalid browser metric shape")
        label = match[1] if spec.label else ""
        value = float(match[2] if spec.label else match[1])
        key = (name, label)
        if (
            label not in spec.values
            or key in samples
            or not math.isfinite(value)
            or value < 0
        ):
            raise ValueError("invalid browser metric value")
        if (
            name.endswith("_success") or name == "admission_available"
        ) and value not in (0, 1):
            raise ValueError("invalid browser metric boolean")
        samples[key] = value
    required = {
        (name, label)
        for name, spec in METRICS.items()
        if not spec.optional
        for label in spec.values
    }
    if not required <= samples.keys():
        raise ValueError("incomplete browser metrics snapshot")
    # Collection failures must omit samples instead of retaining old values.
    has_tasks = ("cgroup_tasks", "") in samples
    has_limit = ("cgroup_task_limit", "") in samples
    has_events = ("cgroup_limit_events_total", "") in samples
    if has_tasks != bool(samples[("cgroup_collection_success", "")]) or (
        has_limit and not has_tasks
    ):
        raise ValueError("inconsistent browser task collection")
    if has_events != bool(samples[("cgroup_limit_events_collection_success", "")]):
        raise ValueError("inconsistent browser events collection")
    return samples


def append_metrics(document: str, fragment: str) -> str:
    """Append a validated fragment while preserving a single terminal EOF."""
    return document.removesuffix("# EOF\n") + fragment + "# EOF\n"
