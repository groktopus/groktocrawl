"""Internal event contract for the research pipeline."""

from typing import Any, Literal, NotRequired, Required, TypedDict

ResearchEvent = TypedDict(
    "ResearchEvent",
    {
        "type": Required[
            Literal[
                "status",
                "research_plan",
                "research_pass",
                "sources_pending",
                "source_scraped",
                "sources",
                "token",
                "done",
                "error",
            ]
        ],
        "state": NotRequired[str],
        "strategy": NotRequired[str],
        "queries": NotRequired[list[str]],
        "reasoning": NotRequired[str],
        "pass": NotRequired[int],
        "total_passes": NotRequired[int],
        "sources": NotRequired[list[Any]],
        "url": NotRequired[str],
        "source": NotRequired[str],
        "chars": NotRequired[int],
        "content": NotRequired[str],
        "classification": NotRequired[str],
        "retry_after_seconds": NotRequired[float | None],
        "result": NotRequired[str],
        "source_details": NotRequired[list[dict[str, Any]]],
        "source_contents": NotRequired[dict[str, str]],
        "latency_ms": NotRequired[int],
    },
    total=False,
)
