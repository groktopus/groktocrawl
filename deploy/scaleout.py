#!/usr/bin/env python3
"""Start the opt-in scraper topology with matched browser admission budgets.

Each scraper replica has a one-CPU quota and 16 browser slots (ADR-0089).
The API uses eight weighted admission units per force-browser scrape.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BROWSER_SLOTS_PER_REPLICA = 16
BROWSER_ADMISSION_WEIGHT = 8
MAX_REPLICAS = 4  # HAProxy server-template scraper 1-4
COMPOSE_PREFIX = [
    "docker",
    "compose",
    "-f",
    "docker-compose.yml",
    "-f",
    "docker-compose.scaleout.yml",
]


def browser_admission_units(replicas: int) -> int:
    if not 1 <= replicas <= MAX_REPLICAS:
        raise ValueError(f"replicas must be between 1 and {MAX_REPLICAS}")
    return replicas * BROWSER_SLOTS_PER_REPLICA * BROWSER_ADMISSION_WEIGHT


def compose_command(replicas: int, *, build: bool = True) -> list[str]:
    browser_admission_units(replicas)
    command = [*COMPOSE_PREFIX, "up", "-d"]
    if build:
        command.append("--build")
    command.extend(["--scale", f"scraper-svc={replicas}"])
    return command


def running_replicas(env: dict[str, str]) -> int:
    result = subprocess.run(
        [*COMPOSE_PREFIX, "ps", "--status", "running", "-q", "scraper-svc"],
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    return len(result.stdout.splitlines())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--replicas", type=int, required=True, choices=range(1, MAX_REPLICAS + 1)
    )
    parser.add_argument("--no-build", action="store_true", help="Use existing images")
    parser.add_argument(
        "--dry-run", action="store_true", help="Print the plan without starting Docker"
    )
    parser.add_argument(
        "--allow-api-restart",
        action="store_true",
        help="Confirm active agent jobs were drained before a replica-count change",
    )
    parser.add_argument(
        "--confirm-drained-downscale",
        action="store_true",
        help="Confirm removed scraper backends were drained before scaling down",
    )
    args = parser.parse_args()

    units = browser_admission_units(args.replicas)
    supplied = os.environ.get("ADMISSION_BROWSER_LIMIT")
    if supplied is not None and supplied != str(units):
        parser.error(
            f"ADMISSION_BROWSER_LIMIT={supplied} conflicts with {args.replicas} "
            f"replicas; expected {units}"
        )
    env = os.environ.copy()
    env["ADMISSION_BROWSER_LIMIT"] = str(units)
    command = compose_command(args.replicas, build=not args.no_build)
    if args.dry_run:
        sys.stdout.write(
            json.dumps(
                {
                    "replicas": args.replicas,
                    "browser_slots_per_replica": BROWSER_SLOTS_PER_REPLICA,
                    "aggregate_browser_slots": args.replicas
                    * BROWSER_SLOTS_PER_REPLICA,
                    "api_browser_admission_units": units,
                    "command": command,
                },
                indent=2,
            )
            + "\n"
        )
        return

    current = running_replicas(env)
    if current and current != args.replicas and not args.allow_api_restart:
        parser.error(
            "changing replica count changes API admission and may recreate agent-svc; "
            "drain active jobs and pass --allow-api-restart"
        )
    if current > args.replicas and not args.confirm_drained_downscale:
        parser.error(
            "downscale may interrupt active scrapes; drain removed backends and "
            "pass --confirm-drained-downscale"
        )
    subprocess.run(command, cwd=ROOT, env=env, check=True)


if __name__ == "__main__":
    main()
