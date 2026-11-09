"""Bounded research-memory decisions, separate from raw lookup outcomes."""

from typing import Literal

from common.metrics import METRICS
from common.stage_metrics import inc_counter, observe_elapsed

_REJECTIONS = (
    "groktocrawl_research_memory_rejections_total",
    "Research memory candidates rejected at each boundary",
)
_BYPASSES = (
    "groktocrawl_research_memory_bypass_total",
    "Agent research memory lookups deliberately bypassed",
)
_EXECUTIONS = (
    "groktocrawl_agent_execution_total",
    "Agent live pipeline starts or successfully published cached answers",
)

Mode = Literal["job", "stream"]
BypassReason = Literal["force_fresh", "source_content", "unavailable"]
Reason = Literal[
    "similarity",
    "missing_id",
    "missing_artifact",
    "invalid_artifact",
    "compatibility",
    "freshness",
    "max_age",
    "stale_policy",
    "citation",
    "error",
    "publication",
]


def record_rejection(
    boundary: Literal["query", "job", "stream"], reason: Reason
) -> None:
    inc_counter(
        *_REJECTIONS,
        {"boundary": boundary, "reason": reason},
    )


def record_bypass(mode: Mode, reason: BypassReason) -> None:
    inc_counter(
        *_BYPASSES,
        {"mode": mode, "reason": reason},
    )


def record_execution(
    mode: Mode,
    path: Literal["live", "replay"],
    freshness: str = "none",
    *,
    started: float | None = None,
) -> None:
    # Never use artifact-supplied arbitrary strings as metric labels.
    freshness = freshness if freshness in {"fresh", "aging", "stale"} else "none"
    labels = {"mode": mode, "path": path, "freshness": freshness}
    inc_counter(
        *_EXECUTIONS,
        labels,
    )
    if path == "replay" and started is not None:
        observe_elapsed(
            "groktocrawl_research_memory_replay_seconds",
            "Accepted replay preparation through publication, excluding lookup and refresh",
            {"mode": mode, "freshness": freshness},
            started,
        )


def initialize_research_cache_metrics() -> None:
    """Seed valid bounded counter series without inventing latency observations.

    Zero increments preserve events already recorded by another app instance.
    Prometheus still needs a scrape before traffic to observe the first increase.
    """
    # Import at app initialization, after ResearchMemory has loaded this module.
    from .research_memory import _MEMORY_LOOKUP_TOTAL, _MEMORY_LOOKUP_TOTAL_HELP

    def seed(spec: tuple[str, str], labels: dict[str, str]) -> None:
        METRICS.counter(*spec, sorted(labels)).inc(labels, 0)

    for outcome in ("fresh", "aging", "stale", "miss", "error", "cancelled"):
        seed((_MEMORY_LOOKUP_TOTAL, _MEMORY_LOOKUP_TOTAL_HELP), {"outcome": outcome})
    for boundary, reasons in (
        (
            "query",
            (
                "similarity",
                "missing_id",
                "missing_artifact",
                "invalid_artifact",
                "compatibility",
                "freshness",
                "max_age",
            ),
        ),
        ("job", ("stale_policy", "citation", "error", "publication")),
        ("stream", ("stale_policy", "citation")),
    ):
        for reason in reasons:
            seed(_REJECTIONS, {"boundary": boundary, "reason": reason})
    for mode in ("job", "stream"):
        for reason in ("force_fresh", "source_content", "unavailable"):
            seed(_BYPASSES, {"mode": mode, "reason": reason})
        seed(_EXECUTIONS, {"mode": mode, "path": "live", "freshness": "none"})
        for freshness in ("fresh", "aging", "stale", "none"):
            seed(_EXECUTIONS, {"mode": mode, "path": "replay", "freshness": freshness})
