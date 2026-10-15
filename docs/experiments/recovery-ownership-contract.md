# Recovery ownership contract status

The bounded contract fixture in `agent.experimental.recovery_contract` turns the
proposed ADR-0074 ownership rules into executable in-memory checks. It requires a
named execution owner, authoritative artifact store, monotonic fencing generation,
explicit retry/outbox/reconciliation owners, bounded lease/reclaim/heartbeat and
retry relationships, and an explicit at-least-once network-effect boundary.

The `RecoveryLedger` fixture covers stale-owner rejection, idempotent receipts,
conflicting receipts, cancellation/publication races, exact receipt binding at
publication, and provider ambiguity reconciliation for confirmed, absent, and
still-unknown outcomes (bounded evidence is tracked in [issue #143](https://github.com/magnus919/groktocrawl-x/issues/143)). The opt-in `DurableResearchLedger` merged in [PR #135](https://github.com/magnus919/groktocrawl-x/pull/135) adds a Valkey-backed bounded
implementation for admission, lease expiry/reclaim, monotonic fencing, persisted
cancellation, checkpoints, and terminal receipt commits with a recoverable projection
payload. Its tests exercise a new ledger
instance after lease loss, including stale-owner rejection, checkpoint persistence, terminal projection
persistence, and late cancellation.
This is still a recovery implementation slice, not a production deployment. The
experimental route wires durable status recovery and persisted cancellation through
[PR #139](https://github.com/magnus919/groktocrawl-x/pull/139), bounded artifact bytes and
terminal event history through [PR #142](https://github.com/magnus919/groktocrawl-x/pull/142),
and provider ambiguity reconciliation through [PR #144](https://github.com/magnus919/groktocrawl-x/pull/144).
The crash/cancel matrix is merged in [PR #146](https://github.com/magnus919/groktocrawl-x/pull/146)
with the hosted Runtime Gate, Integration Tests, PostgreSQL Storage Probes and Twin
Contracts all passing.
The next W5 slice, tracked by [issue #147](https://github.com/magnus919/groktocrawl-x/issues/147),
is merged in [PR #148](https://github.com/magnus919/groktocrawl-x/pull/148): it adds
validated non-lease snapshots, reclaim-after-restore behavior and durable deletion
tombstones. Hosted evidence is recorded in [the W5 packet](durable-backup-restore.md).
[ADR-0078](../adr/experimental/groktocrawl-x/0078-define-durable-research-backup-and-artifact-authority.md)
remains proposed; W5 is still open only for the production artifact-authority and
infrastructure decision.

See [ADR-0074](../adr/experimental/groktocrawl-x/0074-define-research-recovery-before-selecting-infrastructure.md),
the [execution confirmation matrix](research-execution-confirmation.md), and
[issue #111](https://github.com/magnus919/groktocrawl-x/issues/111).
