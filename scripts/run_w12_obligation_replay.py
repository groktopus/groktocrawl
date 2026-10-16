#!/usr/bin/env python3
"""Execute the frozen W12.4 three-arm obligation replay."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agent-svc"))

from agent.experimental.evidence_obligation_experiment import (
    ObligationReplayCase,
    execute_policy,
    score_outcome,
)

POLICIES = ("fixed", "w10_diagnostic", "obligation")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--seed", type=int, default=20260919)
    args = parser.parse_args()
    payload = json.loads(args.corpus.read_bytes())
    cases = [ObligationReplayCase.model_validate(item) for item in payload["cases"]]
    work = [
        (case, policy, repetition)
        for repetition in range(args.repetitions)
        for case in cases
        for policy in POLICIES
    ]
    random.Random(args.seed).shuffle(work)
    records: list[dict[str, Any]] = []
    for sequence, (case, policy, repetition) in enumerate(work, 1):
        outcome = execute_policy(case, policy)  # type: ignore[arg-type]
        records.append(
            {
                "sequence": sequence,
                "case_id": case.case_id,
                "stratum": case.stratum,
                "policy": policy,
                "repetition": repetition,
                "outcome": outcome.model_dump(mode="json"),
                "scores": score_outcome(case, outcome),
            }
        )
    result = {
        "schema_version": "obligation-replay-run/1",
        "created_at": datetime.now(UTC).isoformat(),
        "corpus_sha256": digest(args.corpus),
        "seed": args.seed,
        "repetitions": args.repetitions,
        "records": records,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
