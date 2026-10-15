# Staged continuation Noul pilot outcome

Status: **bounded exploratory pilot completed; no product decision or search
was made.**

The signed packet at
[`continuation-pilot-2026-10-03.case-packet.json`](continuation-pilot-2026-10-03.case-packet.json)
was frozen before execution (SHA-256
`6bdff861da0dfb8f20b7d11983034520d7a37bda6e8d0ad7037da40d314d023c`). It
contains eight new public-project documentation obligations at one pinned Git
revision. All 19 planned sequential calls completed with validated
`answers[id].noul` values from requested and returned model `jev-1.13.0`: eight
first-pass sufficiency judgments, eight supplied-hypothesis reviews, and three
dependent public-addressability judgments. There were no retries, provider
failures, parse failures, or schema failures. The sanitized receipts, exact
request/response digests, probabilities, usage, and latency summaries are in
[`continuation-pilot-2026-10-03.results.json`](continuation-pilot-2026-10-03.results.json);
private raw receipts remain outside version control.

| Case | Best-effort reference | P(first-pass sufficient) | P(proposed gap exists) | P(publicly addressable, if asked) |
|---|---|---:|---:|---:|
| pilot-01 | sufficient; false missing proposal | 0.93 | 0.02 | not asked |
| pilot-02 | sufficient; false missing proposal | 0.94 | 0.02 | not asked |
| pilot-03 | missing numeric retry limit | 0.08 | 0.97 | 0.84 |
| pilot-04 | missing cache-miss behavior | 0.06 | 0.97 | 0.84 |
| pilot-05 | sufficient; false missing proposal | 0.92 | 0.02 | not asked |
| pilot-06 | sufficient; false missing proposal | 0.92 | 0.02 | not asked |
| pilot-07 | passages aligned; no contradiction | 0.85 | 0.03 | not asked |
| pilot-08 | deployment-specific value unavailable publicly | 0.06 | 0.97 | 0.42 |

The values agree with the packet's assistant-authored best-effort references.
Those references and the proposed gaps are not independent gold, so this is
agreement on a small constructed set, not an accuracy estimate. There is no
contradiction-positive case. The one aligned-passage negative control does not
measure contradiction detection. The deployment-specific case shows the
stages can express different judgments: the proposed detail was classified as
absent from supplied excerpts, while public addressability had a 0.42
yes-probability. That value's binary argmax is no for this exploratory record;
it is not a calibrated threshold or a general rule.

The reported fields are proposition-specific yes-probabilities. In particular,
stage 1's probability is not confidence in a search decision. The fixed binary
argmax only controlled whether the dependent stage-4 question was asked. No
query was generated or dispatched, and there is no observed retrieval, answer
quality, evidence gain, savings, or user benefit. The deterministic caller
policy was not evaluated. This pilot therefore validates that the frozen
request sequence can run and return parseable probabilities, not that Jev
improves continuation decisions.

Usage was 25,222 input and 423 output tokens across 19 requests. Median
provider latency was 197 ms for stage 1, 188 ms for stage 3, and 220 ms for
stage 4. These are pilot measurements only. All source evidence was public
project documentation and no evaluator labels or complete source blobs were
sent in a provider request.

This pilot does not close issue #370: its issue-level requirement to replay a
bounded follow-up set and compare actual distinct evidence contribution
against decision-only recommendations remains unmet. It also does not
substitute for held-out, independently reviewed tasks. The next worthwhile
study is a separately frozen replay using external public research questions
and evaluator-only acquired follow-up pages; report zero newly dispatched
searches and make no search-savings claim. No production integration is
justified by this pilot.
