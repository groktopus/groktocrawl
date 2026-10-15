# Import transaction and revocation contract

Implementation contract for [issue #65](https://github.com/magnus919/groktocrawl-x/issues/65),
under the bounded exploration in
[ADR-0071](../adr/experimental/groktocrawl-x/0071-store-research-evidence-independently-of-sessions.md).
**The isolated `ImportStore` implements this bounded contract; it is not a completed W3 gate.**
The [bundle exporter and offline validator](research-artifact-bundles.md) already
preserve original bytes; this contract defines the authority needed before copying them.

## What the first importer does

Import one verified fixture bundle into a fresh recipient root, with both origin
and recipient registered in the same isolated PostgreSQL database. The recipient
gets its own access/lifecycle identity. Original scope, research, revision and
publication identities remain inside unchanged canonical bundle bytes. A separate
recipient mapping identifies which original artifact the recipient holds.

The first importer rejects an absent origin. It does not accept remote/offline
origins, infer authority from a digest, or treat possession of a bundle as access.
A future remote importer needs an authenticated origin/deletion protocol; the
synthetic restore inventory is insufficient for that job.

Grant issuance is a privileged **trusted-server method**, like the current scope
provisioning methods. It requires a separately selected recipient scope and trusted
expected digest/context, then verifies the live origin. It is not a user-facing
authentication or RBAC implementation. No public endpoint should expose it directly.
Public principal authentication, scope membership and export/import permissions
remain W6 work and must precede public delivery.

## Storage model

The explicit schema-5 migration ships with these logical additions:

| Record | Responsibility |
|---|---|
| Root kind | Distinguish native research roots from import-only recipient roots; existing rows remain native. |
| Import operation/grant | Recipient scope/root, original scope/root/publication/generation, expected bundle and context digests, reserved bytes, grant deadline, state and committed receipt digest. |
| Imported bundle | The exact canonical bundle bytes and digest, attached to the recipient root and original identity mapping. |

Use server-issued recipient root identities; never recreate an absent/deleted
root using a caller's old ID. A recipient root contains one imported bundle and
cannot also hold native source/revision/publication writes. Imports cannot be
origins for another import. This keeps revocation one level deep and avoids cycles.

Receipt metadata can survive deletion, including identity/digest mapping, but
copied bundle, source and output text must not. Foreign-key choices must preserve
that distinction rather than preventing purge or retaining payload via a receipt.

## Grant, commit and reopen

1. **Validate outside write locks.** Admit the bounded bundle against independently
   supplied expected digest, original identities, publication context and current
   time. Failure creates no recipient root or grant.
2. **Issue a bounded grant.** In a transaction, verify the original native root is
   active and its publication/revision/source bytes match the admitted members.
   Allow an exported retention deadline shorter than the live origin's deadline;
   never extend it. Require the recipient scope to exist. Create a new import-only
   root and reserve recipient quota. Bind the grant to the origin generation and
   exact immutable bundle/context identities. No copied bundle is readable yet.
3. **Commit.** Recheck grant state/deadline, both scopes/roots, origin generation,
   bundle digest/context and retention. Atomically insert the complete canonical
   bundle and recipient mapping, convert reserved to actual charge, and commit the
   receipt. No partial member set is exposed and no provider work is invoked.
4. **Reopen.** Check the requested recipient scope/root, both current lifecycles,
   origin generation and effective retention. Revalidate bundle integrity using
   the stored binding and independently trusted context. Return an explicit
   recipient mapping alongside the unchanged original artifact.
5. **Reconcile.** Same operation and identical input returns its committed identity;
   changed input conflicts. An uncertain COMMIT acknowledgement is resolved by the
   receipt. An expired grant cannot start a new commit, but must not erase an
   already committed receipt or invalidate an otherwise retained completed copy.

Cancellation releases a pending reservation once and makes its import-only root
unusable. It does not reverse a completed import. Grant creation itself is not
request-idempotent in this first slice; a lost grant response needs explicit
reconciliation/cancellation rather than a claim of automatic recovery.

## Revocation and lock order

Origin deletion must purge all its pending/committed imported copies and release
recipient charges in the same transaction as the origin tombstone. Deleting only
the origin bytes while leaving copied bundle text would not satisfy the contract.
Recipient deletion purges only that recipient copy and preserves the origin.
Expiry denies reads on either side; automated physical expiry collection remains
separate work. Reads started before deletion may complete from their consistent
snapshot; subsequent reads must fail.

The existing single-scope lock helper cannot simply be called origin-first and
recipient-second: opposing imports can otherwise acquire scopes in opposite order.
The initial bounded implementation uses this protocol:

1. Import grant/commit/cancellation and all root deletions acquire one common,
   fixed PostgreSQL transaction advisory-lock key in exclusive mode. This
   serializes changes to import dependencies, not ordinary source/revision writes.
2. Discover affected roots while holding that coordination lock. An origin deletion
   includes its recipient roots; an import includes origin and recipient.
3. Lock every affected scope in UUID order, then every affected root in
   `(scope UUID, root UUID)` order. Deduplicate both sets. Acquire all scope locks
   before any root locks; do not alternate scopes and roots through `_lock`.
4. Recheck lifecycle, generation, quota and references under those locks, then
   mutate/purge. Use the existing bounded transaction and SQL/lock timeouts.

The shared key is an application protocol constant, not a security boundary.
All participating mutation paths must implement the same order. This serializes
imports/deletion and may become a bottleneck; do not present it as a production
throughput design. Ordinary native writes keep their existing scope-then-root
order and must reject import-only roots.

## Bounds and retention

- Preserve the existing 1 MiB encoded bundle, twenty-revision and one-hundred-source
  export limits; do not silently enlarge the bundle format for import.
- Reserve and charge the complete encoded bundle against recipient root/scope
  quotas. Origin storage stays charged independently; this is an explicit copy.
- Limit grant validity to five minutes and count at most twenty issued import
  operations per origin root in this prototype, including cancelled/deleted
  operation metadata. Reject further issuance rather than making deletion traverse
  an unbounded recipient graph. Metadata reclamation is future work.
- Effective committed retention is the earliest of the bundle deadline, current
  origin deadline and recipient policy limit (at most thirty days). Import does
  not renew the origin. Ordinary reads, replay and receipt inspection renew neither.
- No background worker, lease, webhook or automatic job continuation is introduced.

## Required CI evidence

| Scenario | Required observation |
|---|---|
| Same-authority round trip | Recipient UUIDs differ, original canonical bytes/digests stay exact, every original reference and three output layers validate. |
| No usable grant | Wrong recipient/context/digest, absent/deleted/expired origin, stale generation and expired pending grant reject without exposed payload. |
| Grant versus origin deletion | Either serialized ordering is valid; after deletion no pending grant can commit and no recipient payload remains. |
| Commit versus deletion | A committed receipt may survive, but deletion removes all original and imported text and releases each charge exactly once. |
| Concurrent opposite-scope imports | Deterministic lock ordering; bounded completion or explicit timeout, no accounting overflow or exposed partial copy. |
| Native write or chained import | Import-only root rejects source/revision/publication writes and use as a new origin. |
| Quota/fan-out limits | Concurrent reservations cannot exceed recipient quota or the twenty-operation origin cap. |
| Interrupted and ambiguous commit | Fault before commit exposes no payload; fault after commit resolves via one stable receipt without duplicating charge. |
| Recipient deletion | Copy disappears; original and other authorized recipients remain intact. |
| Grant expiry after completed commit | Existing committed copy remains available until effective retention; receipt/replay do not renew it. |
| Schema migration | Verified schema-4 source/revision/publication manifests match an independently restored pre-migration backup. |
| Backup predating origin deletion | Applying current deletion history also purges imported copies in other scopes; missing history keeps restore quarantined. |

Run actual PostgreSQL cases in the existing private Compose harness, with no
existing database/volume deletion. Record which interleavings ran and any untested
failure modes. Passing synthetic fixture imports does not establish semantic truth,
human calibration, remote authorization, production recovery or full W2/W3 completion.

## Internal use and evidence limits

Apply `ImportStore.migrate_imports()` explicitly to an existing isolated schema-4
namespace. Normal operations do not migrate. Keep the pre-migration dump private;
the CI harness restores it into a separate database and compares original source,
revision and publication manifests before proceeding to the schema-5 restore test.
There is no downgrade operation and no database or volume deletion.

A trusted server calls `reserve_import(recipient, origin_scope, origin_root,
publication, raw, expected_digest, context)` after authorizing the recipient.
It receives a new target root UUID, then calls
`commit_import(recipient, target, raw, context)`. `read_import` returns
`ImportedArtifact`: recipient scope/root, effective retention, and the independently
verified original bundle. `import_receipt` resolves a committed digest even after
payload deletion. `cancel_import` releases pending grants only.

The `import` phase in the private storage harness runs nineteen actual PostgreSQL
cases: fourteen import cases plus the five export regressions on schema 5. It
exercises concurrent grant/delete and commit/delete races, opposing scope imports,
quota and fan-out contention, injected before-commit rollback and lost commit
acknowledgement, expiry and retention clamping. These are bounded executions, not
exhaustive scheduling or a production failure-recovery proof. Pure tests cover the
independent retention limits and timezone admission. CI must pass these cases and
the final restore rehearsal; merely collecting tests is insufficient evidence.

The restore rehearsal seeds both deleted and retained imported controls. It verifies
the old backup contains the soon-to-be-revoked copy, applies current origin deletion
history, rejects reopening that copy, and checks every remaining imported bundle
and receipt. Results report `verified_live_imports`. No copied content or private
dump is uploaded as a public result.
