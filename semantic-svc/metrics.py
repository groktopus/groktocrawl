"""In-memory metrics collector with OpenMetrics text export.

Provides Counter, Histogram, and Gauge primitives backed by plain Python
data structures. Thread-safe via ``threading.Lock``. Generates OpenMetrics
text format for Prometheus consumption — no external dependencies.

Duplicated from agent-svc/agent/metrics.py per ADR-0029 (service-level metrics
follow the pattern established by ADR-0018).

Usage::

    from metrics import METRICS

    # Counter — how many things happened
    METRICS.counter("requests_total", "Total requests", ["endpoint"]).inc({"endpoint": "embed"})

    # Histogram — latency distribution
    METRICS.histogram("latency_seconds", "Request latency", ["endpoint"]).observe({"endpoint": "search_vector"}, 0.042)

    # Gauge — current value
    METRICS.gauge("docs_total", "Current document count").set(250000)

    # Export
    print(METRICS.generate_openmetrics())
"""

import threading
from collections import defaultdict

DEFAULT_BUCKETS = [
    0.005,
    0.01,
    0.025,
    0.05,
    0.075,
    0.1,
    0.25,
    0.5,
    0.75,
    1.0,
    2.5,
    5.0,
    7.5,
    10.0,
    30.0,
    60.0,
]


class _SafeCounter:
    """Thread-safe counter with optional label dimensions."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: dict[tuple, float] = defaultdict(float)

    def inc(self, labels: dict[str, str] | None = None, value: float = 1.0) -> None:
        key = tuple(sorted(labels.items())) if labels else ()
        with self._lock:
            self._data[key] += value

    def _collect(self) -> list[tuple[tuple, float]]:
        with self._lock:
            return list(self._data.items())


class _SafeHistogram:
    """Thread-safe histogram with configurable buckets."""

    def __init__(self, buckets: list[float]) -> None:
        self._buckets = sorted(buckets)
        self._lock = threading.Lock()
        self._counts: dict[float, defaultdict[tuple, int]] = {
            b: defaultdict(int) for b in self._buckets
        }
        self._sum: defaultdict[tuple, float] = defaultdict(float)
        self._total_counts: defaultdict[tuple, int] = defaultdict(int)

    def observe(self, labels: dict[str, str] | None, value: float) -> None:
        key = tuple(sorted(labels.items())) if labels else ()
        with self._lock:
            self._total_counts[key] += 1
            self._sum[key] += value
            for b in self._buckets:
                if value <= b:
                    self._counts[b][key] += 1

    def _get_buckets(self) -> list[float]:
        return self._buckets


class _SafeGauge:
    """Thread-safe gauge."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: dict[tuple, float] = defaultdict(float)

    def set(self, labels: dict[str, str] | None = None, value: float = 0.0) -> None:
        key = tuple(sorted(labels.items())) if labels else ()
        with self._lock:
            self._data[key] = value

    def inc(self, labels: dict[str, str] | None = None, value: float = 1.0) -> None:
        key = tuple(sorted(labels.items())) if labels else ()
        with self._lock:
            self._data[key] += value

    def dec(self, labels: dict[str, str] | None = None, value: float = 1.0) -> None:
        key = tuple(sorted(labels.items())) if labels else ()
        with self._lock:
            self._data[key] -= value

    def _collect(self) -> list[tuple[tuple, float]]:
        with self._lock:
            return list(self._data.items())


class MetricsCollector:
    """Central metrics registry with OpenMetrics text export.

    Typical usage is the module-level singleton ``METRICS``.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[str, tuple[_SafeCounter, str, list[str]]] = {}
        self._histograms: dict[
            str, tuple[_SafeHistogram, str, list[str], list[float]]
        ] = {}
        self._gauges: dict[str, tuple[_SafeGauge, str, list[str]]] = {}

    def counter(
        self, name: str, help_text: str, label_names: list[str] | None = None
    ) -> _SafeCounter:
        """Get or create a counter metric."""
        with self._lock:
            if name not in self._counters:
                self._counters[name] = (_SafeCounter(), help_text, label_names or [])
            return self._counters[name][0]

    def histogram(
        self,
        name: str,
        help_text: str,
        label_names: list[str] | None = None,
        buckets: list[float] | None = None,
    ) -> _SafeHistogram:
        """Get or create a histogram metric."""
        with self._lock:
            if name not in self._histograms:
                b = buckets or DEFAULT_BUCKETS
                self._histograms[name] = (
                    _SafeHistogram(b),
                    help_text,
                    label_names or [],
                    b,
                )
            return self._histograms[name][0]

    def gauge(
        self, name: str, help_text: str, label_names: list[str] | None = None
    ) -> _SafeGauge:
        """Get or create a gauge metric."""
        with self._lock:
            if name not in self._gauges:
                self._gauges[name] = (_SafeGauge(), help_text, label_names or [])
            return self._gauges[name][0]

    def generate_openmetrics(self) -> str:
        """Generate the full OpenMetrics text representation.

        Returns a string suitable for a ``/metrics`` HTTP response with
        ``Content-Type: application/openmetrics-text; version=1.0.0``.
        """
        lines: list[str] = []
        lines.append("# HELP groktocrawl_info GroktoCrawl metrics")
        lines.append("# TYPE groktocrawl_info info")
        lines.append('groktocrawl_info{version="0.6.0"} 1')
        lines.append("")

        # Counters
        for name, (safe_counter, help_text, label_names) in self._counters.items():
            lines.append(f"# HELP {name} {help_text}")
            lines.append(f"# TYPE {name} counter")
            for key, value in safe_counter._collect():
                lbls = _format_labels_no_braces(key)
                if lbls:
                    lines.append(f"{name}{{{lbls}}} {value}")
                else:
                    lines.append(f"{name} {value}")
            lines.append("")

        # Histograms — count, sum, per-bucket with le= label
        for name, (
            safe_hist,
            help_text,
            label_names,
            buckets,
        ) in self._histograms.items():
            lines.append(f"# HELP {name} {help_text}")
            lines.append(f"# TYPE {name} histogram")
            for key, total_count in sorted(safe_hist._total_counts.items()):
                lbls = _format_labels_no_braces(key)
                if lbls:
                    lines.append(f"{name}_count{{{lbls}}} {total_count}")
                else:
                    lines.append(f"{name}_count {total_count}")
            for key, total_sum in sorted(safe_hist._sum.items()):
                lbls = _format_labels_no_braces(key)
                if lbls:
                    lines.append(f"{name}_sum{{{lbls}}} {total_sum}")
                else:
                    lines.append(f"{name}_sum {total_sum}")
            for b in safe_hist._get_buckets():
                for key, count in sorted(safe_hist._counts[b].items()):
                    parts = _format_labels_no_braces(key)
                    le_part = f'le="{b}"'
                    bucket_labels = f"{parts},{le_part}" if parts else le_part
                    lines.append(f"{name}_bucket{{{bucket_labels}}} {count}")
            lines.append("")

        # Gauges
        for name, (safe_gauge, help_text, label_names) in self._gauges.items():
            lines.append(f"# HELP {name} {help_text}")
            lines.append(f"# TYPE {name} gauge")
            for key, value in safe_gauge._collect():
                lbls = _format_labels_no_braces(key)
                if lbls:
                    lines.append(f"{name}{{{lbls}}} {value}")
                else:
                    lines.append(f"{name} {value}")
            lines.append("")

        lines.append("# EOF")
        return "\n".join(line for line in lines if line) + "\n"


def _format_labels_no_braces(key: tuple) -> str:
    """Format a sorted label key tuple as comma-separated ``k="v"`` pairs.

    Returns empty string for unlabeled metrics. No surrounding braces.
    """
    if not key:
        return ""
    parts = []
    for k, v in key:
        escaped_v = v.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        parts.append(f'{k}="{escaped_v}"')
    return ",".join(parts)


# Module-level singleton
METRICS = MetricsCollector()
