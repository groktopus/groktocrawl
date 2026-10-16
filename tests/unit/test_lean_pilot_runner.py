"""Validate lean pilot input before network or output work."""

import importlib.util
import json
from pathlib import Path

import pytest


def module():
    path = Path("scripts/run-lean-research-pilot.py")
    spec = importlib.util.spec_from_file_location("run_lean_research_pilot", path)
    assert spec is not None and spec.loader is not None
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def test_exact_input_shape_is_admitted(tmp_path: Path) -> None:
    path = tmp_path / "input.json"
    path.write_text(
        json.dumps(
            {
                "objective": "What happened?",
                "questions": [{"question_id": "q1", "text": "What happened?"}],
                "sources": [{"snapshot_id": "s1", "text": "It happened."}],
            }
        )
    )

    objective, questions, sources = module()._input(path)

    assert objective == "What happened?"
    assert questions[0].question_id == "q1"
    assert sources[0].snapshot_id == "s1"


@pytest.mark.parametrize(
    "payload, message",
    [
        ({"objective": "Q", "questions": [], "sources": [], "extra": True}, "only"),
        ({"objective": "Q", "questions": "bad", "sources": []}, "types"),
        (
            {"objective": "Q", "questions": [{"question_id": "q"}], "sources": []},
            "questions",
        ),
        (
            {
                "objective": "Q",
                "questions": [],
                "sources": [{"snapshot_id": "s", "text": "T", "url": "https://x"}],
            },
            "sources",
        ),
    ],
)
def test_unknown_or_malformed_input_fails_closed(
    tmp_path: Path, payload: object, message: str
) -> None:
    path = tmp_path / "input.json"
    path.write_text(json.dumps(payload))

    with pytest.raises(ValueError, match=message):
        module()._input(path)
