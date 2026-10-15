# W12.2 Research Thread evidence

This packet contains the secret-free public evidence for the frozen W12.2
longitudinal comparison. The full private prompts and provider envelopes remain on
`inference.example.internal`; their response and envelope digests are retained in public receipts.

## Result

The tested explicit Research Thread is rejected as the default substrate for
follow-up research. Across all 27 allocated pairs, including the frozen
worst-case treatment assignments for two missing grades, its mean effect was
-9.0 points. Across the 25 pairs with both grades observed, it was still 2.8
points worse. No repetition achieved the required 10-point improvement.

The treatment was 23.0% slower and used 51.2% more model tokens. It also produced
two stale-current leaks in the contradiction stratum. The no-change anchor passed
its non-inferiority gate, and the final grading pass stayed inside its operational
failure limit, but those successes do not overcome the quality, efficiency, and
hard-boundary failures.

## Packet map

- [`public/outcome.md`](public/outcome.md) is the human-readable frozen result.
- [`public/analysis.json`](public/analysis.json) contains pair-level scores,
  conservative missing-grade assignments, hard failures, and frozen gates.
- `public/initial` and `public/trials` contain the nine initial roots and 54
  matched follow-up answers.
- `public/grades` contains 52 completed blind grades and two retained terminal
  transport failures.
- `public/work-order.json`, `public/run-summary.json`, and
  `public/grade-summary.json` account for the allocation and execution.

## Integrity and scope

The validated v3 tree contains 409 files and 7,421,004 content bytes. Its
canonical content-digest-plus-relative-path manifest contains 49,389 bytes and
has SHA-256
`aaee94a4e3786f9d0d3580a0aca552aaff910d57bb77a1550ea27a58fa47395a`.
Validation found all 54 trials, all 54 grade records, the expected 52/2 terminal
split, all 27 analyzed pairs, and no public authorization material.

V3 inherited the exact frozen 235-file v1 generation tree with SHA-256
`cd657e3f50c323b76952bb9133939c3d7705e67aa0e5be4b6ca7c216c75fd876`.
The v1 and v2 grading passes remain excluded as documented in the
[research log](../../research-thread/w12.2-research-log.md).

This synthetic experiment isolates the value of supplying explicit prior-root
lineage when both arms already receive the same source snapshots. It rejects the
tested thread representation as a default prompt substrate. It does not reject
independent retained roots, explicit comparison reports, or a future user-invoked
continuity view that proves value under a narrower experiment.
