"""Bounded research-memory decisions, separate from raw lookup outcomes."""

from typing import Literal

from common.stage_metrics import inc_counter, observe_elapsed

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
        "groktocrawl_research_memory_rejections_total",
        "Research memory candidates rejected at each boundary",
        {"boundary": boundary, "reason": reason},
    )


def record_bypass(mode: Mode, reason: BypassReason) -> None:
    inc_counter(
        "groktocrawl_research_memory_bypass_total",
        "Agent research memory lookups deliberately bypassed",
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
        "groktocrawl_agent_execution_total",
        "Agent live pipeline starts or successfully published cached answers",
        labels,
    )
    if path == "replay" and started is not None:
        observe_elapsed(
            "groktocrawl_research_memory_replay_seconds",
            "Accepted replay preparation through publication, excluding lookup and refresh",
            {"mode": mode, "freshness": freshness},
            started,
        )
