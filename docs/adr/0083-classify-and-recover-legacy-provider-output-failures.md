# Classify and recover legacy provider output failures

- Status: proposed
- Date: 2026-11-15
- Scope: legacy `/v2` agent and answer synthesis
- Related: [ADR-0075](0075-retain-known-model-output-limit-receipts.md), [ADR-0078](0078-correct-completed-citation-invalid-synthesis.md)

## Context

Legacy research rejected a provider completion stopped at `finish_reason:
length`, but surfaced a generic provider error and retained no safe diagnostics.
Refusals used the same error. Citation-grounded synthesis buffers candidates
before publication, so a single bounded retry can be attempted without
publishing partial text. CLI pyramid mode also transforms a completed response
after the server has finished; the terminal data must survive a local export
failure.

## Decision

For a provider completion that explicitly reports refusal, raise a
non-retryable `LLM_REFUSAL`. Do not retry it. For a completion stopped with
`finish_reason: length`, raise `LLM_OUTPUT_TRUNCATED`; never return or publish
its partial content. Citation-grounded Markdown synthesis may retry exactly
once with the same complete evidence and a concise-complete-answer instruction.
If that retry is also truncated, fail with no answer and report one recovery
attempt. Structured output does not use the Markdown retry path.

Retain only allowlisted failure diagnostics: a recognized finish reason (or
`other`), a refusal-present boolean, and provider token counts when they are
nonnegative integers no greater than 10,000,000. Do not retain provider response
text, prompt, provider/model identity, endpoint, headers, credentials, or raw
exceptions. Persist these fields as `errorCode` and `errorDetails` on a failed
legacy agent job and include them in stream error events. They are diagnostic
metadata, not a completion receipt for the experimental durable-research
protocol.

When pyramid mode receives a terminal response, atomically write it to a
mode-0600 `00-terminal-response.json` checkpoint before filesystem
transformation. A missing body remains an explicit metadata-only source entry;
the pyramid reports partial source-body completeness and preserves available
recovery metadata. No source text is fabricated, and a missing body does not
discard the completed synthesis.

When pyramid mode opts in to source export, each `source_scraped` event also
includes the acquired Markdown body. The CLI appends each event to a mode-0600,
fsynced `00-acquired-sources.jsonl` capture and checkpoints acquisition progress
before synthesis begins. If a later stream error arrives, it writes a failure
pyramid with discovered results and acquired source dossiers, marks those
dossiers `acquired_not_synthesized`, retains safe failure diagnostics, and exits
unsuccessfully. Acquired evidence is not represented as a synthesis. The JSONL
remains a durable event record if later export transformation fails.

## Consequences

Refusals cannot be bypassed by retry logic. A single concise retry can recover
from an output cap while keeping all evidence and rejecting incomplete drafts.
If it fails, callers receive a machine-readable reason and bounded diagnostics.
Pyramid consumers can distinguish complete from partial source-body exports
and recover the terminal response after a transformation failure. Checkpoints
contain the completed response and acquired inline evidence, so their local
files are owner-only and should be handled as research artifacts.

## Validation

Test provider refusal and output truncation with private sentinel content and
verify that only the allowlisted metadata escapes. Verify refusal gets no retry,
truncation gets at most one same-evidence retry, and two truncations publish no
answer. Verify stream partial text is discarded. Verify failed job status carries
safe error metadata. Exercise a terminal pyramid response before export, then a
missing-source body; the checkpoint and synthesis must remain, the missing body
must be marked, and no output text may be fabricated. Exercise a provider failure
after acquisition and verify the private source capture and explicit
not-synthesized dossiers remain usable. Run repository CI before merge and a
single instrumented end-to-end research task after deployment.
