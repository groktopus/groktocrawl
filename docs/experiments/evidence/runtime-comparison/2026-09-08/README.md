# W4 runtime comparison — 2026-09-08

This packet records the first paired fixture measurement for [issue #110](https://github.com/magnus919/groktocrawl-x/issues/110).
It compares the typed imperative runtime with the optional LangGraph adapter at
`langgraph==0.6.11` under the same scripted operations, budgets, operation IDs,
and callback delays.

It is an experimental runtime measurement. It does not compare research quality,
does not exercise LangGraph checkpoint persistence or recovery, and does not select
an adopted runtime.

## Method

- Seed: `20260908`
- Workloads: `serial`, `fanout_join`, and `wide_join`
- Repetitions: 30 paired repetitions per workload in each lane
- Cold lane: first measured invocation in a fresh Python process; process startup
  and dependency import are outside the per-operation timer
- Warm lane: repeated invocations in one process, with no checkpoint store
- Order: runtime order shuffled per pair from the fixed seed
- External provider spend: zero
- Failure handling: every record retained; no retry-to-success behavior

Every one of the 180 cold-lane pairs and 180 warm-lane pairs produced equivalent
terminal state, outputs, and accounting. Conformance failures: **0**.

## Observed timing

Values are per-run milliseconds from the adapter call. The imperative runtime is the
reference for this fixture, so the useful comparison is the paired distribution and
not a claim about end-to-end research latency.

| Lane | Workload | Imperative p50 / p95 | LangGraph p50 / p95 |
|---|---|---:|---:|
| First-run | serial | 2.051 / 2.260 | 5.075 / 6.230 |
| First-run | fanout/join | 2.779 / 3.129 | 6.256 / 7.323 |
| First-run | wide join | 3.441 / 3.572 | 6.368 / 6.976 |
| Repeated | serial | 2.064 / 2.207 | 4.870 / 5.626 |
| Repeated | fanout/join | 2.800 / 2.905 | 5.752 / 6.424 |
| Repeated | wide join | 3.514 / 3.789 | 6.365 / 6.964 |

The first-run serial LangGraph lane contained a 2,023 ms outlier; its p95 remains
6.230 ms. The outlier is retained in the raw measurements and is not discarded from
the distribution.

## Interpretation boundary

This packet supports three limited conclusions: both adapters conformed on these
fixture workloads; LangGraph added measurable scheduling overhead in this harness;
and the raw distributions are available for review. It does not establish that the
overhead matters for real research workloads, that LangGraph improves answer
quality, or that the imperative runtime should be adopted permanently. ADR-0073
remains proposed until the W1 baseline packet is eligible, the measurement bounds
are reviewed, and the recovery/client gates are complete.

Machine-readable records:

- [cold-summary.json](cold-summary.json)
- [warm-summary.json](warm-summary.json)
- [cold-measurements.jsonl](cold-measurements.jsonl)
- [warm-measurements.jsonl](warm-measurements.jsonl)
- [cold-conformance.jsonl](cold-conformance.jsonl)
- [warm-conformance.jsonl](warm-conformance.jsonl)
