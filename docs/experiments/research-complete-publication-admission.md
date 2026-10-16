# Bind fixture publication to a complete research revision

Part of [issue #71](https://github.com/magnus919/groktocrawl-x/issues/71).
This pure admission boundary prevents publication from substituting research that
merely shares the retained structure. It does not yet persist publications for
complete-history roots or authorize historical re-render operations.

## Exact binding

`admit_research_publication` takes an independently retained
`AdmittedResearchRevision`, a caller-selected publication UUID and trusted
`PublicationContext`. The new canonical envelope has exactly five fields:

- `schema_version`: `retained-research-publication-prototype/1`.
- `revision_id`: the retained structure's revision identity.
- `revision_digest`: the schema-prefixed canonical digest of the complete retained
  revision envelope, including creation time, parent and introductions.
- `research`: the exact JSON research object from that retained envelope.
- `publication`: the existing three-layer fixture publication with render audits.

The function re-decodes the pinned envelope and rejects an inconsistent manually
constructed admission container. It compares the entire research object with the
retained JSON before running the existing verification, context, artifact and audit
checks. A changed objective, question, verification or assessment rationale fails
even if the caller constructs new, internally consistent render audits. Pinning
only the structure or revision ID would not provide this guarantee.

The implementation reuses legacy publication validation through an in-memory
representation; it retains the new canonical envelope and its digest. Legacy stored
bytes and readers are unchanged. The canonical envelope plus summary, analysis and
dossier must together fit the existing 1 MiB publication bound. Existing strict JSON
bounds apply before typed validation. Inner verification and render hashes keep
their original algorithms; no prototype is renamed `knowledge-ir/1`.

## Storage integration still required

The caller must obtain the pinned revision from trusted storage with its complete
ancestry and source closure. This pure function cannot authenticate that origin,
check scope access, detect deletion or establish currentness. It does not validate
an omitted predecessor chain or grant a thirty-day retention period.

The next store implementation must reserve quota and a server-issued publication
identity, bind generation/context and the full revision digest, and recheck the
retained history/source closure under the commit lock. Ordinary publication must
require the current complete revision. Historical re-render must explicitly bind a
verified original publication, preserve its revision/research and verification
context, and allow a new renderer/auditor only through the trusted context. Passing
this pure validator with a new renderer alone does not authorize a re-render.

Commit must atomically store the three outputs, reference ledger and stable receipt;
replay, cancellation, deletion/expiry and backup recovery must retain the established
lifecycle guarantees. Separate schema migration and actual PostgreSQL tests remain
required. Versioned export/import must subsequently carry complete predecessor
history. Issue #71 remains open until those paths are integrated.

## Evidence scope

Unit cases cover canonical round trip, renderer changes with the same pinned
revision, self-consistent research substitution, wrong revision/digest/context or
publication identity, failed audit, unknown fields/version and an inconsistent
pinned container. Existing legacy admission and complete revision tests run beside
them. These are synthetic fixture checks, not semantic truth, human calibration,
public authorization, production storage adoption or database publication evidence.

## Dependent storage implementation

[Complete publication storage](research-complete-publication-storage.md) adds schema-8
transactions and explicit historical re-render operations with actual migration and
restore CI required. The pure function's trust limits above remain unchanged;
complete-history export/import remains separate work under #71.
