# Define Durable Research Backup and Artifact Authority

- Status: accepted for bounded experimental use
- Deciders: Magnus Hedemark
- Accepted: 2026-09-30; direct owner approval of the bounded W9 recommendations
- Date: 2026-09-07
- Scope: bounded experimental durable research route in `magnus919/groktocrawl-x` only
- Plan: issue [#147](https://github.com/magnus919/groktocrawl-x/issues/147), W5
- Extends: ADR-0071 and ADR-0074

## Context and Problem Statement

The experimental durable route now retains admission, ownership, checkpoints,
cancellation, terminal receipts, bounded artifact bytes and terminal events in a
Valkey ledger. Process-loss tests prove recovery while that ledger remains
available, but a backup can still be ambiguous about which bytes are authoritative,
whether a restored worker lease may run, and whether deletion survives a restore.
The route also needs an explicit boundary before a production-sized artifact store
is considered.

## Decision Drivers

- Preserve acknowledged terminal receipts, bounded artifact bytes and deletion
  authority across a clean backup/restore rehearsal.
- Never restore an old worker lease as live ownership or silently make an old run
  runnable without reconciliation.
- Validate namespace, schema, per-key digests and the complete snapshot digest
  before mutating the restore target.
- Keep the current experiment bounded and honest about the absence of a production
  object store or disaster-recovery RPO/RTO.

## Considered Options

| Option | Benefit | Cost or risk |
|---|---|---|
| Backup only terminal JSON projections | Small export and easy inspection | Loses idempotency receipts, retention TTLs and index continuity |
| Copy the complete namespaced Valkey keyspace, including leases | Preserves all bytes | Can resurrect stale ownership and create unsafe post-restore dispatch |
| Copy the complete non-lease keyspace with validated restore and durable tombstones | Preserves receipts, TTLs, indexes and bounded artifacts while making active work reclaimable | Requires an explicit snapshot format and operator-controlled restore boundary |
| Introduce an external production artifact store now | Separates large bytes from execution state | Premature infrastructure and no measured migration or authority contract |

## Decision Outcome

Use the third option for the bounded experiment. `DurableResearchLedger` exports
all retained keys in its namespace except ephemeral lease keys. Each entry carries
its remaining TTL, base64-encoded dump and SHA-256 digest; the snapshot carries a
canonical whole-snapshot digest and schema version. Restore validates the complete
payload before an atomic transaction into an empty or explicitly replaced target.

Restored `running` records have no lease and are therefore reclaimable by a new
owner. A restored terminal record remains authoritative for its result digest,
terminal event history and bounded artifact payload. Research deletion writes a
retained durable tombstone and removes artifact authority; after process loss or
restore, status and artifact reads fail closed with deletion rather than resurrecting
text.

The original fixture used bounded ledger payloads as artifact authority. The
accepted deployed boundary instead makes PostgreSQL authoritative for the complete
artifact set and manifest. Valkey owns bounded execution state, receipts, indexes,
retention and deletion continuity, with matching identity/digest pointers rather
than competing artifact bytes. Deletion fences PostgreSQL publication before the
Valkey tombstone; combined restore must preserve both stores’ authority contracts.
The 1 MiB fixture bound remains historical context. This accepts no production
RPO/RTO, Qdrant removal, or broader disaster-recovery claim.

## Inherited Decision Impact

| Record | Relationship | Scope |
|---|---|---|
| ADR-0071 | Extend | Retained evidence remains separately authoritative; this ledger boundary applies only to the experimental durable route's bounded terminal payload |
| ADR-0072 | Extend | Deletion tombstones and restored terminal reads preserve the fail-closed client protocol |
| ADR-0074 | Extend | Backup/restore now defines lease exclusion, reclaim behavior and tombstone continuity for one bounded target |
| ADR-0047 | Retain | Inherited mainline endpoints keep their documented restart limitation |

## Consequences

The experiment can rehearse clean restore without treating a copied checkpoint or
lease as execution proof. Snapshot bytes are inspectable and tamper-evident, and
active work requires normal ownership reclaim after restore. The snapshot is not a
physical-loss or regional-recovery solution: operator RPO/RTO, backup encryption,
cross-version migration and external artifact capacity remain unproven. The bounded
inline payload must migrate before production-sized artifacts can be adopted.

Issue [#240](https://github.com/magnus919/groktocrawl-x/issues/240) implements that
migration behind a fourth opt-in gate. Schemas 13 and 14 establish a scoped PostgreSQL
transaction for the manifest and complete artifact set plus a pre-publication
deletion fence. The client adapter stores
only matching identities and digests in Valkey, uses a stable artifact-set identity
for reconciliation, and orders deletion through PostgreSQL before writing the Valkey
tombstone. This is implementation evidence for the proposed successor boundary;
combined backup/restore rehearsal passed as recorded below, and bounded decider
acceptance was recorded on 2026-09-30.

## Confirmation

Issue #147 records hosted evidence for terminal receipt/artifact recovery,
idempotency retry continuity, active-run reclaim, deletion-tombstone continuity,
tampered snapshot rejection and non-empty-target rejection in [the W5 packet](../../../experiments/durable-backup-restore.md),
from Runtime CI run [34142290308](https://github.com/magnus919/groktocrawl-x/actions/runs/34142290308).
PR [#244](https://github.com/magnus919/groktocrawl-x/pull/244) implemented the
successor PostgreSQL authority boundary. Runtime CI run
[34404714550](https://github.com/magnus919/groktocrawl-x/actions/runs/34404714550)
then restored PostgreSQL and Valkey into fresh targets and passed exact-byte,
pointer, deletion, interrupted-publication reconciliation, and idempotent-replay
checks. The implementation gate has passed. ADR-0074 supplies the accepted bounded execution-owner decision, and the W9
ratification completes the experimental D5 decision. This does not claim
production disaster recovery.

## Links

- [Research architecture plan](../../../experiments/research-architecture.md)
- [Recovery ownership contract](../../../experiments/recovery-ownership-contract.md)
- [Research execution confirmation matrix](../../../experiments/research-execution-confirmation.md)
- [ADR-0071](0071-store-research-evidence-independently-of-sessions.md)
- [ADR-0072](0072-expose-verified-research-through-an-experimental-protocol.md)
- [ADR-0074](0074-define-research-recovery-before-selecting-infrastructure.md)

## W9 pre-approval disposition — 2026-09-30

The [final W9 recommendation](../../../experiments/w9-replacement-readiness-decision.md)
supports this record at its bounded experimental scope after the completed
three-checkpoint pilot. At that pre-approval checkpoint, status remained proposed
pending Magnus Hedemark’s ratification; successful implementation evidence does not supply owner approval.

## Owner ratification — 2026-09-30

Magnus Hedemark approved the [bounded W9 recommendations](../../../experiments/w9-replacement-readiness-decision.md)
in this project conversation: “I approve the bounded ADR recommendations. Please proceed.”
This accepts the documented experimental scope only; the earlier pending note
is retained as decision history. It does not authorize production migration,
upstream replacement, or expansion beyond this record’s confirmation gates.
