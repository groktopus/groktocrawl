# Trusted Consolidated Bundle Import

- Status: accepted for bounded experimental use
- Deciders: Magnus Hedemark
- Accepted: 2026-09-30; direct owner approval of the bounded W9 recommendations
- Date: 2026-09-07
- Scope: bounded experimental PostgreSQL retention in `magnus919/groktocrawl-x` only
- Plan: issue [#103](https://github.com/magnus919/groktocrawl-x/issues/103), W3
- Extends: ADR-0076

## Context and Problem Statement

W3 can now export a consolidated publication and revalidate it offline, but the
bundle has no trusted-server recipient lifecycle. Import must preserve origin
authority: a recipient copy is readable only while the origin root remains live,
the origin operation is still current, and the recipient's independent retention
and quota bounds remain valid.

## Decision Drivers

- Validate all bytes and identities before mutation.
- Keep import trusted-server only; no public authentication or arbitrary remote grants.
- Reuse the existing bounded import lifecycle for quota, cancellation, deletion and expiry.
- Make retries idempotent through a receipt digest.
- Preserve explicit experimental scope and avoid production data movement.

## Considered Options

| Option | Benefit | Cost or risk |
|---|---|---|
| Copy only the publication digest | Small write path | Cannot reopen exact sources and audited outputs offline |
| Add a separate import topology | Strong isolation | Duplicates deletion, expiry and quota coordination |
| Extend the shared import lifecycle with a versioned consolidated payload | Reuses tested authority, fencing and purge behavior | Requires an opt-in schema migration and a consolidated-specific reader |

## Decision Outcome

Use the third option. Schema 12 admits
`retained-consolidated-bundle-prototype/1` in the existing trusted import tables.
Reservation records the origin scope/root/operation, origin generation, bundle
digest, context digest, bounded grant, and independently clamped retention. Commit
revalidates the canonical bundle before writing, requires the origin to remain a
live consolidated root with the same current operation, and records the bundle
digest as the idempotent receipt. Read joins recipient and origin authority and
re-runs offline admission. Origin deletion or expiry therefore makes the copy
unreadable and the existing purge path removes its bytes.

The migration is explicit and isolated. It does not expose a public route, select
PostgreSQL for production, remove Qdrant, or provide restart-safe execution.

## Consequences

The experimental fork can demonstrate cross-scope retained-artifact lifecycle and
failure races without treating a copied bundle as independently authoritative.
The recipient still carries its own quota and retention charge. A deleted, expired,
altered, schema-incompatible or stale origin fails closed. Backup/restore remains
an evidence task and does not follow from this import implementation alone.

## Confirmation

Add hosted PostgreSQL tests for schema-12 migration, exact reserve/commit/read
round trips, quota and retention clamping, idempotent receipts, altered bundles,
origin deletion/expiry, cancellation and commit races. Run full CI before closing
issue #109.

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
