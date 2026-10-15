import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from scripts import verify_w9_closeout as closeout

REVISION = "a" * 40


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value) + "\n", encoding="utf-8")


def packet(root: Path, number: int, completed_at: str, due: str) -> Path:
    directory = root / f"checkpoint-{number}"
    directory.mkdir()
    write_json(directory / "compatibility.json", {"trials": [{"outcome": "completed"}]})
    write_json(
        directory / "research.json",
        {
            "research": {"state": "completed"},
            "runtime": {"model": "general", "revision": REVISION},
        },
    )
    write_json(directory / "resources.json", {"containers": []})
    receipts = {
        name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
        for name in ("compatibility.json", "research.json", "resources.json")
    }
    write_json(
        directory / "checkpoint.json",
        {
            "schema_version": "w9-operational-checkpoint/1",
            "checkpoint": number,
            "not_before": due,
            "completed_at": completed_at,
            "candidate_revision": REVISION,
            "runtime_revision": REVISION,
            "successful_operations": 12,
            "receipts": receipts,
        },
    )
    return directory


def state(path: Path, *, status: str = "complete", requests: int = 36) -> Path:
    write_json(
        path,
        {
            "schema_version": "w9-operational-pilot-state/2",
            "status": status,
            "candidate_revision": REVISION,
            "runtime_revision": REVISION,
            "started_at": "2026-09-22T16:47:25.742584Z",
            "next_checkpoint_not_before": "2026-09-25T16:47:25.742584Z",
            "earliest_completion_at": "2026-09-29T16:47:25.742584Z",
            "required_checkpoints": 3,
            "completed_checkpoints": 3,
            "required_successful_requests": 30,
            "successful_requests": requests,
        },
    )
    return path


def complete_packets(tmp_path: Path) -> list[Path]:
    return [
        packet(tmp_path, 0, "2026-09-22T16:49:57Z", "2026-09-22T16:47:25.742584Z"),
        packet(tmp_path, 1, "2026-09-25T16:50:00Z", "2026-09-25T16:47:25.742584Z"),
        packet(tmp_path, 2, "2026-09-29T16:50:00Z", "2026-09-29T16:47:25.742584Z"),
    ]


def test_accepts_complete_single_revision_window(tmp_path: Path) -> None:
    report = closeout.verify(
        state(tmp_path / "state.json"),
        complete_packets(tmp_path),
        expected_model="general",
        now=datetime(2026, 9, 29, 17, tzinfo=UTC),
    )

    assert report["ready"] is True
    assert report["successful_operations"] == 36
    assert report["errors"] == []


def test_rejects_incomplete_state_before_final_gate(tmp_path: Path) -> None:
    report = closeout.verify(
        state(tmp_path / "state.json", status="in_progress", requests=12),
        complete_packets(tmp_path)[:1],
        expected_model="general",
        now=datetime(2026, 9, 25, 17, tzinfo=UTC),
    )

    assert report["ready"] is False
    assert "state status is not complete" in report["errors"]
    assert "seven-day completion gate has not elapsed" in report["errors"]
    assert "expected 3 checkpoint packets, got 1" in report["errors"]


def test_rejects_mutated_receipt_and_revision(tmp_path: Path) -> None:
    packets = complete_packets(tmp_path)
    (packets[1] / "research.json").write_text("{}\n", encoding="utf-8")
    checkpoint = json.loads((packets[2] / "checkpoint.json").read_text())
    checkpoint["runtime_revision"] = "b" * 40
    write_json(packets[2] / "checkpoint.json", checkpoint)

    report = closeout.verify(
        state(tmp_path / "state.json"),
        packets,
        expected_model="general",
        now=datetime(2026, 9, 29, 17, tzinfo=UTC),
    )

    assert report["ready"] is False
    assert "checkpoint 1 receipt digest differs for research.json" in report["errors"]
    assert "checkpoint 1 research journey did not complete" in report["errors"]
    assert "checkpoint 2 runtime revision differs from state" in report["errors"]
