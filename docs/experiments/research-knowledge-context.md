# Consolidated knowledge context admission

First implementation boundary under [issue #94](https://github.com/magnus919/groktocrawl-x/issues/94)
and accepted [ADR-0075](../adr/experimental/groktocrawl-x/0075-consolidate-research-interchange-contracts.md).
Experimental fork only; this is not complete `knowledge-ir/1` or a publication gate.

`knowledge_context.py` defines the strict non-result context: explicit revision,
parent ID/digest pair, objective/time/policy, referenced snapshots, evidence,
claims, relationships, questions and multi-claim conflicts. Every field is required,
including nullable values and empty arrays. Claims contain no assessment state;
separate assessment records and links are a subsequent boundary.

`context_sources.admit_knowledge_context()` admits a bounded
`knowledge-context-prototype/1` envelope containing `schema_version` and `context`.
It preserves canonical submitted bytes and uses strict JSON model validation with
no implicit defaults or scalar coercion. Caller scope/research/revision identity
and all structural references are checked before any source access.

The caller supplies a trusted `ContextSourceResolver`. That resolver is responsible
for principal authorization, active root/generation, imported logical mappings and
read consistency. No database or network resolver is wired by this change. The
admission code never fetches the snapshot URL; it compares the resolver's reference,
exact byte length/digest, normalization and media type with the pinned descriptor.
It validates UTF-8 and code-point quote spans against those bytes. Resolver denial,
unavailable evidence and cancellation propagate without a fallback source.

Resolution is sequential. Source bodies and decoded text are released before the
next resolution and are not retained in the admitted result. The caller owns time
budgets and cancellation; a later commit must recheck lifecycle/ownership. This
function does not hold a database snapshot across resolutions or prove that sources
remain accessible after admission.

## Implemented bounds and invariants

- Canonical envelope: 1 MiB, shared canonical depth/node/safe-integer limits.
- Source: up to 10 MiB exact UTF-8 bytes; text/plain or text/markdown.
- IDs: 1–200 characters; entity text/quote/reason: 1–100,000 characters.
- At most 100 snapshots, 1,000 evidence spans, 1,000 claims, 2,000 relationships,
  100 required questions and 100 conflict groups.
- Qualifiers/assumptions: at most 100; claims require at least one qualifier.
  Conflict groups require 1–1,000 distinct claims and 2–1,000 distinct evidence IDs.
- Explicit valid UTC `Z` timestamps with at most six fractional digits; revision
  creation cannot precede acquisition or its as-of input.
- Unique entity identities, paired parent ID/digest, no self-parent, local references,
  directed support/contradiction, inference-only derivations and no derivation cycles.
- Every contradiction edge has a matching conflict group on an unresolved question.

Count and source limits do not reserve root/scope quota. Storage integration must
use the existing quota/lifecycle authority. Normalization labels must match the
trusted resolver's record; this function does not perform or certify normalization.
Source dates, lineage, claim kind and question outcomes remain supplied assertions.

## Verification and next boundary

`tests/unit/test_knowledge_context.py` exercises malformed fields and versions,
missing explicit values, booleans/strings used as integers, caller and source scope,
Unicode offsets, digest/metadata mismatch, invalid UTF-8, the exact 10 MiB source
boundary, conflict grouping, derivation cycles and authority/cancellation failures.
The existing frozen compatibility suite also passes without changing its pins.

Next under #94: bind check inputs/results and external assessments to this context,
validate full retained identity history, then admit the separate audited render
manifest. The bounded research journey and retained round trip still remain. Parent
pair validation here does not establish that a parent exists or validate its history.
No format freeze, semantic-quality result, model/human trust, provider execution,
production writer or legacy migration is introduced.
