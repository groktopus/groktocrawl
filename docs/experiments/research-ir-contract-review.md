# Knowledge IR field and version review

GroktoCrawl X is an experimental fork, not a mainline replacement. This review
addresses [issue #86](https://github.com/magnus919/groktocrawl-x/issues/86) against
main `8b42af93ac1b19507e9d7e99120eb9ca6c12c1bc`. It compares the implementation with
[ADR-0069](../adr/experimental/groktocrawl-x/0069-define-versioned-knowledge-and-verification.md) and the
canonical representation in [ADR-0071](../adr/experimental/groktocrawl-x/0071-store-research-evidence-independently-of-sessions.md).
Accepted ADR bodies are unchanged.

## Conclusion

Complete fixture research history now survives retention, publication, historical
re-render, export and same-authority import. The remaining schema work is to define
one explicit interchange contract, its reference resolution and its reader rules.
Renaming a retained envelope to `knowledge-ir/1` would conceal unresolved differences.

The next implementation should publish a machine-readable contract inventory and
compatibility examples for the existing formats, then use that evidence to design
the consolidated representation. This review does not freeze the new format or
claim independently evaluated semantic research. No human decision is required to
prepare that inventory and its tests.

## Field disposition

Paths below are within `agent-svc/agent/experimental/`. “Preserve” means retain the
information and invariant in a future contract, not copy the current JSON layout.

| ADR requirement | Implemented representation | Disposition before consolidated format freeze |
|---|---|---|
| Revision version, research/revision/parent IDs, creation time | `FixtureRevision` wraps `FixtureResearch`; IDs are nested in `KnowledgeStructure`; the retained outer envelope has its own discriminator | Preserve explicit identities, UTC chronology and nullable root parent. Define one authoritative location for each field and distinguish schema version from database migration number. |
| Objective, questions, as-of constraints and policy | Objective is mandatory for a complete revision, although optional in standalone `FixtureResearch`; questions have answer/unresolved status and a reporting claim; structure has nullable `as_of`; verifier set has policy identity | Require the complete-revision context at interchange admission. Specify null versus omitted values and coverage derivation. Do not fill missing legacy objectives, times or constraints from ambient context. |
| Snapshot descriptor and immutable content reference | `Snapshot` contains inline text, digest, normalization, media type, retrieval time and optional source dates/lineage. Stored sources separately have UUID references, exact bytes and a `source-staging/1` descriptor | Preserve exact source and date provenance. Specify the mapping from logical snapshot ID to retained content reference, including unavailable evidence. The storage descriptor alone is not the complete ADR snapshot descriptor. Decide inline versus referenced content explicitly; do not silently omit text from verifier inputs. |
| Evidence span | `Evidence` has snapshot ID, start/end code-point offsets, quote and UTF-8 quote digest; structural validation resolves the exact snapshot | Preserve zero-based half-open offsets and normalization binding. Text-only admission remains explicit; binary/PDF locators require a separate representation. |
| Claim kind, qualifiers and evidence assessment | `Claim` has kind, text, qualifiers, temporal scope and assessment state; assessment IDs live in `AssessmentLink`, while verification subjects link back through their inputs | Preserve assessment separately from verification. Choose and document direct claim links or envelope mapping; require reference closure without inventing links or assessment records for old claims. |
| Relationship direction, rationale, derivation rule and assumptions | `Relationship` distinguishes evidence-to-claim support/contradiction and claim-to-premise derivation; structural validation checks references and DAG | Preserve direction and cycle rejection in schema examples plus semantic validators. JSON Schema alone cannot prove graph closure or entailment. |
| Verification identity, input digest, verdict, time and reason | `FixtureVerification` embeds `VerificationInput`, including structure, policy, fixture verifier, evidence IDs and optional freshness context | Preserve exact checked-input binding. Current verifier kind is only `fixture_expectation`; design explicit model/prompt/tool identity for applicable real reviewers before admitting their records. Do not manufacture model identity or human approval from fixture records. |
| Source dates, freshness and independence | Nullable dates retain provenance; freshness records bind source/date basis, evaluated time, as-of and maximum age; optional lineage/origin IDs are carried | Preserve unknown dates and unknown independence. Retention TTL is separate. Recorded dates/lineage are assertions, not authenticated source independence. |
| Conflicts, coverage and unresolved questions | `Conflict` ties one claim, multiple evidence IDs and an unresolved question; `FixtureResearch.coverage()` derives complete/partial/insufficient | Preserve the existing fixture semantics. Review whether the final conflict grouping must also name mutually incompatible claims, as allowed by the ADR. Define coverage in the consolidated representation without treating an authored answered status as semantic proof. |
| Immutable IDs and predecessors | `FixtureHistory` validates introductions and predecessor IDs across a bounded linear history, including removal/reintroduction; retained complete roots validate stored prefixes | Preserve append-only identity rules. Declare the 20-revision bound and history requirements explicitly. Do not promise arbitrary branching, authenticated authorship or automatic legacy conversion. |
| Render manifest, artifacts, mappings and audits | Three `FixtureRenderAudit` inputs pin research, artifact set, renderer version and per-layer statements; retained complete publication also pins the full revision digest | Specify the separate `render-manifest/1` layout, artifact digests and audit references. Preserve summary/analysis/dossier and material-statement mappings; existing nested audit containers are not yet that manifest. |
| Canonical bytes and digest domains | Retained envelopes use JCS with schema-version prefix plus zero byte; inner fixture verifier/render hashes retain fixture serialization | Preserve each existing digest interpretation. New IR/manifest and applicable verifier input formats need explicit domains and golden vectors; wrapping an old hash in JCS does not change the old hash's meaning. |

Source models: [knowledge.py](../../agent-svc/agent/experimental/knowledge.py),
[verification.py](../../agent-svc/agent/experimental/verification.py),
[publication.py](../../agent-svc/agent/experimental/publication.py),
[revisions.py](../../agent-svc/agent/experimental/revisions.py),
[research_revision.py](../../agent-svc/agent/experimental/research_revision.py),
[research_publication.py](../../agent-svc/agent/experimental/research_publication.py),
and [source_store.py](../../agent-svc/agent/experimental/source_store.py).

## Reader and migration policy to carry forward

1. Keep existing prototype discriminators, exact stored bytes, receipts and digest
   meanings. Database schema 9 does not make a payload `knowledge-ir/1`.
2. Select readers by an explicit supported version. Reject unknown versions and
   unknown fields at admission; never try a permissive fallback or silently drop
   information. A future optional-field extension needs an explicit reader policy.
3. Distinguish raw-byte identity from validated model projection. Pydantic defaults
   and accepted input coercions can change a projection; they must not silently
   become a new authoritative hash or a fabricated provenance value.
4. Any future conversion must identify its source format, preserve original bytes
   and identities, and either satisfy the new required information or return a
   named incompatibility. Missing full history or reviewer provenance is not a
   migration opportunity to synthesize it.
5. Preserve the implemented 1 MiB canonical/encoded-bundle limits and 20-revision
   history limit in compatibility cases. The measured storage probe does not prove
   that a 495 MiB source set fits in one research bundle or justify raising limits.

## Next implementation and acceptance evidence

Deliver an executable inventory for current complete revision/publication payloads
and their nested fixture models. Include exact format names, reader entry points,
digest rules, JSON Schema where available, and a clear list of checks performed
only by semantic/context validators. Inventory hand-written outer envelopes too;
a Pydantic schema export alone does not describe their contracts.

Add small golden examples and meaningful negative controls: complete revision with
explicit objective and predecessor declarations; complete pinned publication;
unknown version/field rejection; missing provenance; snapshot/reference mismatch;
and unchanged legacy bytes/digests. Exercise the actual admission readers, not
only schema syntax. Document any schema-versus-reader differences discovered.
This creates a reproducible basis for the new format proposal; it does not rename
or migrate stored data.

After that inventory, prepare the proposed consolidated IR and render manifest
with explicit field layout, content-reference resolution, reviewer identity,
checked-input serialization, resource bounds and conversion dispositions. If a
choice changes an accepted ADR's semantics, write a successor ADR identifying the
affected scope. Keep the new proposal visibly separate until its contract and
compatibility review are complete, as required by issue #47.

Independent semantic evaluation, human calibration, provider authorization and
public client authentication remain separate gates. This work can proceed within
the accepted fixture scope while those gates are unresolved.

## Completed foundations

- [Complete history compatibility](research-ir-compatibility.md): #71 is complete;
  its original audit and intermediate “open” statements are historical.
- [Connection admission](research-storage-admission.md): #80 bounds concurrent
  database transactions per process; it is not distributed admission control.
- [Storage capacity findings](research-storage-capacity-findings.md): #82 records
  one constrained feasibility sample and restore integrity, not an SLO or adoption.

W2 and W3 remain open. PostgreSQL production use, vector consolidation, orchestration
runtime adoption and restart-safe execution are not selected by this review.

## Executable inventory follow-up

The [frozen format inventory](../../tests/contracts/research/README.md) implements
this review's next compatibility boundary under issue #88. It includes complete
root/child/publication and legacy examples, explicit outer reader contracts,
nested model schema snapshots, byte/digest pins and actual-reader negative tests.
An accepted omitted-date example also demonstrates why model defaults must not
rewrite authoritative bytes. The consolidated IR and render-manifest proposal
remains the next architecture task; these examples do not freeze either format.

The [consolidated format proposal](research-consolidated-format.md) and proposed
[ADR-0075](../adr/experimental/groktocrawl-x/0075-consolidate-research-interchange-contracts.md) now define the
next representation and confirmation plan. They remain proposed; old formats and
accepted ADR bodies are unchanged.

## Accepted consolidated direction

Magnus accepted ADR-0075 for bounded experimental implementation on 2026-09-06
([issue #92](https://github.com/magnus919/groktocrawl-x/issues/92)). The proposal
references above are historical. Consolidated admission and the bounded research
journey may now proceed; format freeze and independent semantic evaluation remain
separate gates. Existing prototype readers and byte identities remain preserved.
