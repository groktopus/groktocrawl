#!/usr/bin/env python3
"""Measure pause/restart/resume for the two future-runtime arms."""

from __future__ import annotations

import argparse
import json
import statistics
import tempfile
import time
from pathlib import Path

from agent.experimental.guidance_runtime import (
    GuidanceError,
    GuidanceJournal,
    ImperativeCheckpointStore,
    ImperativeGuidanceRuntime,
    LangGraphGuidanceRuntime,
    OwnershipToken,
    build_guidance_graph,
)


class Owner:
    def __init__(self):
        self.generation = 0
        self.live = False

    def claim(self, run_id, scope_id, owner_id):
        if self.live:
            raise GuidanceError("run still has a live owner")
        self.live = True
        self.generation += 1
        return OwnershipToken(run_id, scope_id, owner_id, self.generation)

    def checkpoint(self, token, name, checkpoint_digest):
        if not self.live or token.generation != self.generation:
            raise GuidanceError("stale checkpoint owner")

    def complete(self, token, result_digest):
        if not self.live or token.generation != self.generation:
            raise GuidanceError("stale completion owner")
        self.live = False

    def process_lost(self):
        self.live = False


class Adapter:
    def __init__(self, kind):
        self.kind = kind
        self.calls = 0

    def __call__(self, *values):
        self.calls += 1
        return f"{self.kind}:" + ":".join(values)


def measure_imperative(root: Path, repetition: int):
    journal = GuidanceJournal(root / "journal.json")
    checkpoint = ImperativeCheckpointStore(root / "checkpoint.json")
    owner, research, synthesis = Owner(), Adapter("evidence"), Adapter("answer")
    runtime = ImperativeGuidanceRuntime(
        checkpoint, journal, owner, research, synthesis
    )
    started = time.perf_counter_ns()
    pause = runtime.start("run", "scope", "before")
    pause_ms = (time.perf_counter_ns() - started) / 1_000_000
    guidance = journal.record(
        "run", "scope", "guidance", "answer", pause.question, "reliability"
    )
    owner.process_lost()
    restarted = ImperativeGuidanceRuntime(
        ImperativeCheckpointStore(root / "checkpoint.json"),
        GuidanceJournal(root / "journal.json"),
        owner,
        research,
        synthesis,
    )
    started = time.perf_counter_ns()
    result = restarted.resume("run", "scope", "after", guidance.receipt_id)
    resume_ms = (time.perf_counter_ns() - started) / 1_000_000
    return {
        "runtime": "imperative",
        "repetition": repetition,
        "pause_ms": pause_ms,
        "resume_ms": resume_ms,
        "checkpoint_bytes": (root / "checkpoint.json").stat().st_size,
        "journal_bytes": (root / "journal.json").stat().st_size,
        "research_calls": research.calls,
        "synthesis_calls": synthesis.calls,
        "owner_generations": owner.generation,
        "result": result.output,
    }


def measure_langgraph(root: Path, repetition: int):
    from langgraph.checkpoint.sqlite import SqliteSaver

    journal = GuidanceJournal(root / "journal.json")
    owner, research, synthesis = Owner(), Adapter("evidence"), Adapter("answer")
    checkpoint_path = root / "langgraph.sqlite"
    with SqliteSaver.from_conn_string(str(checkpoint_path)) as saver:
        graph = build_guidance_graph(journal, research, synthesis, saver)
        started = time.perf_counter_ns()
        pause = LangGraphGuidanceRuntime(graph, journal, owner).start(
            "run", "scope", "before"
        )
        pause_ms = (time.perf_counter_ns() - started) / 1_000_000
    guidance = journal.record(
        "run", "scope", "guidance", "answer", pause.question, "reliability"
    )
    owner.process_lost()
    with SqliteSaver.from_conn_string(str(checkpoint_path)) as saver:
        graph = build_guidance_graph(journal, research, synthesis, saver)
        started = time.perf_counter_ns()
        result = LangGraphGuidanceRuntime(graph, journal, owner).resume(
            "run", "scope", "after", guidance.receipt_id
        )
        resume_ms = (time.perf_counter_ns() - started) / 1_000_000
    return {
        "runtime": "langgraph",
        "repetition": repetition,
        "pause_ms": pause_ms,
        "resume_ms": resume_ms,
        "checkpoint_bytes": checkpoint_path.stat().st_size,
        "journal_bytes": (root / "journal.json").stat().st_size,
        "research_calls": research.calls,
        "synthesis_calls": synthesis.calls,
        "owner_generations": owner.generation,
        "result": result.output,
    }


def summary(records):
    groups = {}
    for record in records:
        groups.setdefault(record["runtime"], []).append(record)
    return {
        "schema_version": 1,
        "records": len(records),
        "conformance_failures": sum(
            record["research_calls"] != 1
            or record["synthesis_calls"] != 1
            or record["owner_generations"] != 2
            or record["result"] != "answer:evidence:research question:reliability"
            for record in records
        ),
        "measurements": {
            runtime: {
                key: statistics.median(record[key] for record in group)
                for key in ("pause_ms", "resume_ms", "checkpoint_bytes", "journal_bytes")
            }
            for runtime, group in sorted(groups.items())
        },
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repetitions", type=int, default=30)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    records = []
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        for repetition in range(args.repetitions):
            records.append(measure_imperative(root / f"i-{repetition}", repetition))
            records.append(measure_langgraph(root / f"g-{repetition}", repetition))
    result = summary(records)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "measurements.jsonl").write_text(
        "".join(json.dumps(record, sort_keys=True) + "\n" for record in records)
    )
    (args.output / "summary.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n"
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
