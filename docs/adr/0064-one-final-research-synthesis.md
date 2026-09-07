# One Final Research Synthesis

- Status: accepted
- Deciders: GroktoCrawl maintainers
- Date: 2026-09-04

## Context

Gap analysis reads evidence and the original question, never the draft answer.
The old two-pass loop nevertheless synthesized before gap analysis, discarded
that draft for polling/structured requests, and streamed two answers without
revision markers for streaming requests. This wastes one full model generation
and can leave clients displaying concatenated incompatible answers.

## Decision

Complete coverage analysis and any budgeted second discovery pass before one
final synthesis in all response modes. Keep the existing progress event types.
Only final answer text is emitted as `token` events; the final `sources` event
fixes source ordering before the first token. `done.result` is the sole terminal
answer, with the same source set. Structured results are generated and validated
once. Errors terminate without a successful done event; cancellation propagates
through the current awaited stage and client cleanup.

No provisional draft or revision protocol is introduced. Synthesis uses an
immutable context assembled after discovery, and existing LLM admission remains
in force. Coverage checks remain skipped when the source-credit budget is spent.
Duplicate-only second passes retain the existing evidence and still synthesize
exactly once. Source acquisition and early progress are handled by ADR-0059 and
its discovery pipeline work.

## Consequences

Two-pass jobs avoid a discarded generation and associated tokens, model queue
occupancy, validation, and latency. Streaming no longer concatenates two answers.
No extra concurrent LLM request is introduced, avoiding provider contention.
Time to first answer token can increase because coverage and follow-up discovery
finish first. Discovery progress remains available. This is an explicit tradeoff
for one grounded final answer and lower total work; a future provisional-answer
API must define revision/source identity semantics before introducing drafts.

## Alternatives Considered

Overlapping a provisional synthesis with gap analysis would reduce time to a
provisional token but spend a full draft generation on gap-positive jobs, contend
for LLM admission, and require a new revision-aware client protocol. It is deferred.

## Links

- [ADR-0059](0059-extend-source-artifact-reuse-across-research-passes.md)
- Issue #621
