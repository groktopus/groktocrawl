# Retained-storage capacity findings

The bounded probe retained 495 MiB of large source bodies, published small complete
artifacts during ingestion, and restored exact bytes and receipts without a
mismatch. This completes [issue #82](https://github.com/magnus919/groktocrawl-x/issues/82)'s
measurement scope once this assessment is merged. It establishes a reproducible
feasibility result under the declared limits; **W3 and production adoption remain
open**.

## What was run

The [predeclared design](research-storage-capacity.md) used PostgreSQL 17.11 with
one CPU and 512 MiB memory, plus an adapter limited to one CPU and 256 MiB memory.
It wrote one 99 MiB source-heavy root sequentially, then four more 99 MiB roots
concurrently while ten small publications used a separate root in that scope.
Each writer reserved capacity before generating one body. The corpus was unique,
deterministic ASCII hex, not representative enterprise source material.

The corrected [PR #84 Runtime CI](https://github.com/magnus919/groktocrawl-x/actions/runs/34036401190)
executed merge-test commit `41903eb82cef62ba1aaf2e91dfdb7fa178e113ac` for PR head
`25ca367fff7fbb441ad7642d08a31d0d15dc1fcc`. The workload ran on 2026-09-06,
13:39–13:44 UTC. All eleven PR checks passed, including full integration and 268
existing PostgreSQL cases. All five workflows also passed on merged main
`cc6604f4ecd27fa23079eb85ac7dd875d80df84a`, including
[post-merge Runtime CI](https://github.com/magnus919/groktocrawl-x/actions/runs/34037528893).
The measurements below come from the corrected PR run, not a pooled average of
these validation runs.

The raw [corrected report](evidence/storage-capacity/corrected/result.json) retains
operation records, the complete manifest, image identities, host identity and
verification outcomes. Its [resource samples](evidence/storage-capacity/corrected/resources.jsonl)
are retained alongside it. The design-file SHA-256 at execution was
`c7cf93810853243eba8698b1c27b0c363a72f468e1eb53cb32fd237211687142`.

## Observations

| Measurement | Corrected run |
|---|---|
| Sequential ingestion | 99 MiB in 5.566 seconds |
| Four-writer ingestion | 396 MiB in 24.004 seconds total |
| Publication overlap | All ten cycles overlapped ingestion |
| Publication reservation | Ten samples; median 1.175 s, maximum/nearest-rank p95 1.479 s |
| Publication commit | Ten samples; median 1.187 s, maximum/nearest-rank p95 1.820 s |
| Publication read | Ten samples; median 0.185 s, maximum/nearest-rank p95 0.327 s |
| Exact source body bytes | 519,045,376 (495 MiB large bodies plus two 128-byte fixtures) |
| Total logical charge | 520,514,705 bytes, reconciled to bodies, descriptors, complete revisions, publications and outputs |
| Database size | 8,451,763 bytes before; 547,509,939 bytes after workload |
| Research relation total | 539,385,856 bytes after, including indexes and TOAST |
| Cluster-wide WAL delta | 556,224,320 bytes during the workload interval |
| Custom-format dump | 337,973,077 bytes; 195.654 seconds |
| Restore command | 29.133 seconds, followed by independent verification |
| Workload process peak RSS | 163,264 KiB, as reported by Linux `getrusage` |
| Docker sampled memory maximum | PostgreSQL 256.9 MiB; adapter 81.84 MiB |

Durations include the caller/driver and connection work described by each operation
record. Ingestion duration also includes generation and reservation; it is not a
pure database write-speed measurement. The ten publication samples are too few to
establish a stable tail estimate; their nearest-rank p95 is simply their maximum.
Backup/restore timings each have one observation and exclude subsequent integrity
verification. WAL is cluster-wide, not attributable exclusively to one table.

There were 79 resource sample records, with zero recorded sampling failures;
PostgreSQL appeared in 79 and the temporary adapter in 14. The sampler waited two
seconds after collecting each sample, so observed sample-start intervals were
3.551–4.558 seconds (median 3.554), not exactly two seconds. Temporary adapters did
not exist during all phases. Docker sampling can miss peaks and uses different
accounting from process RSS; the lower sampled adapter maximum does not supersede
the recorded process peak. CPU samples peaked at 105.96% for PostgreSQL and 83.64%
for the adapter under Docker's sampling/accounting conventions. They do not establish
sustained CPU demand or unused capacity.

## Integrity and boundary results

Fresh connections before backup and after independent restore verified all 57
sources, two complete revisions, eleven publications and seventy committed
receipts. Body regeneration, canonical descriptors, publication outputs, root/scope
charges and manifest digest matched. Both verification passes reported zero
mismatches. Cancelled reservation operations had no committed source receipt, and
no pending source operations remained.

The source-heavy root rejected another 2 MiB reservation before acquisition with
no charge, source-count or current-pointer change. A separate scope reserved exactly
1 GiB logically, rejected an extra byte and released reservations through
cancellation. This did not store 1 GiB of physical source bodies.

A valid complete publication was retained but its export was rejected because
base64 encoding exceeded the 1 MiB bundle ceiling. The underlying bytes remained
readable. A 100 MiB retained-root quota therefore must not be advertised as a
100 MiB complete-revision or export capability.

## Earlier incomplete report

The [first run](https://github.com/magnus919/groktocrawl-x/actions/runs/34035703079)
passed its data/restore assertions but omitted adapter image identity. The harness
queried Compose after temporary containers had been removed. Its
[original report](evidence/storage-capacity/initial/result.json) and
[resource samples](evidence/storage-capacity/initial/resources.jsonl) are preserved
unchanged, including two sampling failures. Although that early report's status
field says `verified_feasibility_sample`, its provenance was incomplete and it is
not the accepted evidence for issue #82.

The corrected harness captures the running adapter's image and effective limits,
rejecting missing, invalid or changed identity. The workload and resource limits
were unchanged. Both runs remain visible; they are not a controlled before/after
performance comparison. For example, the first dump took 151.204 seconds versus
195.654 seconds in the corrected run, without evidence establishing the cause.

## Consequences and next work

Retained bytes, quota rejection and restore worked at this declared scale. The
observations do not establish acceptable production footprint, predictable
publication latency or backup targets. Continue bounded exploration; do not promote
a baseline or accept PostgreSQL production/vector adoption on this evidence.

Before performance tuning, select and predeclare the workload and success criteria
that matter for the intended deployment. In particular, investigate publication
latency under ingestion with a design that separates application generation/event-
loop work, connection setup and database lock/commit time. The current wall-clock
samples cannot identify which component caused the observed delays. Avoid changing
locking or adding infrastructure on an untested causal explanation.

Full 1 GiB physical occupancy, multiple processes, long histories, large complete
IR, vector contention, retention churn and production recovery remain unmeasured.
Independent semantic evaluation and consolidated `knowledge-ir/1` remain separate
W2 gates. No provider spend, default-stack cutover, Qdrant removal, new runtime or
volume deletion was authorized or performed by this probe.
