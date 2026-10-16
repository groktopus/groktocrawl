# Jev-retention synthesis replay: registered continuation R1

**Study:** GCX-JEV-SYNTH-371. **Status:** stopped after its first provider
response was truncated. No additional calls are authorized or made under this
continuation. This addendum supersedes the original
call-sequence execution instructions only as to run identity, budget, and
failure recording. It does not change the frozen sources, question, prompts,
payloads, rubric, or original total maximum of four attempted provider calls.

The original first call was triggered by an accidental `--help` invocation of
a runner that executed unconditionally. Its proxy returned a sanitized
transport failure without HTTP status, provider metadata, token usage, or
answer. Delivery to the provider is unknown, and it cannot be described as a
confirmed nondelivery. The original append-only journal SHA-256 is
`369f8b89199610f1e5b48d9e4d6d3c46ae82de7a3a4c16c61f26900b58c4bc61`; the stop
record SHA-256 is
`f8369ce33e3adddb737cf7cabe4196c5f5a0fe25700e9ff944963855e1df5e9d`; the run
summary SHA-256 is
`0c10c5a1d19de7d584139879276ab33ad9fce0cc193bca5bc17661341e3a3850`. These
files remain private and unchanged. No answer text was received or persisted.
The public deviation record is
[the attempt outcome](evidence/jev-retention-2026-10-03/synthesis-attempt-outcome.md).

The R1 runner passed static review and parent review, then attempted one
frozen payload. The response matched the `free` alias and returned
`finish_reason=length` for the registered request with `max_tokens=1600`. No complete answer was
persisted, and the remaining two unique frozen payloads were not attempted.
No retries or further calls are authorized under this registration. The
original protocol registered three distinct inputs: full text without scores, full text with score metadata (also the
existing-filter arm), and the no-score 8,000-character-per-page projection.
The uncertain first attempt may have been delivered, so the full/no-score input
could have been sent twice across the study. Count all four possible
provider deliveries against the original four-attempt ceiling; do not claim
exactly-once delivery. Stop on any proxy/provider error, unexpected alias,
invalid or oversized output, truncation, or input-integrity failure. Preserve
an unmatched call-start event as attempted with unknown outcome after
interruption. Error strings and full provider error bodies must not be stored or
printed.

The runner used a new, empty mode-0700 output directory distinct from the
original attempt directory. It required explicit `--execute`, parsed help before
repository imports, pinned the frozen plan and payload hashes, and wrote a call
start before dispatch. The completed attempt does not support an answer-quality
grade or filter recommendation.
