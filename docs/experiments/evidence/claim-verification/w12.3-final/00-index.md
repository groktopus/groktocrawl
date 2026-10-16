# W12.3 independent claim-verification evidence

This packet contains the secret-free public evidence for the frozen W12.3
comparison. Private prompts and provider envelopes remain on `inference.example.internal`; public
records retain their digests, model identity, usage, and latency.

## Result

The tested independent verifier cleared the default-adoption gate for source-bound
publication candidates in all three repetitions. It eliminated every critical
false accept, caught every contradiction, preserved every publishable claim, and
did not follow hostile source instructions. All 36 trials completed with one model
call each.

Mean incremental latency was 11.9 seconds and high-risk p95 latency was 22.3
seconds, inside the frozen 120-second high-risk budget. Calibration Brier score
improved from 0.667 for control to 0.129, 0.129, and 0.024 across the repetitions.

## Packet map

- [`public/outcome.md`](public/outcome.md) is the human-readable result.
- [`public/analysis.json`](public/analysis.json) contains every case-level decision
  and repetition gate.
- `public/verifications` contains the 36 typed verifier records and receipts.
- `public/summary.json` accounts for completed and failed trials.

The frozen corpus, work order, protocol, and v1/v2 manifests live in
[`docs/experiments/claim-verification`](../../../claim-verification/).

## Integrity and scope

The validated v2 tree contains 75 files and 500,990 content bytes. Its 8,625-byte
canonical content-digest-plus-relative-path manifest has SHA-256
`026e657eda23dfe5d034965efc805a3649d25d56b308e28caae91feb479912fa`.
Validation found 36 completed records, zero failures, the frozen adoption decision,
and no public authorization material.

The corpus intentionally concentrates semantic publication failures. It establishes
that the verifier can causally separate these fixed claims under the tested prompt;
it does not estimate their production frequency. Production adoption should begin
behind the existing experimental boundary with live calibration and rollback.
