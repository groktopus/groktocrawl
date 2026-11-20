# Resume source-grounded synthesis in bounded outline chunks

- Status: proposed
- Date: 2026-11-15
- Scope: explicit source-snapshot synthesis through `/v2/session`; `/v2/agent` and legacy job recovery remain unchanged
- Related: ADR-0072, ADR-0076, ADR-0081, ADR-0083

## Context

Large captured evidence sets can exceed one model input or output allowance.
The legacy research path buffers one synthesis and its claimed jobs are not
restartable. Replaying a claimed legacy job can repeat paid discovery or model
operations, so synthesis continuation must be a separate explicit operation.

## Decision

Add an owner-scoped session workflow that imports an already acquired source
snapshot, persists its source manifest and outline, and processes one assigned
evidence chunk per explicit session step. Import validates unique HTTP(S) URLs,
nonempty Markdown, at most 256 sources, and a 20 MB UTF-8 snapshot ceiling. It
does not fetch those URLs. Source bodies remain in the session store; the
manifest records their exact SHA-256, character count and stable URL-derived
reference identity. Every resumed step rechecks the snapshot digests.

The first `synthesize` action creates a versioned manifest, records an
`outline_pending` operation before calling the model, validates a unique safe
outline of at most twelve sections, assigns every source chunk exactly once,
and durably saves the outline before section work. Chunks are at most 12,000
characters and preserve exact source offsets and hashes. Assignment prefers
title/outline term overlap; stable hash fallback is counted in the manifest.
The outline cannot contain more sections than available chunks.

Each subsequent request must carry `resume=true` and processes one bounded
chunk. It writes a pending operation before dispatch, then atomically saves the
complete bounded provider response before parsing or validation, then
atomically saves the validated chunk text and structured continuity digest. A
crash after the response checkpoint commits can resume parsing from that saved
response without issuing another provider call. The narrow interval between
provider acceptance and that persistence write remains an unknown outcome
because Valkey and the model provider do not share a transaction. Invalid
responses require an explicit retry. The digest carries
claims, terminology, transitions and unresolved points. When full prior
sections and the stable outline fit, they are supplied in full. Otherwise the
structured digest is used. The current chunk's evidence allowance is reserved
before continuity selection; if the outline, digest and evidence cannot fit,
the operation fails explicitly without shortening evidence. An ambiguous
pending model call requires an explicit `retry_pending=true`; no discovery or
scraping is replayed.

The input character budget (8,000–64,000; default 32,000) is distinct from the
provider's configured output-token allowance. Both are returned in progress
metadata. The character budget is a conservative payload envelope, not an
exact tokenizer count. The output allowance remains the configured
`LLM_TOKEN_PARAMETER` budget; a provider-truncated or malformed chunk is not
accepted as complete. For non-stream calls, only `finish_reason: stop` confirms
a completed text response. Truncation, filtering, refusal, absent or unknown
finish reasons, and tool-call completions are never accepted as synthesized
text. Refusal and filtered-output checkpoints are terminal and cannot be
replayed with `retry_pending`; a filtered result is not an ambiguous dispatch.
Provider failures expose only allowlisted finish reason, refusal presence and
bounded token counts when supplied; raw provider errors, prompts and source
text are excluded from diagnostics. A provider context-limit rejection is
terminal for that synthesis ID; the caller may start a new ID over the same
imported refs with a smaller input budget. Every completed chunk must contain the citation marker
for its assigned source, in addition to passing final citation-index checks.
Final assembly follows outline order, requires every section and evidence chunk
exactly once, and validates citations against the immutable source ordering
before returning a completed answer. Repeated requests after completion replay
the saved answer without another model call.

`scripts/resume_synthesis_snapshot.py` imports an existing acquired-source
JSONL and drives the session API. Canonical `source_scraped` stream events do
not contain the research question, so JSONL replay requires `--question`; if a
captured row does contain a question, it must match that argument. It parses
physical newline-delimited records and preserves Unicode line separators
inside source Markdown. The script stores only session ID, synthesis ID,
question, reference IDs and progress in an atomic mode-0600 local state file.
`--resume` continues the saved run; `--retry-pending` is required to retry an
ambiguous provider dispatch. This path can validate saved research without
repeating acquisition.

## Consequences and limits

This is an explicit synthesis continuation, not restart-safe legacy job
recovery and not an exactly-once provider guarantee. Session TTL still bounds
retention. A crash after provider acceptance but before checkpointing leaves an
unknown operation that requires explicit user retry. There is one model call
per outline or evidence chunk; total cost is therefore proportional to the
number of chunks. Structural tests prove coverage, state transitions, budget
accounting and citation-index bounds, not factual answer quality. The workflow
does not imply exhaustive discovery.

The alternative of routing `/v2/agent` through experimental durable research
was rejected because it changes an existing API execution boundary and its
admission/receipt semantics. Replaying the original legacy job was rejected
because discovery and model calls may already have occurred.

## Validation

Test canonical `source_scraped` JSONL replay with an explicit question,
preserve Unicode line separators inside bodies, and reject any conflicting
captured question. Test that absent, unknown and
non-text finish reasons fail closed and filtered output cannot be explicitly
retried. Test exact source-manifest coverage and stable digests, non-lossy chunking,
outline validation and assignment, pending/unknown operation handling,
explicit resume, immutable completed chunks, full-prior versus structured
continuity selection, input-evidence reservation, deterministic assembly,
duplicate or missing sections, citation bounds, snapshot import validation,
and private atomic CLI state writes. Run existing answer evaluation checks
before making any factual-quality claim. Validate saved evidence through this
path without search or scrape calls.
