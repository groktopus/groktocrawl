#!/usr/bin/env python3
"""Parent-admitted two-case screen; defaults to refusing live execution.

No judge or labels. Execution requires exact runtime/backend hashes plus a trusted
parent admission. --describe-only reads code/config metadata without case access.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agent-svc"))
sys.path.insert(0, str(ROOT))

from agent.experimental.calibration_budget import CalibrationLedger, ExclusiveJournal
from agent.experimental.calibration_runner import (
    ARMS,
    SSHBackend,
    execute_screen,
    validate_questions,
)
from agent.settings import load_settings

RUNTIME_FILES = tuple(
    sorted(
        [
            str(path.relative_to(ROOT))
            for base in ("agent-svc/agent", "common")
            for path in (ROOT / base).rglob("*.py")
        ]
        + [
            "scripts/run_w12_live_screen.py",
            "scripts/w12_calibration_worker.py",
            "pyproject.toml",
            "agent-svc/pyproject.toml",
            "uv.lock",
        ]
    )
)


def artifact_hashes(root: Path = ROOT) -> dict[str, str]:
    return {
        name: hashlib.sha256((root / name).read_bytes()).hexdigest()
        for name in RUNTIME_FILES
    }


def bundle_hash(hashes: dict[str, str]) -> str:
    return hashlib.sha256(
        json.dumps(hashes, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def validate_admission(value: dict, hashes: dict[str, str]) -> None:
    # These attestations come from the parent, not from any retrieved page.
    required = {
        "owner_authorized_live_dispatch",
        "independent_review_no_open_p1_p2",
        "evaluator_isolation_confirmed",
    }
    if any(value.get(key) is not True for key in required):
        raise ValueError("parent admission is not complete; do not load questions")
    if (
        value.get("schema_version") != "w12.4-first-screen-admission/2"
        or value.get("runtime_files") != hashes
        or value.get("bundle_sha256") != bundle_hash(hashes)
    ):
        raise ValueError("runtime freeze mismatch")
    if value.get("reviewed_bundle_sha256") != value["bundle_sha256"]:
        raise ValueError("reviewed candidate differs")
    if (
        not isinstance(value.get("backend_descriptor_sha256"), str)
        or not isinstance(value.get("blocked_source_sha256"), list)
        or len(value["blocked_source_sha256"]) != 4
    ):
        raise ValueError("backend/unresolved-operation freeze missing")
    if (
        value.get("model_provider_attempt_upper_per_call") != 1
        or value.get("search_provider_attempt_upper_per_call") != 1
        or value.get("source_get_upper_per_call") != 2
    ):
        raise ValueError("lower-layer bounds changed")
    settings = load_settings()
    if (
        settings.typesafe_api_key
        or settings.llm_enable_thinking
        or settings.llm_llama_cpp_disable_thinking
    ):
        raise ValueError("local optional model routing changed")


def validate_backends(values: dict) -> None:
    agent, search, source, gateway = (
        values[k] for k in ("agent", "search", "source", "gateway")
    )
    if (
        agent["model"] != "free"
        or agent["jev_enabled"]
        or agent["thinking"]
        or agent["token_parameter"] != "max_tokens"
    ):
        raise ValueError("agent route drift")
    if (
        not search["enabled"]
        or not search["api_key_configured"]
        or search["suggestions"]
        or search["jev_or_rerank_env"]
        or not 1 <= search["max_results"] <= 20
    ):
        raise ValueError("search fanout contract unavailable")
    if (
        source["politeness_enabled"]
        or source["distributed_politeness"]
        or source["compatibility_proxy_configured"]
        or source["environment_proxy_configured"]
    ):
        raise ValueError("origin-coordination accounting needs a new reviewed freeze")
    if gateway["drop_params"]:
        raise ValueError(
            "gateway format-repair may remove max_tokens; live admission blocked"
        )
    if (
        gateway["free_models"] != ["openai/ornith/ornith-1.5-122b:free"]
        or gateway["retry_policy_configured"]
        or not gateway["callbacks_observability_only"]
        or gateway["pre_call_rules_configured"]
    ):
        raise ValueError("gateway contract drift")
    if any(
        costs.get(k) != 0
        for costs in gateway["declared_costs"]
        for k in ("input_cost_per_token", "output_cost_per_token")
    ):
        raise ValueError("a paid model route requires separate approval")


async def describe(backend, *, require_admissible=True):
    values = {
        role: await backend.call("describe", {}, role=role)
        for role in ("agent", "search", "source", "gateway")
    }
    if require_admissible:
        validate_backends(values)
    return values


async def run(args) -> None:
    backend = SSHBackend(ROOT)
    if args.describe_only:
        print(json.dumps(await describe(backend, require_admissible=False), indent=2))
        return
    if (
        not args.execute
        or args.admission is None
        or args.questions is None
        or args.output is None
    ):
        raise ValueError("live execution disabled; explicit parent admission required")
    hashes = artifact_hashes()
    admission = json.loads(args.admission.read_text())
    validate_admission(admission, hashes)
    values = await describe(backend)
    descriptor_hash = hashlib.sha256(
        json.dumps(values, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if descriptor_hash != admission["backend_descriptor_sha256"]:
        raise ValueError("backend changed; questions remain unread")
    # Questions are read only AFTER all frozen-code/independent-review/backend gates.
    packet = json.loads(
        sys.stdin.read() if str(args.questions) == "-" else args.questions.read_text()
    )
    cases = validate_questions(packet)
    if packet["frozen_runner_sha256"] != admission["bundle_sha256"]:
        raise ValueError("question packet names a different frozen candidate")
    args.output.mkdir(mode=0o700, parents=False, exist_ok=False)
    dispatches = ExclusiveJournal(args.output / "dispatches.jsonl")
    attempts = ExclusiveJournal(args.output / "attempts.jsonl")
    backend_receipts = ExclusiveJournal(args.output / "backend-receipts.jsonl")
    plan = ExclusiveJournal(args.output / "run-manifest.jsonl")
    plan(
        {
            "schema_version": "w12.4-first-screen-run/2",
            "experiment_id": packet["experiment_id"],
            "bundle_sha256": admission["bundle_sha256"],
            "case_ids": [x["case_id"] for x in cases],
            "arms": list(ARMS),
            "scheduled_attempts": 6,
            "status": "started",
            "lower_call_ceiling": {
                "model_gateway_http": 24,
                "model_provider_http": 24,
                "search_service_http": 12,
                "brave_api_http": 12,
                "source_get_http": 24,
                "robots_get_http": 24,
                "internet_http": 84,
                "all_model_search_source_http": 120,
            },
            "provider_output_token_reservation_upper": 6144,
            "model_input_bytes_at_gateway_upper": 288000,
            "total_cost_usd": None,
        }
    )
    # Hard local process deadline even if a library suppresses cancellation.
    # Pre-dispatch intents survive an abrupt exit; none may be blindly replayed.
    watchdog = threading.Timer(300, lambda: os._exit(124))
    watchdog.daemon = True
    watchdog.start()
    backend.receipt_sink = backend_receipts
    for receipt in backend.receipts:
        backend_receipts(receipt)
    book = CalibrationLedger(packet["experiment_id"], dispatches)
    try:
        await execute_screen(
            packet,
            book,
            backend,
            frozenset(admission["blocked_source_sha256"]),
            attempts,
        )
        plan(
            {
                "status": "halted" if book.halted else "completed",
                "halt_reason": book.halted,
                "grading_status": "parent_pending",
                "total_cost_usd": None,
            }
        )
    finally:
        watchdog.cancel()
        for journal in (dispatches, attempts, backend_receipts, plan):
            journal.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--describe-only", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--admission", type=Path)
    parser.add_argument("--questions", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
