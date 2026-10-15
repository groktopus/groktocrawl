#!/usr/bin/env python3
"""Verify that one frozen W9 window satisfies every mechanical closeout gate."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp must include a timezone: {value}")
    return parsed.astimezone(UTC)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return value


def verify(
    state_file: Path,
    checkpoint_dirs: list[Path],
    *,
    expected_model: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    state = load_json(state_file)
    observed_at = (now or datetime.now(UTC)).astimezone(UTC)
    errors: list[str] = []

    required_checkpoints = int(state.get("required_checkpoints", 0))
    required_requests = int(state.get("required_successful_requests", 0))
    completed_checkpoints = int(state.get("completed_checkpoints", 0))
    state_requests = int(state.get("successful_requests", 0))
    candidate_revision = str(state.get("candidate_revision", ""))
    runtime_revision = str(state.get("runtime_revision", ""))
    earliest_completion = parse_time(str(state["earliest_completion_at"]))
    next_checkpoint = parse_time(str(state["next_checkpoint_not_before"]))
    started_at = parse_time(str(state["started_at"]))

    if state.get("schema_version") != "w9-operational-pilot-state/2":
        errors.append("state schema is not w9-operational-pilot-state/2")
    if state.get("status") != "complete":
        errors.append("state status is not complete")
    if candidate_revision != runtime_revision or not candidate_revision:
        errors.append("state candidate and runtime revisions do not match")
    if observed_at < earliest_completion:
        errors.append("seven-day completion gate has not elapsed")
    if completed_checkpoints != required_checkpoints:
        errors.append("state does not record all required checkpoints")
    if state_requests < required_requests:
        errors.append("state does not record the required successful operations")
    if len(checkpoint_dirs) != required_checkpoints:
        errors.append(
            f"expected {required_checkpoints} checkpoint packets, got "
            f"{len(checkpoint_dirs)}"
        )

    checkpoints: list[dict[str, Any]] = []
    for directory in checkpoint_dirs:
        checkpoint_file = directory / "checkpoint.json"
        if not checkpoint_file.is_file():
            errors.append(f"missing checkpoint receipt: {checkpoint_file}")
            continue
        checkpoint = load_json(checkpoint_file)
        number = int(checkpoint.get("checkpoint", -1))
        operation_count = int(checkpoint.get("successful_operations", 0))
        completed_at = parse_time(str(checkpoint["completed_at"]))

        if checkpoint.get("schema_version") != "w9-operational-checkpoint/1":
            errors.append(f"checkpoint {number} has an unsupported schema")
        if checkpoint.get("candidate_revision") != candidate_revision:
            errors.append(f"checkpoint {number} candidate revision differs from state")
        if checkpoint.get("runtime_revision") != runtime_revision:
            errors.append(f"checkpoint {number} runtime revision differs from state")

        due = {0: started_at, 1: next_checkpoint, 2: earliest_completion}.get(number)
        if due is None:
            errors.append(f"unexpected checkpoint number {number}")
        elif completed_at < due:
            errors.append(f"checkpoint {number} completed before its elapsed-time gate")
        try:
            recorded_not_before = parse_time(str(checkpoint["not_before"]))
        except (KeyError, ValueError):
            errors.append(f"checkpoint {number} has no valid not_before timestamp")
        else:
            if due is not None and recorded_not_before != due:
                errors.append(f"checkpoint {number} records the wrong time gate")

        receipts = checkpoint.get("receipts", {})
        if not isinstance(receipts, dict) or not receipts:
            errors.append(f"checkpoint {number} has no receipt digests")
            receipts = {}
        for name, expected_digest in receipts.items():
            receipt = directory / str(name)
            if not receipt.is_file():
                errors.append(f"checkpoint {number} is missing receipt {name}")
            elif digest(receipt) != expected_digest:
                errors.append(f"checkpoint {number} receipt digest differs for {name}")

        compatibility_file = directory / "compatibility.json"
        if compatibility_file.is_file():
            compatibility = load_json(compatibility_file)
            trials = compatibility.get("trials", [])
            if not trials or any(
                trial.get("outcome") != "completed" for trial in trials
            ):
                errors.append(
                    f"checkpoint {number} compatibility journey did not complete"
                )
        else:
            errors.append(f"checkpoint {number} has no compatibility receipt")

        research_file = directory / "research.json"
        if research_file.is_file():
            research = load_json(research_file)
            if research.get("research", {}).get("state") != "completed":
                errors.append(f"checkpoint {number} research journey did not complete")
            runtime = research.get("runtime", {})
            if runtime.get("revision") != runtime_revision:
                errors.append(f"checkpoint {number} observed a different live revision")
            if runtime.get("model") != expected_model:
                errors.append(f"checkpoint {number} observed a different model alias")
        else:
            errors.append(f"checkpoint {number} has no research receipt")

        checkpoints.append(
            {
                "checkpoint": number,
                "completed_at": completed_at.isoformat(),
                "successful_operations": operation_count,
                "packet": str(directory),
            }
        )

    numbers = [item["checkpoint"] for item in checkpoints]
    if sorted(numbers) != list(range(required_checkpoints)):
        errors.append("checkpoint packets are incomplete, duplicated, or out of order")
    packet_requests = sum(item["successful_operations"] for item in checkpoints)
    if packet_requests != state_requests:
        errors.append(
            "state successful-operation count differs from checkpoint packets"
        )
    if packet_requests < required_requests:
        errors.append(
            "checkpoint packets do not prove the required successful operations"
        )

    return {
        "schema_version": "w9-closeout-verification/1",
        "ready": not errors,
        "observed_at": observed_at.isoformat(),
        "candidate_revision": candidate_revision,
        "expected_model": expected_model,
        "successful_operations": packet_requests,
        "required_successful_operations": required_requests,
        "completed_checkpoints": len(checkpoints),
        "required_checkpoints": required_checkpoints,
        "checkpoints": checkpoints,
        "errors": errors,
    }


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument(
        "--state-file",
        type=Path,
        default=Path(
            "docs/experiments/evidence/replacement-rehearsal/w9-pilot-state.json"
        ),
    )
    value.add_argument("--checkpoint-dir", type=Path, action="append", required=True)
    value.add_argument("--expected-model", default="free")
    value.add_argument(
        "--now", help="UTC-aware ISO timestamp; defaults to current time"
    )
    value.add_argument("--output", type=Path)
    return value


def main() -> int:
    args = parser().parse_args()
    report = verify(
        args.state_file,
        args.checkpoint_dir,
        expected_model=args.expected_model,
        now=parse_time(args.now) if args.now else None,
    )
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    return 0 if report["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
