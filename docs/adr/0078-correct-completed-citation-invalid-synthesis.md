# Correct completed citation-invalid synthesis once

- Status: accepted
- Deciders: GroktoCrawl maintainers
- Date: 2026-11-08
- Partially supersedes [ADR-0064](0064-one-final-research-synthesis.md) only for the number of legacy Markdown synthesis attempts and answer-token timing.

## Context

A completed model answer can contain an invalid numeric citation even when the
acquired evidence is usable. Legacy research and answer paths previously failed
immediately. Retrying an entire job would repeat acquisition; retrying synthesis
after emitting its prefix would concatenate incompatible answers in clients that
have no revision/reset protocol. The citation parser also rejected ordinary
numeric-leading Markdown titles such as `[2026 Texas election results](url)`.
Validation counters were created on first use, so a rate query could miss the
first transition when its first observed sample was already one.

## Decision

For legacy `research/` Markdown synthesis (`/v2/agent`, `/v2/answer` and callers of
the same functions), allow at most one corrective generation after a **completed**
citation-invalid response. Preserve the original question, system prompt,
evidence context, source ordering and model. Add bounded formatting guidance;
do not feed the rejected answer back as instructions, invent sources, remap
indices, repeat discovery, or weaken citation validation. Ordinary successful
answers still use one generation. Each call retains existing admission,
cancellation, timeout, token-budget and LLM call accounting.

For SSE, collect each candidate privately. Require a provider completion and
exact token/final-content parity before citation validation. Only after successful
validation emit answer text and successful `done`. Failed candidate bytes never
reach clients or research memory. Sources and discovery progress remain live.
This intentionally defers the first answer token until a complete candidate has
passed validation; no provisional-answer or reset event is introduced.

Provider errors (including rate limits), incomplete/mismatched streams, and
cancellation do not trigger citation correction. A second citation-invalid
completion fails with `CITATION_INVALID`; there is no third generation. Existing
caller-owned rate-limit policy remains separate. Structured JSON, cached-artifact
validation, and the durable experimental execution/operation-receipt boundary are
unchanged. This is not replay permission for ambiguous paid operations.

A numeric-leading label is ordinary Markdown only when it starts with a canonical
positive integer, horizontal whitespace and a descriptive word, and has a complete
inline link destination. Reference-link forms remain fail-closed because a
closed reference tail alone does not establish a defined destination. Numeric-only markers and ambiguous numeric
forms (`[01]`, `[1e3]`, `[1-2]`, `[1, bad]`) remain fail-closed, even when linked.
Bare or incomplete descriptive numeric labels also remain fail-closed. Code,
escapes, image labels and existing nonnumeric Markdown behavior are retained.

At application creation, expose all bounded style/outcome validation counter
samples and stage/outcome correction counter samples with an idempotent zero
increment. Never reset existing counts. `groktocrawl_citation_recovery_total`
distinguishes `attempted`, `succeeded`, and `exhausted`; ordinary LLM counters retain
both calls. Scrapes before the first event are still needed for an observed rate
transition. Metrics are operational counters, not answer-truthfulness scores.

## Consequences and alternatives

One failed generation may add a second call, its tokens, cost and latency. Full
candidate buffering adds bounded-by-provider-output memory and increases time to
first answer token. Source progress still gives feedback. Total pipeline time
includes correction. Deterministic contract tests prove boundedness and byte
safety, not model answer quality or production correction success rate.

Immediate failure avoids the extra call but discards recoverable output. Streaming
provisional text needs a versioned reset/revision protocol across clients and is
not introduced here. Removing validation or silently changing source identities
would compromise grounding and is rejected. Full-job retries needlessly repeat
acquisition and are rejected.

## Verification and rollback

Unit tests cover descriptive labels at all chunk boundaries and strict malformed
numeric controls. Service tests cover valid/invalid sequences, exactly one or two
calls, immutable context, provider failures, cancellation, schema bypass, SSE
terminal semantics, and metric zero/increment behavior. Use the existing answer
evaluation harness for any subsequent quality claims. Revert this change to
restore immediate citation failure and incremental validated-prefix emission;
there is no storage migration or retained-job replay.
