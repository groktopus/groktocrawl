import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from scripts import run_w9_pilot_checkpoint as checkpoint


def state(path: Path, *, completed: int = 1) -> Path:
    path.write_text(
        json.dumps(
            {
                "completed_checkpoints": completed,
                "started_at": "2026-09-19T13:42:50Z",
                "next_checkpoint_not_before": "2026-09-22T02:19:40Z",
                "earliest_completion_at": "2026-09-26T02:19:40Z",
                "candidate_revision": "candidate",
                "runtime_revision": "runtime",
            }
        )
    )
    return path


def args(tmp_path: Path) -> argparse.Namespace:
    return argparse.Namespace(
        checkpoint=1,
        state_file=state(tmp_path / "state.json"),
        env_file=tmp_path / "candidate.env",
        compose_file=tmp_path / "compose.yml",
        base_url="http://127.0.0.1:18080",
        mcp_url="http://127.0.0.1:18002",
        mcp_host_header="localhost:18002",
        timeout=300,
        output_dir=tmp_path / "checkpoint-1",
    )


def test_refuses_checkpoint_before_elapsed_time(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="cannot count before"):
        checkpoint.execute(
            args(tmp_path), now=datetime(2026, 9, 22, 2, 19, 39, tzinfo=UTC)
        )
    assert not (tmp_path / "checkpoint-1").exists()


def test_refuses_out_of_order_checkpoint(tmp_path: Path) -> None:
    current = args(tmp_path)
    current.checkpoint = 2
    with pytest.raises(ValueError, match="invalid after 1"):
        checkpoint.execute(current, now=datetime(2026, 9, 26, 2, 19, 40, tzinfo=UTC))


def test_restart_checkpoint_uses_started_at_gate(tmp_path: Path) -> None:
    current = args(tmp_path)
    current.checkpoint = 0
    current.state_file = state(tmp_path / "restart-state.json", completed=0)
    with pytest.raises(RuntimeError, match="cannot count before"):
        checkpoint.execute(current, now=datetime(2026, 9, 19, 13, 42, 49, tzinfo=UTC))


def test_resolves_effective_key_and_publishes_atomically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    current = args(tmp_path)
    current.env_file.write_text('CANDIDATE_API_KEY="quoted-value"\n')
    current.compose_file.write_text("services: {}\n")
    override = tmp_path / "pilot.override.yml"
    override.write_text("services: {}\n")
    current.compose_file = [current.compose_file, override]
    calls: list[tuple[list[str], dict[str, str] | None]] = []

    def fake_run(command: list[str], *, env=None) -> str:
        calls.append((command, env))
        if command[-3:] == ["printenv", "API_KEY"] or command[-2:] == [
            "printenv",
            "API_KEY",
        ]:
            return "effective-value"
        if any(item.endswith("run_w9_compatibility.py") for item in command):
            output = Path(command[command.index("--output") + 1])
            output.write_text('{"trials":[{"outcome":"completed"}]}\n')
        if any(item.endswith("verify_experimental_candidate.py") for item in command):
            output = Path(command[command.index("--output") + 1])
            output.write_text('{"research":{"state":"completed"}}\n')
        return ""

    monkeypatch.setattr(checkpoint, "run", fake_run)
    monkeypatch.setattr(
        checkpoint,
        "resource_snapshot",
        lambda compose: {"observed_at": "now", "containers": []},
    )
    output = checkpoint.execute(
        current, now=datetime(2026, 9, 22, 2, 19, 40, tzinfo=UTC)
    )
    assert output == current.output_dir
    assert (output / "checkpoint.json").exists()
    assert calls[0][0][-2:] == ["-c", "import httpx, requests"]
    child_calls = [
        item
        for item in calls
        if any(value.endswith("run_w9_compatibility.py") for value in item[0])
    ]
    assert child_calls[0][1]["CANDIDATE_API_KEY"] == "effective-value"
    assert child_calls[0][1]["GROKTOCRAWL_API_KEY"] == "effective-value"
    assert "effective-value" not in " ".join(child_calls[0][0])
    compose_calls = [
        command for command, _ in calls if command[:2] == ["docker", "compose"]
    ]
    assert all(command.count("-f") == 2 for command in compose_calls)
    verify_calls = [
        command
        for command, _ in calls
        if "scripts/verify_experimental_candidate.py" in command
    ]
    assert verify_calls[0].count("--compose-file") == 2
