# Jev contribution retention: supply-chain search batch (2026-10-03)

**Disposition: revise; no new activation.** On this one frozen batch, the
existing ADR-0090 filter retained every page labeled as a possible material
contributor. The filter excluded nothing, so this is a retention result rather
than evidence that filtering improves answers. The keyless 8,000-character
projection separately clipped or omitted six labeled passage spans. A bounded
synthesis replay produced no usable complete answer, so the projection loss and
any score-metadata effect on answers remain unmeasured.

## Frozen packet and acquisition

The query was “software supply chain provenance SLSA reproducible builds
attestations verification.” The captured SlopSearX response (`ssx-4ba02ef6`)
contained 20 returned results, was uncached, and marked partial. The response
had no Jev rerank explanation and no deployed search revision, so the packet
cannot establish that the reviewed upstream reranker was active. It is an
observed response from the supplied snapshot, not a deployment audit.

All 20 distinct result URLs were attempted once through the public scrape API.
All 20 returned nonempty Markdown, from 3,183 to 32,808 characters (327,605
characters total). Every scraper quality score was 1.0 and all four checks
(block detection, boilerplate, completeness, and volume) passed. These are
scrape-quality checks; they do not establish source authority or factual
correctness. The returned cards had no explicit DOI, PMID, PMCID, arXiv,
publication, work, or version identifiers. Exact content hashes showed 20
unique acquired pages; pages from the same standard or publisher still do not
count as independent corroboration.

The full input snapshot and pages remain in the owner’s private temporary
ledger. The committed label freeze stores source URLs, result/page hashes,
lengths, and offset spans, without page text. It is bound to protocol commit
`d46f852`, runner commit `9c29cd5`, and label commit `fc54186`.

## Jev assessment

Pinned `jev-1.13.0` assessed every complete page once, sequentially. All 20
calls returned valid scores. Scores ranged from 0.83 to 0.98 (median 0.97,
mean 0.958); all exceeded the existing 0.10 threshold. Thus the current rule
retained all 20 pages, including all 19 pages with a best-effort positive
contribution label. One page with an uncertain version-status claim was also
retained. There were no missed positive pages in this sample.

The 20 requests reported 79,886 input and 460 output tokens. Provider-reported
call latency had a median of 204 ms and a p95 of 226 ms; these are descriptive
per-call figures, not end-to-end filter latency or a concurrency benchmark.
The provider did not report a charge. Every page fit in one Jev chunk, so this
batch does not test the multiple-chunk maximum-score bias documented in the
protocol.

Labels were a single reviewer’s best-effort audit, not independent gold
judgments. They treat contribution, accuracy, source trust, and safety as
separate questions. The one uncertain status claim is preserved as uncertain;
its truth was not resolved here. Injection safety was not assessed.

## Missed-value and projection audit

All 28 labeled candidate-contribution spans are available in complete text and
remain present after the Jev filter because it removed no pages. In the
keyless 8,000-character projection, 22 spans are fully present, one is cut
partway through, and five begin after the projection. The five late spans are
on the [build-provenance explainer](https://www.encryptionconsulting.com/slsa-build-provenance-and-signing-in-ci-cd-pipelines/),
[verification walkthrough](https://www.systemshardening.com/articles/cicd/slsa-build-provenance/)
(two spans), [first-party draft specification](https://slsa.dev/spec/draft/build-provenance),
and [build-provenance guide](https://www.kusari.dev/learning-center/build-provenance/).
The partially clipped span is in [a provenance limits analysis](https://www.ostering.com/slsa-and-provenance/).
The draft specification’s labeled verification passage begins at character
20,890. These are pre-Jev, best-effort labels. They show projection loss in
this corpus, not that a generated answer would necessarily use every passage.

The registered synthesis replay was attempted but yielded no usable answer. A
pre-call setup error failed before an output directory, call journal, or provider
request existed; after correcting the local import path, one request for the
full-text/no-score arm reached the configured `free` alias and returned
`finish_reason=length` for a request with `max_tokens=1600`. The response was
not a complete answer. Usage and latency were not retained, so generated-token
count and timing are unknown. The earlier accidental proxy attempt has unknown remote
delivery; together, the study has two external-call attempts, no retries, and
no usable synthesis output. The remaining two distinct arms were not attempted.
Because the filter removed no source, its arm and score-only arm are identical
in content and score metadata; the comparison is not measured. Answer quality,
citation support, projection impact, and score-metadata effect remain
unmeasured. See the [synthesis attempt outcome](synthesis-attempt-outcome.md).

## What this supports

Keep ADR-0090’s current experimental behavior unchanged. This batch supports
that the 0.10 rule did not discard any pre-labeled contribution in this
particular 20-result packet. It does not establish general recall, useful
source yield, answer improvement, safety, or production readiness. Any future
quality comparison requires a separate registration and approved budget, records
the deployed search revision and rerank explanation, independently reviews
labels, and completes with a synthesis output that can be graded. This study
makes no further provider calls. Preserve the full-text and keyless-projection
comparisons as separate questions.

## Issue-scope disposition and evidence boundary

**Research-spike exit: revise; no new activation.** This result answers only a
narrow first-batch retention question: on one partial 20-result search snapshot,
19 pages received best-effort positive contribution labels and all 19 were
retained by the existing rule. It does not support general recall or an
answer-quality benefit. ADR-0090 remains opt-in and unchanged.

| Requested question | Evidence in this packet or repository | Disposition |
|---|---|---|
| Acquire every distinct result returned, without the retired local source quota | All 20 URLs in the partial search snapshot were attempted and yielded Markdown. The separate direct-scraper run attempted the fixed 34-URL pool. ADR-0092 and `tests/service/test_research_scrape_all.py` cover no-slice/deduplication and continuation after a mocked failure. | Demonstrated for the 20-result packet and a separate bounded direct-service pool; not a live end-to-end 25–40-result agent acquisition test. |
| Does the existing post-scrape filter retain useful contributions? | 19 positive and one uncertain page; all 20 were retained. There were no excluded pages, no independent label reviewer, and no labeled complementary or contradictory source. | Narrow recall observation only; negative-page behavior and broader recall remain untested here. |
| Does filtering improve answer synthesis on the current upstream search baseline? | The snapshot was partial, with no rerank explanation or deployed revision. The current synthesis replay yielded one truncated full-text/no-score response and no usable answer; the other two distinct payloads were not attempted. The filter and score-only inputs are identical because nothing was excluded. ADR-0090 records an earlier complete-page replay where the unfiltered model ignored six obvious junk pages; that historical replay is not this baseline or a measured uplift. | Unanswered. Do not claim uplift or no uplift on the current baseline. |
| Does the keyless 8,000-character projection lose useful evidence? | Of 28 labeled candidate spans, 22 were present, one was partial, and five were beyond the prefix, including a frozen late-passage control. The only synthesis response was truncated and unusable. | Evidence of input projection loss in this corpus; answer impact is unmeasured. |
| Does acquisition remain acceptable at 25–40 sources under mixed latency/failure, cold/warm cache, cancellation, concurrent jobs, and progress reporting? | The direct scraper slice replayed the same 34 URLs at widths 1, 3, and 5 (width 1 once, widths 3/5 twice): 165 HTTP 200 responses and the same PDF result returned HTTP 502 in all five passes. Cache state was not observable. Fake-client unit tests cover 24 results, continuation after mocked failure, deduplication, and maximum five in flight; lifecycle contract tests exist. | A bounded direct-service observation only. It does not establish cold/warm behavior, width selection, end-to-end queue/progress/cancellation, concurrent jobs, controlled timeouts, or resource contention. |
| Are chunking, threshold failures, version/work identity, source complementarity, contradictions, and safety adequately tested? | All pages fit one Jev chunk; every score exceeded 0.10; result cards had no explicit work/version identifiers; the label freeze has candidate-contribution spans only, including one uncertain status label. Injection/safety was not assessed. | Multi-chunk maximum-score bias, below-threshold recall, explicit duplicate-version handling, complementary/contradictory evidence, and injection behavior remain open. |

A future quality comparison would need a prospectively frozen current-baseline
batch, acquired without a local source quota, that includes a below-threshold
page and independently reviewed required contribution spans (otherwise the
filter-removal contrast is not identified). On identical acquired Markdown,
prompt, synthesis model, and settings, compare full text without Jev, full text
with scores but no removal, the existing filter, and the keyless 8k projection.
Freeze source/work/version labels and the answer rubric before Jev or synthesis
outcomes; verify actual deployed reranking; include complementary, contradictory,
and post-8k evidence. Such work requires a new registration and call budget. If
the batch has no below-threshold source or complete input exceeds a documented
context limit, report the comparison as non-identifying/blocked and stop rather
than manufacturing a contrast or silently truncating text. The alias metadata declared `max_input_tokens=1,048,576`; this is not
empirical routed-capacity proof. The attempted full packet was within that
declaration, but the only response returned `finish_reason=length` for a request
with `max_tokens=1600`. No request was made with more than the 20 frozen pages.

The follow-on [GroktoCrawl direct-scraper capacity probe](exp036-capacity-outcome.md)
first preserved 102 setup failures as zero-credit transport evidence. Its
corrected R1 run stopped after 22 attempts at the first HTTP 502; R2 then
completed all five prospectively fixed passes over the same 34 URLs, with 165
HTTP 200 and five HTTP 502 responses for the same PDF page. Per-request latency
was measured, but whole-sweep duration, cache warmth, queue/resource saturation,
and agent lifecycle behavior were not. Counter movement remains unattributed.
The experiment therefore does not justify a production width or capacity claim.
The corrected protocol and outcomes are recorded in the [R1 transport
addendum](../../jev-retention-capacity-2026-10-03-transport-addendum.md), [R2
registration](../../jev-retention-capacity-2026-10-03-r2.md), and [capacity
outcome](exp036-capacity-outcome.md).
No filter activation or deployment follows from this report.
