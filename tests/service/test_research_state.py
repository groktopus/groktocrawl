"""Tests for the compact research-event state projection."""

from agent.research.events import ResearchEvent
from agent.research.state import apply_research_event, initial_research_state


def test_research_events_build_compact_immutable_state() -> None:
    initial = initial_research_state("Compare renewable-energy storage options")
    state = initial
    events: list[ResearchEvent] = [
        {"type": "status", "state": "planning"},
        {
            "type": "research_plan",
            "strategy": "compare primary sources",
            "queries": ["battery storage", "pumped hydro"],
            "reasoning": "cover leading approaches",
        },
        {"type": "research_pass", "pass": 1, "total_passes": 2},
        {
            "type": "sources_pending",
            "sources": [
                {
                    "url": "https://example.com/batteries",
                    "title": "Battery report",
                    "relevance": "cost data",
                    "body": "must not persist",
                }
            ],
        },
        {
            "type": "source_scraped",
            "url": "https://example.com/batteries",
            "source": "web",
            "chars": 2400,
            "content": "must not persist",
        },
        {"type": "sources", "sources": ["https://example.com/hydro"]},
        {
            "type": "token",
            "content": "streamed answer text must not persist",
        },
        {
            "type": "done",
            "result": "final answer text must not persist",
            "sources": ["https://example.com/batteries"],
            "source_details": [
                {
                    "url": "https://example.com/hydro",
                    "title": "Hydro report",
                    "source": "web",
                    "char_count": 1800,
                    "markdown": "evidence body must not persist",
                }
            ],
            "latency_ms": 42,
        },
    ]

    for event in events[:5]:
        state = apply_research_event(state, event)

    assert state["pending_sources"] == []

    for event in events[5:]:
        state = apply_research_event(state, event)

    assert initial == initial_research_state("Compare renewable-energy storage options")
    assert state == {
        "objective": "Compare renewable-energy storage options",
        "status": "completed",
        "plan": {
            "strategy": "compare primary sources",
            "queries": ["battery storage", "pumped hydro"],
            "reasoning": "cover leading approaches",
        },
        "pass_number": 1,
        "total_passes": 2,
        "pending_sources": [],
        "source_metadata": [
            {
                "url": "https://example.com/batteries",
                "title": "Battery report",
                "relevance": "cost data",
                "source": "web",
                "chars": 2400,
            },
            {
                "url": "https://example.com/hydro",
                "title": "Hydro report",
                "source": "web",
                "chars": 1800,
            },
        ],
        "source_urls": [
            "https://example.com/batteries",
            "https://example.com/hydro",
        ],
        "completed": True,
        "error": None,
    }
    assert "streamed answer text must not persist" not in repr(state)
    assert "final answer text must not persist" not in repr(state)
    assert "evidence body must not persist" not in repr(state)
