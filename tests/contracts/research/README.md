# Existing research format compatibility examples

These are frozen, hand-authored synthetic examples for GroktoCrawl X's current
prototype formats. They are not the consolidated `knowledge-ir/1` specification,
real research outputs, human-reviewed claims or an adoption benchmark.

`inventory.json` records the two complete-envelope readers, exact outer fields,
nested model schema locations, contextual checks, size/history limits, original
hash interpretation and golden file/digest pins. The schema files describe nested
Pydantic model shapes. The outer readers are hand-written; their field sets and
required independent context are listed explicitly in the inventory. These files
are not a standalone validator for complete research interchange.

| Example | Purpose |
|---|---|
| `revision.json` | Complete root revision with explicit objective, creation time, source/evidence, authored verification and identity introductions |
| `successor.json` | Complete child retaining unchanged entity identities and introducing newly input-bound verification/assessment records |
| `publication.json` | Three fixture-audited outputs pinned to the exact complete root revision |
| `legacy-structure.json` | Original structural envelope, retained under its own reader and digest domain |
| `context.json` | Independent fixture publication policy, verifier and renderer context |

The source includes a non-ASCII character. Every checked-in file is pinned by its
raw SHA-256; the manifest separately pins the domain-separated canonical revision,
publication and legacy digests. Inner fixture input/output digests remain embedded
in the frozen examples and are validated by the actual admission readers. Golden
values are read from disk during tests, never regenerated to match the reader.

Run from the repository root:

```bash
PYTHONPATH=agent-svc:. .venv/bin/python -m pytest tests/unit/test_research_contract_inventory.py -o addopts='' -q
```

## Schema is necessary but insufficient

Cross-record references, input digests, exact quotations, history identity,
chronology, publication eligibility and caller-selected authority cannot be proved
by the exported model shapes. Complete admission requires explicit context and
semantic validators. Here “semantic validators” means cross-field/model rules;
they do not independently judge whether evidence supports a real-world claim.

The negative examples are mutations of frozen inputs exercised against the actual
readers. They cover unknown versions and fields, invented human-review fields,
missing objective and introductions, and unresolved snapshot references.

One important compatibility case is intentionally accepted: omitting a nullable
snapshot date produces a parsed model with a null default, while the authoritative
canonical bytes still omit that field. Those bytes have a different digest from
the example with an explicit null. Readers must not replace retained bytes with a
reserialized model projection. A future consolidated format must decide its own
omission/default rules explicitly.

## Maintaining the inventory

Model schema snapshots are compared with `model_json_schema()` in CI. A schema
change should cause a reviewable difference; do not update snapshots or digest
pins simply to make CI green. Review the affected prototype version, field/default
semantics, old readers and byte-preservation guarantees first. Add a new versioned
example for an incompatible format rather than overwriting the old contract.

These examples were prepared from existing deterministic fixture construction
helpers in `tests/storage/publication_fixture.py`,
`tests/unit/test_fixture_revisions.py` and
`tests/unit/test_research_publication.py`, using fixed UUIDs and timestamps.
They are frozen inputs, not a claim of independently generated compatibility.
Actual PostgreSQL source resolution, current-parent authorization, expiry, deletion
and imported-copy authority remain covered by the existing storage suites.

See the [field/version review](../../../docs/experiments/research-ir-contract-review.md)
for the next consolidated-format decisions. This inventory closes compatibility
preparation only; it does not close W2/W3 or reopen storage capacity exploration.
