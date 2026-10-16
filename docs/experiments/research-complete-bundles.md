# Export a complete research publication with its history

Part of [issue #71](https://github.com/magnus919/groktocrawl-x/issues/71), following
[complete publication storage](research-complete-publication-storage.md). This adds
bounded export and offline integrity validation. **It does not import a bundle or
authorize access to another scope.**

## Versioned contents

`retained-research-bundle-prototype/1` uses the existing bundle container shape with
an explicit new discriminator. It carries origin scope/root/publication UUIDs,
root-first revision UUIDs, sorted snapshot UUIDs, a timezone-qualified retention
deadline and a fixed member map. Members are raw-SHA-256 checked, canonical base64
encoded bytes:

- `revisions/<UUID>.json`: complete canonical research revision envelopes, from the
  first revision through the selected publication's revision.
- `sources/<UUID>.json` and `.body`: exact canonical source descriptors and UTF-8
  bytes for the union of snapshots referenced by that history.
- `publication.json`: the complete canonical publication envelope and its audits.
- `outputs/summary.md`, `outputs/analysis.md`, `outputs/dossier.md`: exact audited
  UTF-8 output bytes.

A historical re-render exports its selected historical revision and predecessors;
newer research is not silently appended. The selected representation is included,
not the database's reservation/receipt history or its original re-render operation.
This bundle makes no claim to reproduce every presentation operation ever issued.

Legacy `retained-artifact-bundle-prototype/1` remains unchanged and rejects the new
version. Shared container parsing preserves its existing strict field, identity,
deadline, member-name, encoding, digest and source-descriptor checks. No missing
research fields are inferred from legacy rows. Inner verification/render hash
algorithms retain their original meanings.

## Integrity and limits

`admit_research_bundle` requires an independently expected bundle digest, origin
identities, trusted publication context and aware validation time. It validates
canonical complete revision bytes and the entire typed predecessor chain, including
immutable assessments, verifications, questions, conflicts and introduction
records. It verifies exact source closure across all ancestors, then checks that
the selected publication binds the final complete revision and exact output bytes.
Rehashing a changed historical question and its publication does not bypass history
identity rules.

The existing bounds remain: at most twenty revisions, at most one hundred snapshot
identities, and at most 1 MiB for the whole encoded canonical bundle, including
base64 overhead. Individually admissible stored objects may therefore be too large
to export together. Export rejects overflow; it does not truncate history or omit
sources. Unknown/missing/extra paths, invalid encodings, noncanonical bytes and
expired deadlines fail. No archive extraction, filesystem paths or remote fetching
occurs during admission.

## Snapshot and authority

`ResearchBundleStore.export_research_publication` reads the selected publication,
complete ancestry and source bytes in one repeatable-read transaction. It checks
root lifecycle and uses the retained deadline. Export neither charges storage nor
renews retention or changes the current revision. Schema 8 suffices; this step adds
no database migration.

An export already reading a consistent snapshot may complete while a concurrent
root deletion commits. The resulting bytes can remain internally consistent; a
subsequent export must fail. Offline integrity does not prove current availability,
revocation status, principal authorization or permission to import. The upcoming
import boundary must revalidate against live origin authority and enforce recipient
quota, retention, cancellation and deletion propagation before retaining a copy.

## Evidence and remaining work

Fourteen new unit cases cover complete round trips, legacy-version separation,
missing/reordered ancestry, changed sources and outputs, unsafe member paths,
encoding/digest errors, wrong scope, expiration, unknown fields/version, oversized
bundles and rehashed historical question substitution. Existing container and
publication admission tests run alongside them.

Six actual PostgreSQL cases cover exact bytes and unchanged root state, child versus
historical ancestry, scope/context/expiry/deletion, corrupt history ledger, bounded
overflow and a concurrent deletion during a repeatable-read export. These follow
183 storage cases: 189 total expected. Final backup verification exports each
restored complete publication and reports `verified_complete_research_exports`.
Hosted database and full integration checks remain required before merge.

Complete-history import remains open under #71. This is synthetic fixture evidence,
not human semantic calibration, production adoption, vector consolidation or
restart-safe job execution.

## Dependent import implementation

[Complete research import](research-complete-imports.md) adds explicit schema-9
format binding to the shared trusted-server import lifecycle, including live-origin
comparison, recipient quota/retention and deletion propagation. Actual database,
migration and recovery validation remain required before accepting this path.
