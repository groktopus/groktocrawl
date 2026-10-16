# W3 restore rehearsal evidence

This is the bounded W3 evidence packet for the experimental fork. It records the
latest full PostgreSQL Runtime CI probe and its restore failure controls. It is
not a production recovery, disaster-recovery, or restart-SLO claim.

## Source and run identity

- Runtime CI run: [34110279633](https://github.com/magnus919/groktocrawl-x/actions/runs/34110279633)
- Tested commit: `4d464faa848165918f1694dbdec119449d0369f9`
- PostgreSQL job: [101704687976](https://github.com/magnus919/groktocrawl-x/actions/runs/34110279633/job/101704687976)
- Run conclusion: `success` on 2026-09-07
- Workflow path: `.github/workflows/runtime.yml`, PostgreSQL Storage Probes

The job exercised schema-one through schema-nine restore probes before the
dedicated source and consolidated restore rehearsals. The database was isolated
with a run-specific Compose project and a clean restore database.

## Observed results

| Evidence | Result | Artifact |
|---|---|---|
| Source-store PostgreSQL version | `17.11 (Debian 17.11-1.pgdg12+2)` | [source-storage-restore-result](https://github.com/magnus919/groktocrawl-x/actions/runs/34110279633/artifacts/10014346198) |
| Live source bytes verified after restore | `241` | [source-storage-restore-result](https://github.com/magnus919/groktocrawl-x/actions/runs/34110279633/artifacts/10014346198) |
| Live revisions verified after restore | `133` | [source-storage-restore-result](https://github.com/magnus919/groktocrawl-x/actions/runs/34110279633/artifacts/10014346198) |
| Complete-research revisions verified after restore | `179` | [source-storage-restore-result](https://github.com/magnus919/groktocrawl-x/actions/runs/34110279633/artifacts/10014346198) |
| Live publications / imports verified | `128` / `54` | [source-storage-restore-result](https://github.com/magnus919/groktocrawl-x/actions/runs/34110279633/artifacts/10014346198) |
| Complete-research publications / exports / imports verified | `58` / `58` / `21` | [source-storage-restore-result](https://github.com/magnus919/groktocrawl-x/actions/runs/34110279633/artifacts/10014346198) |
| Current deletion inventory entries | `438` | [source-storage-restore-result](https://github.com/magnus919/groktocrawl-x/actions/runs/34110279633/artifacts/10014346198) |
| Post-backup deletion denied after restore | `true` | [source-storage-restore-result](https://github.com/magnus919/groktocrawl-x/actions/runs/34110279633/artifacts/10014346198) |
| Expiry collection reconciled | `true` | [source-storage-restore-result](https://github.com/magnus919/groktocrawl-x/actions/runs/34110279633/artifacts/10014346198) |
| Live receipt references resolve | `true` | [source-storage-restore-result](https://github.com/magnus919/groktocrawl-x/actions/runs/34110279633/artifacts/10014346198) |
| Consolidated retained bytes exact / deleted resources unavailable / rows purged / receipt preserved | `true` / `true` / `true` / `true` | [consolidated-storage-restore-result](https://github.com/magnus919/groktocrawl-x/actions/runs/34110279633/artifacts/10014352741) |
| Corrupt SQL restore rejected | `true` | [source-storage-restore-failure-controls](https://github.com/magnus919/groktocrawl-x/actions/runs/34110279633/artifacts/10014347054) |
| Truncated restore rejected by authoritative verification | `true` | [source-storage-restore-failure-controls](https://github.com/magnus919/groktocrawl-x/actions/runs/34110279633/artifacts/10014347054) |

The workflow also rejected a restore with no deletion inventory and a restore
with an incomplete inventory before applying the complete inventory. The
consolidated rehearsal likewise rejected missing and stale deletion inventories.
Those negative controls are visible in the PostgreSQL job log around the
dedicated restore steps.

## Acceptance status

This run satisfies the bounded W3 acceptance gate: PostgreSQL restore, exact
retained bytes, ancestry and receipt closure, expiry reconciliation, complete
research/publication/export/import reads, post-backup deletion denial, consolidated
purge checks, and explicit corrupt/truncated restore rejection all passed. Issue
[#122](https://github.com/magnus919/groktocrawl-x/issues/122) can close against this
packet and the merged implementation in PR #125.

One successful fixture restore does not establish operator recovery, physical-loss
tolerance, capacity, latency, or production deletion authority. W5 durable
execution and W7 adoption remain separate gates.
