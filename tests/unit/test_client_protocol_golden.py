"""Golden traces prove parity across the experimental client projections."""

import json
from pathlib import Path
from typing import Any

import pytest
from agent.experimental.client_protocol import (
    ProtocolEvent,
    client_projections,
    replay_after,
    validate_trace,
)

GOLDEN_DIR = Path(__file__).parents[2] / "docs/experiments/client-protocol/golden"


def _load_fixture(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("fixture_path", sorted(GOLDEN_DIR.glob("*.json")))
def test_golden_trace_is_valid_and_projects_identically(fixture_path: Path) -> None:
    fixture = _load_fixture(fixture_path)
    trace = validate_trace(
        ProtocolEvent.model_validate(event) for event in fixture["events"]
    )

    projections = client_projections(trace)
    assert projections["status"] == projections["cli"] == projections["mcp"]
    assert projections["status"]["run_id"] == trace[0].run_id
    assert [event.sequence for event in replay_after(trace, fixture["replay_after"])] == fixture[
        "replay_sequences"
    ]


def test_golden_fixtures_cover_each_terminal_outcome() -> None:
    outcomes = {
        ProtocolEvent.model_validate(event).event
        for path in GOLDEN_DIR.glob("*.json")
        for event in _load_fixture(path)["events"]
        if ProtocolEvent.model_validate(event).terminal
    }
    assert outcomes == {"done", "error", "cancelled"}
