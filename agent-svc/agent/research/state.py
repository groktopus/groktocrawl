"""Pure, compact projection of normalized research events.

The projection retains only facts present in the event contract. It excludes token,
result, and evidence bodies and does not invent gap, budget, or artifact identifiers.
"""

from collections.abc import Mapping
from typing import Any, TypedDict, cast

from .events import ResearchEvent


class CompactSource(TypedDict, total=False):
    """Whitelisted source metadata; never scraped content."""

    url: str
    title: str
    relevance: str
    source: str
    chars: int


class ResearchPlanState(TypedDict):
    """Planning facts emitted by a ``research_plan`` event."""

    strategy: str
    queries: list[str]
    reasoning: str


class ResearchState(TypedDict):
    """Replayable execution facts projected from ``ResearchEvent`` values."""

    objective: str
    status: str | None
    plan: ResearchPlanState | None
    pass_number: int | None
    total_passes: int | None
    pending_sources: list[CompactSource]
    source_metadata: list[CompactSource]
    source_urls: list[str]
    completed: bool
    error: str | None


def initial_research_state(objective: str) -> ResearchState:
    """Create an empty projection for a research objective."""
    return {
        "objective": objective,
        "status": None,
        "plan": None,
        "pass_number": None,
        "total_passes": None,
        "pending_sources": [],
        "source_metadata": [],
        "source_urls": [],
        "completed": False,
        "error": None,
    }


def apply_research_event(state: ResearchState, event: ResearchEvent) -> ResearchState:
    """Return a new state after applying one event; never mutate ``state``."""
    next_state: ResearchState = {
        "objective": state["objective"],
        "status": state["status"],
        "plan": _copy_plan(state["plan"]),
        "pass_number": state["pass_number"],
        "total_passes": state["total_passes"],
        "pending_sources": [
            cast(CompactSource, dict(source)) for source in state["pending_sources"]
        ],
        "source_metadata": [
            cast(CompactSource, dict(source)) for source in state["source_metadata"]
        ],
        "source_urls": list(state["source_urls"]),
        "completed": state["completed"],
        "error": state["error"],
    }

    event_type = event["type"]
    if event_type == "status":
        next_state["status"] = event.get("state")
    elif event_type == "research_plan":
        next_state["plan"] = {
            "strategy": event.get("strategy", ""),
            "queries": list(event.get("queries", [])),
            "reasoning": event.get("reasoning", ""),
        }
    elif event_type == "research_pass":
        next_state["pass_number"] = event.get("pass")
        next_state["total_passes"] = event.get("total_passes")
    elif event_type == "sources_pending":
        next_state["pending_sources"] = _compact_sources(event.get("sources", []))
    elif event_type == "source_scraped":
        source = _compact_source(event)
        pending: CompactSource = {}
        source_url = source.get("url")
        if source_url:
            for index, item in enumerate(next_state["pending_sources"]):
                if item.get("url") == source_url:
                    pending = next_state["pending_sources"].pop(index)
                    break
        combined = cast(CompactSource, {**pending, **source})
        next_state["source_metadata"] = _merge_sources(
            next_state["source_metadata"], [combined]
        )
        _add_source_urls(next_state["source_urls"], [source])
    elif event_type == "sources":
        sources = event.get("sources", [])
        _add_source_urls(next_state["source_urls"], _compact_sources(sources))
        _add_url_values(next_state["source_urls"], sources)
        next_state["pending_sources"] = []
    elif event_type == "done":
        source_details = _compact_sources(event.get("source_details", []))
        next_state["source_metadata"] = _merge_sources(
            next_state["source_metadata"], source_details
        )
        _add_source_urls(next_state["source_urls"], source_details)
        _add_url_values(next_state["source_urls"], event.get("sources", []))
        next_state["pending_sources"] = []
        next_state["status"] = "completed"
        next_state["completed"] = True
        next_state["error"] = None
    elif event_type == "error":
        next_state["status"] = "failed"
        next_state["completed"] = False
        next_state["error"] = event.get("content", "")

    return next_state


def _copy_plan(plan: ResearchPlanState | None) -> ResearchPlanState | None:
    if plan is None:
        return None
    return {
        "strategy": plan["strategy"],
        "queries": list(plan["queries"]),
        "reasoning": plan["reasoning"],
    }


def _compact_sources(sources: list[Any]) -> list[CompactSource]:
    return [
        compact
        for source in sources
        if isinstance(source, Mapping) and (compact := _compact_source(source))
    ]


def _compact_source(source: Mapping[str, Any]) -> CompactSource:
    compact = {
        field: source[field]
        for field in ("url", "title", "relevance", "source", "chars")
        if field in source
    }
    if "char_count" in source:
        compact["chars"] = source["char_count"]
    return cast(CompactSource, compact)


def _merge_sources(
    existing: list[CompactSource], additions: list[CompactSource]
) -> list[CompactSource]:
    merged: list[CompactSource] = [
        cast(CompactSource, dict(source)) for source in existing
    ]
    for source in additions:
        if not source:
            continue
        url = source.get("url")
        match = next((item for item in merged if url and item.get("url") == url), None)
        if match is None:
            merged.append(cast(CompactSource, dict(source)))
        else:
            match.update(source)
    return merged


def _add_source_urls(urls: list[str], sources: list[CompactSource]) -> None:
    _add_url_values(urls, [source.get("url") for source in sources])


def _add_url_values(urls: list[str], values: list[Any]) -> None:
    for value in values:
        if isinstance(value, str) and value and value not in urls:
            urls.append(value)
