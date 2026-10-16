# Durable backup and restore evidence

This packet defines the W5 backup/restore rehearsal for the experimental durable
research route. It is evidence for issue [#147](https://github.com/magnus919/groktocrawl-x/issues/147)
and proposed [ADR-0078](../adr/experimental/groktocrawl-x/0078-define-durable-research-backup-and-artifact-authority.md).
It is not a production disaster-recovery claim.

## Hosted evidence

- Runtime CI run: [34142290308](https://github.com/magnus919/groktocrawl-x/actions/runs/34142290308)
- Tested commit: `7b1523dcd3062da2600eb2f395384e94b3577310`
- Integration job: [101808522545](https://github.com/magnus919/groktocrawl-x/actions/runs/34142290308/job/101808522545)
- PostgreSQL Storage Probes job: [101808522538](https://github.com/magnus919/groktocrawl-x/actions/runs/34142290308/job/101808522538)
- Twin Contracts job: [101808522507](https://github.com/magnus919/groktocrawl-x/actions/runs/34142290308/job/101808522507)
- Runtime Gate: passed
- Run conclusion: `success` on 2026-09-07

The hosted run passed the W5 recovery matrix, integration tests, PostgreSQL storage
probes, twin contracts and the fail-closed Runtime Gate. The focused local suite
reported 10 passed before the PR was opened.

The successor PostgreSQL artifact-authority rehearsal is recorded separately:

- Runtime CI run: [34404714550](https://github.com/magnus919/groktocrawl-x/actions/runs/34404714550)
- Tested commit: `2a3336b94460acafdb51c9c1405a68d9e072cf44`
- PostgreSQL Storage Probes job: [102644869244](https://github.com/magnus919/groktocrawl-x/actions/runs/34404714550/job/102644869244)
- Integration job: [102644869124](https://github.com/magnus919/groktocrawl-x/actions/runs/34404714550/job/102644869124)
- Runtime Gate: passed
- Run conclusion: `success` on 2026-09-09

That run restored PostgreSQL and Valkey together into fresh targets. It verified
exact artifact digests for a completed run, durable deletion for a deleted run,
and safe reclaim plus reconciliation for a PostgreSQL commit interrupted before
its Valkey terminal projection. Replaying the PostgreSQL commit remained
idempotent.

## Superseded bounded authority boundary

The first rehearsal used the following boundary. The Valkey durable ledger was
authoritative for admitted run identity, idempotency
receipts, ownership generation, checkpoints, terminal state, retention and deletion
tombstones. The fixture route's bounded terminal payload is authoritative for the
published manifest, artifact bytes and terminal event history. The execution
process-local map is only a projection and may be discarded.

That terminal payload was bounded by the route's 1 MiB encoded limit and each
manifest/artifact body was checked against its SHA-256 digest. A production-sized
artifact store was still an open W5 decision at that point.

PR [#244](https://github.com/magnus919/groktocrawl-x/pull/244) superseded that
experimental boundary behind a fourth opt-in gate. PostgreSQL now owns the
complete manifest and artifact bytes in one digest-checked transaction. Valkey
retains bounded execution state and matching identities, digests, and pointers.
PR [#247](https://github.com/magnus919/groktocrawl-x/pull/247) proved the combined
restore boundary described above. No external object store is implied.

## Snapshot contract

`DurableResearchLedger.export_snapshot()` copies retained keys in one namespace,
including run records, idempotency receipts and the run index. Ephemeral lease keys
are deliberately excluded. Every entry records its remaining TTL, encoded dump and
value digest; the snapshot has a schema version and canonical whole-snapshot digest.

`restore_snapshot()` validates the entire snapshot before mutation and restores in a
single Redis transaction into an empty target or an explicitly replaced namespace.
Restored `running` records have no lease and are therefore reclaimable. Terminal
receipts and deletion tombstones remain retained with their original TTL bounds.

## Required rehearsal cases

| Case | Expected result | Status |
|---|---|---|
| Completed run with terminal artifacts | Result, event history and artifact bytes reopen with identical digests | Local and hosted W5 validation passed |
| Idempotency retry after restore | Same request key returns the original run identity | Local and hosted W5 validation passed |
| Running run at backup time | No old lease is restored; a new owner can reclaim it | Local and hosted W5 validation passed |
| Deletion before backup | Tombstone survives restore and text-bearing reads fail closed | Local and hosted W5 validation passed |
| Altered snapshot digest or value | Restore rejects before changing the target | Local and hosted W5 validation passed |
| Non-empty target without explicit replacement | Restore rejects rather than overwriting state | Local and hosted W5 validation passed |
| PostgreSQL and Valkey restored together | Completed bytes and pointers match; deletion persists; interrupted publication reconciles once | Hosted successor validation passed |

The hosted packet must record the commit, Valkey version/configuration, namespace,
retention settings, target state, counts, elapsed restore time and any excluded
failure schedules. Passing these cases does not establish disk-loss, regional,
encrypted-backup or cross-version recovery.

## Outcome and next gate

The PostgreSQL artifact-authority implementation and combined restore gate have
passed for the experiment. W5 now has an accepted execution-owner decision. The
broader W6 client journey still must pass before this architecture can be called a
replacement candidate. This evidence does not claim production disaster recovery
or change mainline GroktoCrawl.
