#!/usr/bin/env python3
"""Measure version-pinned capability evolution for both W4 runtimes."""

import argparse
import json
import statistics
import tempfile
import time
from pathlib import Path

from agent.experimental.capability_runtime import (
    CapabilityRegistry,
    CapabilitySet,
    ImperativeCapabilityRuntime,
    build_capability_graph,
)


class Adapter:
    def __init__(self, name):
        self.name = name
        self.calls = 0

    def __call__(self, value):
        self.calls += 1
        return f"{self.name}:{value}"


def fixture():
    names = ("search-1", "model-1", "search-2", "specialist-2", "model-2")
    adapters = {name: Adapter(name) for name in names}
    first = CapabilitySet(
        "capability/1",
        "policy/1",
        "state/1",
        "receipt/1",
        "model/1",
        "search/1",
        adapters["search-1"],
        adapters["model-1"],
    )
    second = CapabilitySet(
        "capability/2",
        "policy/2",
        "state/2",
        "receipt/2",
        "model/2",
        "search/2",
        adapters["search-2"],
        adapters["model-2"],
        adapters["specialist-2"],
    )
    return first, second, adapters


def result(runtime, index, elapsed, checkpoint_bytes, old, new, adapters):
    return {
        "runtime": runtime,
        "repetition": index,
        "resume_and_upgrade_ms": elapsed,
        "checkpoint_bytes": checkpoint_bytes,
        "old_capability": old["capability_id"],
        "new_capability": new["capability_id"],
        "old_schema": json.loads(old["artifact_json"])["schema"],
        "new_schema": json.loads(new["artifact_json"])["schema"],
        "old_search_calls": adapters["search-1"].calls,
        "new_search_calls": adapters["search-2"].calls,
        "new_specialist_calls": adapters["specialist-2"].calls,
    }


def measure_imperative(index):
    first, second, adapters = fixture()
    registry = CapabilityRegistry((first, second), first.capability_id)
    runtime = ImperativeCapabilityRuntime(registry)
    old = runtime.search(registry.admit("old"))
    checkpoint_bytes = len(json.dumps(old, sort_keys=True).encode())
    registry.select_default(second.capability_id)
    started = time.perf_counter_ns()
    old = runtime.finish(old)
    new = runtime.finish(runtime.search(registry.admit("new")))
    elapsed = (time.perf_counter_ns() - started) / 1_000_000
    return result("imperative", index, elapsed, checkpoint_bytes, old, new, adapters)


def measure_langgraph(root, index):
    from langgraph.checkpoint.sqlite import SqliteSaver

    first, second, adapters = fixture()
    registry = CapabilityRegistry((first, second), first.capability_id)
    path = root / "graph.sqlite"
    with SqliteSaver.from_conn_string(str(path)) as saver:
        graph = build_capability_graph(registry, saver)
        old_config = {"configurable": {"thread_id": "old"}}
        graph.invoke(registry.admit("old"), old_config, interrupt_after=["search"])
        registry.select_default(second.capability_id)
        started = time.perf_counter_ns()
        old = graph.invoke(None, old_config)
        new = graph.invoke(
            registry.admit("new"), {"configurable": {"thread_id": "new"}}
        )
        elapsed = (time.perf_counter_ns() - started) / 1_000_000
    return result("langgraph", index, elapsed, path.stat().st_size, old, new, adapters)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repetitions", type=int, default=30)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    records = []
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        for index in range(args.repetitions):
            records.append(measure_imperative(index))
            run_root = root / str(index)
            run_root.mkdir()
            records.append(measure_langgraph(run_root, index))
    failures = sum(
        item["old_capability"] != "capability/1"
        or item["new_capability"] != "capability/2"
        or item["old_schema"] != item["new_schema"]
        or item["old_search_calls"] != 1
        or item["new_search_calls"] != 1
        or item["new_specialist_calls"] != 1
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
            for key in ("resume_and_upgrade_ms", "checkpoint_bytes")
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
