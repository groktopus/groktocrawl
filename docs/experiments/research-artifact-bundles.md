# Bounded research artifact bundles

`ArtifactBundleStore.export_publication` exports one retained fixture publication
and its evidence dependencies from a consistent PostgreSQL snapshot.
`admit_bundle` verifies those bytes offline. This implements the first export
boundary of [ADR-0071](../adr/experimental/groktocrawl-x/0071-store-research-evidence-independently-of-sessions.md);
it does **not** write imports, grant access in another scope, or complete W3.

## What the bundle preserves

The canonical `retained-artifact-bundle-prototype/1` JSON envelope contains:

- Original scope, research-root and publication UUIDs.
- The selected revision's complete ordered ancestry, oldest first.
- The sorted union of snapshot UUIDs referenced by that ancestry.
- The root's retained-until timestamp.
- An exact member map with base64 bytes and raw-byte SHA-256 digests.

Members are only `revisions/<UUID>.json`, `sources/<UUID>.json` (descriptor),
`sources/<UUID>.body`, `publication.json`, and the three
`outputs/{summary,analysis,dossier}.md` files. These are logical member names;
the validator never extracts paths or fetches URLs. Arbitrary names, missing or
extra members, duplicate identities, malformed/noncanonical base64 and changed
member digests fail. There is no compression, content execution or archive loader.

Canonical revision/publication/descriptor bytes are preserved exactly. Source
bodies preserve UTF-8 bytes, including CR/LF, NUL and Unicode composition. The
bundle does not rewrite embedded scope or entity identities. It includes ancestor
sources even when the selected revision no longer references them, so immutable
entity checks remain reproducible. Unselected descendants and unrelated roots
are excluded. Historical re-rendered publications can be exported with their
independently supplied trusted renderer context.

The member allowlist excludes database connection credentials, session tokens,
job state and unrelated operational records. It does not redact arbitrary source
content: source text or URLs may themselves contain sensitive material. An actual
export endpoint must authorize that content before delivery. No public endpoint
or automatic external delivery is introduced here.

## Verification and trust

Admission requires the expected bundle digest, original scope/root/publication
identities, expected publication context and a timezone-aware validation time.
These must come independently from the caller; copying expectations from an
untrusted bundle defeats that boundary. The outer digest uses the existing
schema-prefixed JCS rule. A matching hash detects changed bytes relative to that
expected digest; it is not a signature or an authenticated author identity.

The validator checks every member, canonical source descriptor and exact source
body; complete ordered parent closure; immutable entity identity across history;
revision-to-source correspondence; expected verification/render context; and all
three output bytes against their audited inputs. Correctly updating a member's
hash does not bypass those reference and output checks. Unsupported versions and
expired or timezone-free retention metadata fail closed.

Fixture semantic verdicts remain hand-authored expectations. Successful admission
neither establishes their truth nor makes historical evidence current. Offline
validation cannot know that the origin root was deleted after export. It does not
permit serving or importing that bundle: a future importer must verify recipient
authorization, explicit scope mapping, current deletion/retention history and
atomic publication before exposing bytes. No runnable job or webhook is resumed.

## Transaction and size bounds

Export reads publication, selected ancestry, referenced sources and retention in
one repeatable-read transaction. It uses the same canonical/ledger/body checks as
normal reads, then materializes and validates the entire bundle before returning.
There is no transaction held open for a slow client and no acquisition callback.
Export does not charge reservations, advance the current revision or renew expiry.
An already-started consistent read may finish across a concurrent deletion; new
exports after deletion fail. Downstream admission therefore cannot treat export
success as current deletion authority.

The **whole encoded bundle is limited to 1 MiB**, with at most twenty revisions
and one hundred distinct snapshots. Base64 overhead, repeated fixture structures,
manifest metadata and copied outputs all count. This is intentionally narrower
than root storage capacity. Some valid retained roots cannot fit this prototype;
export rejects them rather than truncating ancestry or evidence. The encoder checks
cumulative base64 size before adding members; canonical admission enforces the
complete envelope bound. The current implementation may materialize up to twenty
bounded revision records before rejecting an oversized bundle; it is not a
streaming large-corpus exporter or aggregate-memory admission controller.

There is no database migration: export uses the existing publication schema 3/4/5
readers. Stored fixture versions and hashes are unchanged. No extra dependency,
provider, vector backend or mainline service is introduced.

## Evidence

Offline tests exercise exact bytes, missing/extra/unsafe members, invalid encoding,
changed body/output/digest, duplicate identities, broken ancestry, expiry, unknown
schema, wrong origin and independently supplied trust context.

Required PostgreSQL CI adds five real-database cases: exact offline export without
renewal/charge; complete selected ancestry and historical re-render export; wrong
scope/context plus expiry/deletion; corrupt source ledger rejection; and byte-budget
rejection without mutation. The last test deliberately reduces the encoder budget
to exercise overflow against real retained rows; it is not a measured capacity
benchmark. These exported roots also participate in the existing final restore
rehearsal. Docker is unavailable locally; hosted database cases cannot be replaced
by skipped tests or mocks. Follow the `Exercise bounded artifact export and offline
validation` phase in [Runtime CI](../../.github/workflows/runtime.yml) using the
explicit isolated harness, without dropping existing databases or volumes.

Remote import, signatures/authentication, remote deletion authority,
full Knowledge IR compatibility, automatic GC and remaining W2/W3 acceptance
remain separate work.

The [import transaction and revocation contract](research-import-contract.md)
describes the schema-5 same-authority importer. It preserves these bytes in a
recipient mapping and atomically purges copies when the registered origin is
deleted. It does not make offline validation a source of current deletion authority.
