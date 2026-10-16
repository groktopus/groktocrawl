#!/usr/bin/env python3
"""Run one time-gated W9 pilot checkpoint and publish its receipts atomically."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("checkpoint time must include a timezone")
    return parsed.astimezone(UTC)


def run(command: list[str], *, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    return result.stdout.strip()


def compose_files(value: Path | list[Path] | None) -> list[Path]:
    if value is None:
        return [Path("compose.experimental-candidate.yml")]
    return [value] if isinstance(value, Path) else value


def compose_command(env_file: Path, files: list[Path]) -> list[str]:
    command = ["docker", "compose", "--env-file", str(env_file)]
    for file in files:
        command.extend(("-f", str(file)))
    return command


def input_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def resource_snapshot(compose: list[str]) -> dict[str, Any]:
    container_ids = run([*compose, "ps", "-q"]).splitlines()
    if not container_ids:
        raise RuntimeError("candidate Compose project has no running containers")
    raw = run(
        [
            "docker",
            "stats",
            "--no-stream",
            "--format",
            "{{json .}}",
            *container_ids,
        ]
    )
    return {
        "observed_at": datetime.now(UTC).isoformat(),
        "containers": [json.loads(line) for line in raw.splitlines() if line],
    }


def checkpoint_due(state: dict[str, Any], checkpoint: int) -> datetime:
    completed = int(state["completed_checkpoints"])
    if checkpoint != completed:
        raise ValueError(
            f"checkpoint {checkpoint} is invalid after {completed} completed checkpoints"
        )
    if checkpoint == 0:
        key = "started_at"
    elif checkpoint == 1:
        key = "next_checkpoint_not_before"
    else:
        key = "earliest_completion_at"
    return parse_time(str(state[key]))


def write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def execute(args: argparse.Namespace, *, now: datetime | None = None) -> Path:
    state = json.loads(args.state_file.read_bytes())
    due = checkpoint_due(state, args.checkpoint)
    observed_now = (now or datetime.now(UTC)).astimezone(UTC)
    if observed_now < due:
        raise RuntimeError(
            f"checkpoint {args.checkpoint} cannot count before {due.isoformat()}"
        )
    if args.output_dir.exists():
        raise FileExistsError(f"checkpoint output already exists: {args.output_dir}")

    files = compose_files(args.compose_file)
    compose = compose_command(args.env_file, files)
    partial = args.output_dir.with_name(
        f".{args.output_dir.name}.partial-{uuid.uuid4().hex}"
    )
    partial.mkdir(parents=True)
    started_at = datetime.now(UTC)
    try:
        # The compatibility journey invokes the repository CLI, which imports
        # requests. Check both isolated-runner dependencies before product calls.
        run([sys.executable, "-c", "import httpx, requests"])
        run(
            [
                sys.executable,
                "scripts/validate_experimental_candidate_config.py",
                str(args.env_file),
            ]
        )
        run([*compose, "config", "--quiet"])
        api_key = run(
            [*compose, "exec", "-T", "candidate-agent", "printenv", "API_KEY"]
        )
        if not api_key or "\n" in api_key:
            raise RuntimeError("candidate API key could not be resolved from Compose")
        child_env = {
            **os.environ,
            "CANDIDATE_API_KEY": api_key,
            "GROKTOCRAWL_API_KEY": api_key,
        }

        compatibility = partial / "compatibility.json"
        run(
            [
                sys.executable,
                "scripts/run_w9_compatibility.py",
                "--arm",
                "candidate",
                "--base-url",
                args.base_url,
                "--mcp-url",
                args.mcp_url,
                "--mcp-host-header",
                args.mcp_host_header,
                "--repetitions",
                "1",
                "--timeout",
                str(args.timeout),
                "--output",
                str(compatibility),
            ],
            env=child_env,
        )
        research = partial / "research.json"
        verify_command = [
            sys.executable,
            "scripts/verify_experimental_candidate.py",
            "--base-url",
            args.base_url,
            "--mcp-url",
            args.mcp_url,
            "--env-file",
            str(args.env_file),
            "--timeout",
            str(args.timeout),
            "--output",
            str(research),
        ]
        for file in files:
            verify_command.extend(("--compose-file", str(file)))
        run(verify_command, env=child_env)
        write_json(partial / "resources.json", resource_snapshot(compose))
        files = {
            path.name: input_digest(path)
            for path in sorted(partial.iterdir())
            if path.is_file()
        }
        write_json(
            partial / "checkpoint.json",
            {
                "schema_version": "w9-operational-checkpoint/1",
                "checkpoint": args.checkpoint,
                "not_before": due.isoformat(),
                "started_at": started_at.isoformat(),
                "completed_at": datetime.now(UTC).isoformat(),
                "candidate_revision": state["candidate_revision"],
                "runtime_revision": state["runtime_revision"],
                "successful_operations": 12,
                "receipts": files,
            },
        )
        partial.rename(args.output_dir)
        return args.output_dir
    except Exception as error:
        write_json(
            partial / "failure.json",
            {
                "schema_version": "w9-operational-checkpoint-failure/1",
                "checkpoint": args.checkpoint,
                "started_at": started_at.isoformat(),
                "failed_at": datetime.now(UTC).isoformat(),
                "error_type": type(error).__name__,
                "error": str(error)[:1000],
            },
        )
        failed = args.output_dir.with_name(
            f"{args.output_dir.name}.failed-{int(time.time())}"
        )
        partial.rename(failed)
        raise


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--checkpoint", type=int, choices=(0, 1, 2), required=True)
    value.add_argument(
        "--state-file",
        type=Path,
        default=Path(
            "docs/experiments/evidence/replacement-rehearsal/w9-pilot-state.json"
        ),
    )
    value.add_argument("--env-file", type=Path, required=True)
    value.add_argument("--compose-file", type=Path, action="append")
    value.add_argument("--base-url", default="http://127.0.0.1:18080")
    value.add_argument("--mcp-url", default="http://127.0.0.1:18002")
    value.add_argument("--mcp-host-header", default="localhost:18002")
    value.add_argument("--timeout", type=float, default=300)
    value.add_argument("--output-dir", type=Path, required=True)
    return value


def main() -> int:
    args = parser().parse_args()
    output = execute(args)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
