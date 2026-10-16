#!/usr/bin/env python3
"""Measure future-facing imperative and LangGraph research scenarios."""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from agent.experimental.adaptive_runtime import (
    AdaptiveObservation,
    AdaptivePolicy,
    ImperativeAdaptiveRuntime,
    LangGraphAdaptiveRuntime,
    compare_adaptive_outcomes,
)
from agent.experimental.specialist_runtime import (
    ImperativeSpecialistRuntime,
    LangGraphSpecialistRuntime,
    SpecialistAssignment,
    SpecialistFinding,
    compare_specialist_outcomes,
)


class EvidenceAdapter:
    def __init__(self, signals: tuple[str, ...]):
        self._signals = iter(signals)

    async def __call__(self, query: str) -> AdaptiveObservation:
        signal = next(self._signals)
        return AdaptiveObservation(f"evidence:{query}", signal)  # type: ignore[arg-type]


class SpecialistAdapter:
    async def __call__(self, assignment: SpecialistAssignment) -> SpecialistFinding:
        await asyncio.sleep(0)
        return SpecialistFinding(
            specialist_id=assignment.specialist_id,
            output_id=f"finding:{assignment.specialist_id}",
            stance="challenges" if assignment.specialist_id == "risk" else "supports",
            source_ids=(f"source:{assignment.specialist_id}",),
        )


def percentile(values: list[float], proportion: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * proportion
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def encoded_size(value: Any) -> int:
    def encode(item: Any) -> Any:
        if hasattr(item, "model_dump"):
            return item.model_dump(mode="json")
        raise TypeError(f"cannot encode {type(item).__name__}")

    return len(
        json.dumps(value, default=encode, sort_keys=True, separators=(",", ":")).encode()
    )


def accounting_without_run_id(accounting: Any) -> dict[str, Any]:
    return accounting.model_dump(mode="json", exclude={"run_id"})


async def measure_adaptive(
    repetitions: int, workload: str, signals: tuple[str, ...]
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for repetition in range(repetitions):
        outcomes = {}
        for name, runtime in (
            ("imperative", ImperativeAdaptiveRuntime()),
            ("langgraph", LangGraphAdaptiveRuntime()),
        ):
            started = time.perf_counter_ns()
            outcome, receipts = await runtime.run(
                f"adaptive-{name}-{repetition}",
                "research question",
                AdaptivePolicy(max_replans=2),
                EvidenceAdapter(signals),
            )
            elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000
            outcomes[name] = outcome
            records.append(
                {
                    "scenario": "adaptive_replanning",
                    "workload": workload,
                    "repetition": repetition,
                    "runtime": name,
                    "elapsed_ms": elapsed_ms,
                    "adapter_calls": outcome.adapter_calls,
                    "terminal_state": outcome.accounting.state,
                    "terminal_reason": outcome.stop_reason,
                    "state_bytes": encoded_size(
                        {
                            "accounting": accounting_without_run_id(outcome.accounting),
                            "queries": outcome.queries,
                            "signals": outcome.signals,
                            "outputs": outcome.outputs,
                            "receipts": {key: asdict(value) for key, value in receipts.items()},
                        }
                    ),
                }
            )
        failures = compare_adaptive_outcomes(outcomes["imperative"], outcomes["langgraph"])
        for record in records[-2:]:
            record["conformance_failures"] = list(failures)
    return records


async def measure_specialists(
    repetitions: int, names: tuple[str, ...]
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    assignments = tuple(
        SpecialistAssignment(name, f"Investigate {name}") for name in names
    )
    for repetition in range(repetitions):
        outcomes = {}
        for name, runtime in (
            ("imperative", ImperativeSpecialistRuntime()),
            ("langgraph", LangGraphSpecialistRuntime()),
        ):
            started = time.perf_counter_ns()
            outcome = await runtime.run(
                f"specialists-{name}-{repetition}", assignments, SpecialistAdapter()
            )
            elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000
            outcomes[name] = outcome
            records.append(
                {
                    "scenario": "dynamic_specialists",
                    "workload": f"{len(names)}_specialists",
                    "repetition": repetition,
                    "runtime": name,
                    "elapsed_ms": elapsed_ms,
                    "adapter_calls": outcome.adapter_calls,
                    "terminal_state": outcome.accounting.state,
                    "state_bytes": encoded_size(
                        {
                            "accounting": accounting_without_run_id(outcome.accounting),
                            "findings": [asdict(value) for value in outcome.findings],
                            "synthesis_digest": outcome.synthesis_digest,
                        }
                    ),
                }
            )
        failures = compare_specialist_outcomes(
            outcomes["imperative"], outcomes["langgraph"]
        )
        for record in records[-2:]:
            record["conformance_failures"] = list(failures)
    return records


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for record in records:
        key = (record["scenario"], record["workload"], record["runtime"])
        groups.setdefault(key, []).append(record)
    measurements = []
    for (scenario, workload, runtime), group in sorted(groups.items()):
        elapsed = [record["elapsed_ms"] for record in group]
        measurements.append(
            {
                "scenario": scenario,
                "workload": workload,
                "runtime": runtime,
                "samples": len(group),
                "elapsed_ms": {
                    "median": statistics.median(elapsed),
                    "p95": percentile(elapsed, 0.95),
                },
                "state_bytes": {
                    "median": statistics.median(record["state_bytes"] for record in group),
                    "maximum": max(record["state_bytes"] for record in group),
                },
            }
        )
    return {
        "schema_version": 1,
        "records": len(records),
        "conformance_failures": sum(
            len(record["conformance_failures"]) for record in records
        ),
        "measurements": measurements,
    }


async def run(repetitions: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    # Keep one-time dependency import and interpreter initialization out of the
    # repeated scheduling measurements. Graph compilation remains in every run.
    await LangGraphAdaptiveRuntime().run(
        "warmup-adaptive",
        "warmup",
        AdaptivePolicy(max_replans=0, max_operations=1),
        EvidenceAdapter(("adequate",)),
    )
    await LangGraphSpecialistRuntime().run(
        "warmup-specialist",
        (SpecialistAssignment("warmup", "Warm up"),),
        SpecialistAdapter(),
    )
    records = []
    for workload, signals in (
        ("adequate_first_pass", ("adequate",)),
        ("weak_then_adequate", ("weak", "adequate")),
        ("contradiction_then_recovery", ("contradictory", "weak", "adequate")),
        ("replan_limit", ("weak", "weak", "weak")),
    ):
        records.extend(await measure_adaptive(repetitions, workload, signals))
    for names in (("technical",), ("technical", "risk", "user"), ("technical", "risk", "user", "legal", "operations")):
        records.extend(await measure_specialists(repetitions, names))
    return records, summarize(records)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repetitions", type=int, default=30)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.repetitions < 1:
        parser.error("--repetitions must be at least 1")
    records, summary = asyncio.run(run(args.repetitions))
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "measurements.jsonl").write_text(
        "".join(json.dumps(record, sort_keys=True) + "\n" for record in records)
    )
    (args.output / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n"
    )
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
