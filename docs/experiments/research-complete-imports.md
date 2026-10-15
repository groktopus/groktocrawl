# Import complete research bundles within the retained authority

Part of [issue #71](https://github.com/magnus919/groktocrawl-x/issues/71), following
[complete research exports](research-complete-bundles.md). This implements a bounded,
trusted-server copy inside the same isolated PostgreSQL authority. The internal
caller must authorize the recipient scope. It is not a public authentication API,
remote grant protocol or production storage adoption.

## Shared lifecycle with explicit formats

Migration 9 requires schema 8. It adds a `bundle_schema` discriminator to existing
import operations and stored bundles, defaulting existing rows to the legacy
`retained-artifact-bundle-prototype/1` format. New complete copies use
`retained-research-bundle-prototype/1`. A composite foreign key binds stored bundle
format to its operation, and the database digest check uses that explicit schema.
Existing payload bytes and digests do not change.

`ResearchImportStore` selects complete-history admission and origin export through
narrow hooks in the existing import lifecycle. `ImportStore` keeps legacy admission
and export. Read, commit, cancel and receipt access reject an operation belonging
to the other format. Old-schema legacy operations without a format column remain
readable through their existing default. Earlier source/revision/publication and
expiry readers accept schema 9.

Both formats share import operations, the twenty-issued-imports-per-origin bound,
recipient quotas, advisory coordination and sorted scope/root locks. Cancelled
operation metadata counts toward the bound. This preserves one deletion/copy
lifecycle instead of introducing independent accounting or purge paths.

## Origin authority and retention

Reservation first validates the bounded bundle and independently supplied origin
identities, expected digest and publication context. Under coordinated locks it
requires a live native origin and existing recipient scope, then compares the
incoming bundle with a fresh verified export of that exact origin publication.
Every field/member must match except the retention deadline, which is independently
clamped. A self-consistent, rehashed publication with different audit metadata is
still rejected if it differs from the origin.

The recipient receives a new server-issued import-root UUID. The stored bundle
preserves its original scope/root/revision/source identities; the returned
`ImportedArtifact` explicitly identifies the recipient mapping. Copies cannot take
native source/revision/publication writes or become chained import origins.

Reservation charges the exact canonical bundle size. Its grant expires within five
minutes; effective retention is the minimum of the bundle deadline, current origin
deadline and thirty days from issuance. Commit validates the representation before
write locks, then rechecks operation binding, origin generation, live roots, grant,
source authority and quota. Payload, receipt, charge and recipient deadline commit
atomically. Identical replay does not renew retention or charge again. Expired grant
metadata does not invalidate an already committed copy while its retention and
origin lifecycle remain valid.

Reads require the live origin and recipient in one repeatable-read snapshot,
validate complete bundle/history/source/publication integrity and compare against
current origin export. Shortening origin retention shortens effective access to
existing copies. Root deletion or expiry blocks access; collection purges origin
copies atomically using the existing shared lifecycle. Deleting one recipient copy
preserves the origin and peers. Pending cancellation releases quota once. Receipts
and tombstones remain after payload purge; reservation creation is not request-
idempotent. No automatic expiry scheduler or restart-safe job execution is added.

## Migration and recovery evidence

The schema-9 phase passed 74 actual PostgreSQL cases: 57 existing lifecycle,
complete-history/publication/export regressions plus 17 complete-import cases.
Combined with 189 preceding cases, all 263 passed in PR #78. New-format cases include exact
round trip, complete child ancestry, native/chained write denial, format separation,
rehashed live-origin mismatch, cancellation, grant expiry, recipient quota races,
fan-out bounds, opposing scope imports, generation changes, deletion races,
retention clamping and before-commit/lost-acknowledgement failures.

CI backs up schema 8 before migration, restores it separately and compares six
source/revision/publication/import/complete-history/complete-publication manifests.
Final schema-9 recovery seeds imported historical re-renders for the retained and
post-backup-expired complete-research controls. It proves the old backup contains
the deleted copy, reconciles current deletions, then denies that copy and validates
all retained imports through their explicit format readers. The result separates
`verified_complete_research_imports` from legacy import counts. No databases or
volumes are removed by the added workflow.

Required PostgreSQL, restore, full integration and all five exact-main workflows
passed for PR #78. The [completion assessment](research-ir-compatibility.md#verified-completion-of-issue-71)
records the pinned runs, recovery counts and remaining gates. Semantic verdicts
remain fixtures; this implementation supplies neither independent evaluation nor
human calibration.
