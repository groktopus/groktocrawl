# Preserve Complete Default Research Evidence and Recoverable Context

- Status: accepted
- Date: 2026-11-15
- Supersedes: [ADR-0068](0068-bounded-full-source-evidence.md)

## Context

An aggregate evidence cap was presented as if it bounded only model context, but
it also silently omitted available source text from ordinary research and
answer synthesis. Raising that cap cannot solve provider-specific context
windows, and a generic passage selector is not a complete recovery contract
unless callers can read the retained body later.

## Decision

Supply all retained source text by default. Keep `evidence_budget_chars` as an
explicit, optional user request for lexical excerpts; its minimum is 256 and
the application imposes no maximum. Acquisition and retained source bodies are
independent of this synthesis-input choice. Report source digests and exact
coverage metadata; never interpret selected-text coverage as exhaustive answer
coverage.

On a provider-reported context-window error, split source-labeled evidence
blocks and hierarchically produce notes from every block, then retry synthesis
with those notes. Splitting is driven by provider feedback rather than a
configured application character cap. Preserve original source text outside
model context, cite original sources, and disclose direct versus hierarchical
processing in completion coverage. If evidence cannot be reduced to fit, return
a structured context-limit error and no partial answer.

After fresh agent or answer acquisition, retain complete bodies in a
credential-scoped session with a 24-hour TTL. Return a session ID and
digest-pinned, paged recovery instructions. Inline source export remains
optional; if session storage fails, return the full inline evidence and an
explicit recovery failure. Research-memory cache entries keep metadata, not
source bodies, and clearly report that evidence is not retained. Existing
session paging budgets continue to bound each page, not the complete source.

## Consequences

Short corpora preserve their complete text in synthesis. Larger corpora use
additional provider calls only after an explicit context-window rejection;
the reduction prompts preserve source labels, values, units, dates, qualifiers
and disagreements, but notes are model transformations and can lose nuance.
Retained original text and digest-pinned paging make that limitation auditable.
Large session storage use is bounded by TTL and remains isolated by request
credential scope. Explicit excerpt budgets retain deterministic lexical
selection and its known semantic-recall limitations.

Old cache fingerprints are incompatible with the new full-source default.
Callers that explicitly set a budget continue to get budget-specific cache
compatibility. No provider's nominal context size is assumed to be universal.

## Validation

Regression coverage must prove that omission selects the complete text for
every supplied source, explicit excerpt selection still reports omissions, a
synthetic provider limit causes hierarchical processing of every source block,
and retained-source paging is digest-pinned and credential scoped. Run the
focused evidence, route and CLI checks, then normal repository CI before merge.

## Links

- [Issue #724](https://github.com/groktopus/groktocrawl/issues/724)
- [ADR-0068](0068-bounded-full-source-evidence.md)
