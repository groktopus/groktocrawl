# W4 repeated runtime measurement — 2026-09-09

This packet records a second paired fixture measurement for [issue #110](https://github.com/magnus919/groktocrawl-x/issues/110). It compares the typed imperative runtime with the optional LangGraph adapter at `langgraph==0.6.11` under the same operation policy, budgets, operation identities, and callback delays within each pair.

This is experimental runtime evidence. It does not compare research quality, exercise checkpoint persistence or recovery, or select an adopted production runtime.

## Method

- Seed: `20260909`
- Workloads: `serial`, `fanout_join`, and `wide_join`
- Repetitions: 30 paired repetitions per workload in both cold and warm lanes
- Cold lane: each pair ran in a fresh Python process; the adapter timer begins after process startup and import
- Warm lane: repeated invocations ran in one process
- Order: runtime order was shuffled per pair from the fixed seed
- External provider spend: zero
- Failure handling: every record was retained; no retry-to-success behavior

All 180 cold-lane pairs and all 180 warm-lane pairs produced equivalent terminal
state, outputs, and accounting. Conformance failures: **0**.

## Observed timing

Values are per-run milliseconds from the adapter call. The imperative runtime is
the reference for this fixture; these timings are not end-to-end research latency.

| Lane | Workload | Imperative p50 / p95 | LangGraph p50 / p95 |
|---|---|---:|---:|
| Cold | serial | 0.788 / 1.361 | 181.445 / 226.474 |
| Cold | fanout/join | 1.198 / 1.401 | 194.222 / 235.750 |
| Cold | wide join | 1.719 / 2.009 | 181.659 / 195.222 |
| Warm | serial | 0.689 / 0.973 | 3.258 / 4.167 |
| Warm | fanout/join | 1.047 / 1.338 | 4.140 / 4.709 |
| Warm | wide join | 1.513 / 1.704 | 5.117 / 5.572 |

The warm serial LangGraph lane contained a 181.230 ms maximum; its p95 remains
4.167 ms. The cold lane is materially slower because each pair compiles a fresh
graph in a fresh process. The raw values and that outlier remain in the packet.

## Interpretation boundary

This run supports two limited conclusions: the LangGraph adapter continued to
conform to the shared fixture contract, and it added measurable scheduling and
graph-construction overhead under these conditions. It does not establish an
end-to-end performance bound, answer-quality difference, recovery behavior, or
production adoption decision. The imperative runtime remains the reference while
the W1 baseline and ADR-0073 review remain open.

Machine-readable records:

- [cold-summary.json](cold-summary.json)
- [warm-summary.json](warm-summary.json)
- [cold-measurements.jsonl](cold-measurements.jsonl)
- [warm-measurements.jsonl](warm-measurements.jsonl)
- [cold-conformance.jsonl](cold-conformance.jsonl)
- [warm-conformance.jsonl](warm-conformance.jsonl)
