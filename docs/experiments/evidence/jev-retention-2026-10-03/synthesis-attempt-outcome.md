# GCX-JEV-SYNTH-371 synthesis attempt outcome

**Disposition: no usable synthesis output; stopped within the registered call
budget.** One initial accidental proxy attempt returned only a sanitized
transport failure; provider delivery is unknown. It remains one external-call
attempt, not confirmed nondelivery. A later pre-call setup failure caused by a
local import-path issue happened before an output directory, attempt journal, or
provider request existed; it counts as zero provider attempts.

After fixing the local import path, the registered R1 runner made one
provider attempt for the frozen full-text/no-score payload. The configured
`free` alias matched, but the response returned `finish_reason=length` for a
request with `max_tokens=1600`. It was not a complete answer and was not
persisted for grading. The failure branch did not retain usage or latency, so
generated-token count and timing remain unknown. The remaining two distinct payloads were not attempted, and there were
no retries or subsequent provider calls. Across the study there are two external
attempts: the original attempt with unknown delivery and this one confirmed
provider response. The four-attempt maximum was not expanded.

The original private journal SHA-256 is
`369f8b89199610f1e5b48d9e4d6d3c46ae82de7a3a4c16c61f26900b58c4bc61`; stop
record `f8369ce33e3adddb737cf7cabe4196c5f5a0fe25700e9ff944963855e1df5e9d`;
run summary `0c10c5a1d19de7d584139879276ab33ad9fce0cc193bca5bc17661341e3a3850`.
The registered R1 journal SHA-256 is
`3baa5df503ab537bfbd38a1984a474aca76b5384815f1ffd3a01f71d7ecbc07b`; stop
record `f3a9b8eee69d2bcaaa2ea7cc87e939ce9d198962348c76ea5aedc30f168c5292`;
run summary `6824391dc470fe5a268972834300d1420c0d2c66ff35d41d888e0dbcec6abbf9`.
All private artifacts remain access-restricted. No complete model answer or
source text is published.

This is operational failure evidence only, not an answer-quality result. It
supports neither adoption nor rejection of filtering. The current comparison
remains unmeasured; the remaining payloads were not called. See the registered
[GCX-JEV-SYNTH-371-R1 addendum](../../jev-retention-synthesis-2026-10-03-r1.md).
