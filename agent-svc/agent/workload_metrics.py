"""Workload-class capacity signals for background job processing.

Centralises active-work, cancellation, and completion gauge/counter
registration so the generic worker scaffolding and the crawl-specific
path share one definition instead of duplicating metric registration
(which the jscpd gate rejects).
"""

from __future__ import annotations

from .metrics import METRICS

_ACTIVE_HELP = "Number of currently in-flight jobs by workload class"
_CANCELLED_HELP = "Total jobs cancelled by workload class"


def initialize_job_metrics() -> None:
    """Expose bounded legacy job counters before their first observed event.

    Zero increments preserve existing values if multiple apps share a collector.
    Rate queries still need a scrape before the first event; historical missing
    samples cannot be reconstructed by initialization.
    """
    for name, description in (
        ("jobs_submitted_total", "Total jobs submitted"),
        ("jobs_completed_total", "Total completed jobs"),
        ("jobs_failed_total", "Total failed jobs"),
    ):
        counter = METRICS.counter(name, description, ["type"])
        for kind in (
            "agent",
            "crawl",
            "batch_scrape",
            "extract",
            "llmstxt",
            "plan_execute",
        ):
            counter.inc({"type": kind}, 0)


def active_jobs_gauge():
    return METRICS.gauge("groktocrawl_active_jobs", _ACTIVE_HELP, ["type"])


def cancelled_jobs_counter():
    return METRICS.counter(
        "groktocrawl_jobs_cancelled_total", _CANCELLED_HELP, ["type"]
    )


def record_job_start(job_type: str) -> None:
    active_jobs_gauge().inc({"type": job_type})


def record_job_end(job_type: str) -> None:
    active_jobs_gauge().dec({"type": job_type})


def record_job_cancelled(job_type: str) -> None:
    cancelled_jobs_counter().inc({"type": job_type})
