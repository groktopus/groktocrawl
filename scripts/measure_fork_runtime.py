#!/usr/bin/env python3
"""Measure competing-hypothesis forks for both W4 runtime arms."""

import argparse
import json
import statistics
import tempfile
import time
from pathlib import Path

from agent.experimental.fork_runtime import (
    ForkJournal,
    ForkPolicy,
    ImperativeForkRuntime,
    build_fork_graph,
)


class Adapter:
    def __init__(self, prefix):
        self.prefix, self.calls = prefix, 0

    def __call__(self, value):
        self.calls += 1
        return f"{self.prefix}:{value}"


def imperative(root, repetition):
    journal, policy = ForkJournal(root / "journal.json"), ForkPolicy()
    evidence, analyze = Adapter("evidence"), Adapter("analysis")
    runtime = ImperativeForkRuntime(journal, evidence, analyze)
    fork_point = runtime.start("parent", "scope", policy, "a")
    runtime.save_checkpoint(root / "parent-checkpoint.json", fork_point)
    parent = runtime.finish(fork_point)
    started = time.perf_counter_ns()
    retained = runtime.load_checkpoint(root / "parent-checkpoint.json")
    child_point = runtime.fork(retained, "child", policy, "b")
    runtime.save_checkpoint(root / "child-checkpoint.json", child_point)
    child = runtime.finish(child_point)
    elapsed = (time.perf_counter_ns() - started) / 1_000_000
    return record(
        "imperative", repetition, elapsed, root, evidence, analyze, parent, child
    )


def langgraph(root, repetition):
    from langgraph.checkpoint.sqlite import SqliteSaver

    journal, policy = ForkJournal(root / "journal.json"), ForkPolicy()
    evidence, analyze = Adapter("evidence"), Adapter("analysis")
    journal.admit("parent", "scope", policy)
    config = {"configurable": {"thread_id": "investigation"}}
    path = root / "graph.sqlite"
    with SqliteSaver.from_conn_string(str(path)) as saver:
        graph = build_fork_graph(journal, evidence, analyze, saver)
        graph.invoke(
            {
                "run_id": "parent",
                "scope_id": "scope",
                "fingerprint": policy.fingerprint,
                "hypothesis": "a",
                "evidence_receipt_id": "",
                "result_receipt_id": "",
            },
            config,
            interrupt_after=["gather"],
        )
        fork_point = graph.get_state(config)
        parent = graph.invoke(None, config)
        journal.fork("parent", "child", "scope", policy)
        started = time.perf_counter_ns()
        child_config = graph.update_state(
            fork_point.config,
            {"run_id": "child", "hypothesis": "b", "result_receipt_id": ""},
            as_node="gather",
        )
        child = graph.invoke(None, child_config)
        elapsed = (time.perf_counter_ns() - started) / 1_000_000
    return record(
        "langgraph", repetition, elapsed, root, evidence, analyze, parent, child
    )


def record(runtime, repetition, elapsed, root, evidence, analyze, parent, child):
    checkpoints = sum(path.stat().st_size for path in root.glob("*checkpoint*"))
    graph_db = root / "graph.sqlite"
    if graph_db.exists():
        checkpoints += graph_db.stat().st_size
    return {
        "runtime": runtime,
        "repetition": repetition,
        "fork_ms": elapsed,
        "checkpoint_bytes": checkpoints,
        "journal_bytes": (root / "journal.json").stat().st_size,
        "evidence_calls": evidence.calls,
        "analysis_calls": analyze.calls,
        "parent_result": parent["result_receipt_id"],
        "child_result": child["result_receipt_id"],
        "parent_hypothesis": parent["hypothesis"],
        "child_hypothesis": child["hypothesis"],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repetitions", type=int, default=30)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    records = []
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        for index in range(args.repetitions):
            records.extend(
                (
                    imperative(root / f"i-{index}", index),
                    langgraph(root / f"g-{index}", index),
                )
            )
    failures = sum(
        item["evidence_calls"] != 1
        or item["analysis_calls"] != 2
        or item["parent_result"] == item["child_result"]
        or item["parent_hypothesis"] != "a"
        or item["child_hypothesis"] != "b"
        for item in records
    )
    summary = {
        "schema_version": 1,
        "records": len(records),
        "conformance_failures": failures,
        "measurements": {},
    }
    for runtime in ("imperative", "langgraph"):
        group = [item for item in records if item["runtime"] == runtime]
        summary["measurements"][runtime] = {
            key: statistics.median(item[key] for item in group)
            for key in ("fork_ms", "checkpoint_bytes", "journal_bytes")
        }
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "measurements.jsonl").write_text(
        "".join(json.dumps(item, sort_keys=True) + "\n" for item in records)
    )
    (args.output / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n"
    )
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
