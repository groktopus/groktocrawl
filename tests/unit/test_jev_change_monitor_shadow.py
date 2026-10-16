from __future__ import annotations

import importlib.util
import json
import os
import shutil
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

SCRIPT = (
    Path(__file__).resolve().parents[2] / "scripts/run_jev_change_monitor_shadow.py"
)
SPEC = importlib.util.spec_from_file_location("jev_change_runner", SCRIPT)
assert SPEC and SPEC.loader
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


def test_frozen_packet_validates_without_provider_traffic():
    manifest, requests = runner.validate_packet()
    assert manifest["provider_calls_made"] == 0
    assert len(requests) == 11
    assert [case["split"] for case in requests].count("calibration") == 4
    assert [case["split"] for case in requests].count("validation") == 7
    for case in requests:
        state = case["request"]["state"]
        assert set(state) == {"intent", "before_excerpts", "after_excerpts"}
        assert "label" not in json.dumps(case["request"]).lower()


def test_excerpt_spans_match_the_content_addressed_public_pages():
    _, requests = runner.validate_packet()
    _, redactions = runner._validate_publication_amendment(
        json.loads((runner.PACKET / "freeze-manifest.json").read_text())["files"]
    )
    runner._validate_excerpt_source_spans(requests, redactions)


def test_noul_response_requires_exact_model_and_finite_probability():
    assert (
        runner._validate_response(
            {"model": runner.MODEL, "answers": {"q0": {"type": "noul", "noul": 0.5}}}
        )["q0_noul"]
        == 0.5
    )
    for bad in (True, float("nan"), float("inf"), -0.1, 1.1, "0.6"):
        try:
            runner._validate_response(
                {
                    "model": runner.MODEL,
                    "answers": {"q0": {"type": "noul", "noul": bad}},
                }
            )
        except ValueError:
            pass
        else:
            raise AssertionError(f"accepted invalid Noul value: {bad!r}")


def make_preoutcome_packet(tmp_path: Path) -> Path:
    packet = tmp_path / "packet"
    shutil.copytree(runner.PACKET, packet)
    for name in ("outcome.json", "casewise-outcome-ledger.json"):
        (packet / name).unlink(missing_ok=True)
    return packet


def test_existing_receipt_directory_fails_before_any_proxy_call(tmp_path):
    journal = tmp_path / "journal.jsonl"
    receipts = tmp_path / "receipts"
    receipts.mkdir()
    args = SimpleNamespace(
        execute=True,
        expected_freeze_sha256=runner.FREEZE_SHA256,
        proxy_command=["fake"],
        journal=str(journal),
        receipts_dir=str(receipts),
        timeout=2,
    )
    packet = make_preoutcome_packet(tmp_path)
    with (
        patch.object(runner, "PACKET", packet),
        patch.object(runner.subprocess, "run") as call,
    ):
        try:
            runner.run(args)
        except FileExistsError:
            pass
        else:
            raise AssertionError("existing receipt directory should block run")
        call.assert_not_called()
    assert not journal.exists()


def test_fake_run_journals_attempt_before_sequential_proxy_and_private_receipts(
    tmp_path,
):
    journal_parent = tmp_path / "existing-parent"
    journal_parent.mkdir()
    os.chmod(journal_parent, 0o755)
    parent_mode_before = stat_mode(journal_parent)
    journal = journal_parent / "run.jsonl"
    receipts = tmp_path / "receipts"
    calls: list[int] = []

    def fake_run(_argv, **kwargs):
        assert journal.exists()
        rows = journal.read_text().splitlines()
        assert json.loads(rows[-1])["event"] == "attempted"
        assert kwargs["capture_output"] is True
        assert json.loads(kwargs["input"])["model"] == runner.MODEL
        calls.append(len(calls) + 1)
        response = {
            "model": runner.MODEL,
            "answers": {"q0": {"type": "noul", "noul": 0.7}},
        }
        return SimpleNamespace(
            returncode=0, stdout=json.dumps(response).encode(), stderr=b""
        )

    args = SimpleNamespace(
        execute=True,
        expected_freeze_sha256=runner.FREEZE_SHA256,
        proxy_command=["fake"],
        journal=str(journal),
        receipts_dir=str(receipts),
        timeout=2,
    )
    packet = make_preoutcome_packet(tmp_path)
    with (
        patch.object(runner, "PACKET", packet),
        patch.object(runner.subprocess, "run", side_effect=fake_run),
    ):
        assert runner.run(args) == 0
    assert calls == list(range(1, 12))
    rows = [json.loads(line) for line in journal.read_text().splitlines()]
    assert len(rows) == 22
    assert [r["event"] for r in rows].count("attempted") == 11
    assert [r["event"] for r in rows].count("outcome") == 11
    assert all(r["status"] == "ok" for r in rows if r["event"] == "outcome")
    assert stat_mode(journal) == 0o600
    assert stat_mode(journal_parent) == parent_mode_before == 0o755
    assert stat_mode(receipts) == 0o700
    assert all(stat_mode(path) == 0o600 for path in receipts.iterdir())


def stat_mode(path: Path) -> int:
    return os.stat(path).st_mode & 0o777


def test_existing_journal_file_fails_before_any_proxy_call(tmp_path):
    journal = tmp_path / "journal.jsonl"
    journal.write_text("existing\n")
    receipts = tmp_path / "receipts"
    args = SimpleNamespace(
        execute=True,
        expected_freeze_sha256=runner.FREEZE_SHA256,
        proxy_command=["fake"],
        journal=str(journal),
        receipts_dir=str(receipts),
        timeout=2,
    )
    packet = make_preoutcome_packet(tmp_path)
    with (
        patch.object(runner, "PACKET", packet),
        patch.object(runner.subprocess, "run") as call,
    ):
        try:
            runner.run(args)
        except FileExistsError:
            pass
        else:
            raise AssertionError("existing journal should block run")
        call.assert_not_called()
    assert not receipts.exists()


def test_recorded_results_refuse_any_additional_provider_attempt(tmp_path):
    args = SimpleNamespace(
        execute=True,
        expected_freeze_sha256=runner.FREEZE_SHA256,
        proxy_command=["fake"],
        journal=str(tmp_path / "new-journal.jsonl"),
        receipts_dir=str(tmp_path / "new-receipts"),
        timeout=2,
    )
    with patch.object(runner.subprocess, "run") as call:
        try:
            runner.run(args)
        except ValueError as exc:
            assert "refusing repeat provider calls" in str(exc)
        else:
            raise AssertionError("recorded study must block duplicate provider calls")
        call.assert_not_called()
