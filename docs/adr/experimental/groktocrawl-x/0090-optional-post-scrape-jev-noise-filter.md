# Optional Post-Scrape Jev Noise Filter

- Status: accepted for opt-in implementation in the experimental fork
- Decider: Magnus Hedemark
- Date: 2026-09-21
- Scope: research-agent synthesis in `magnus919/groktocrawl-x`; not mainline deployment
- Related: [issue #371](https://github.com/magnus919/groktocrawl-x/issues/371), [ADR-0089](0089-bound-browser-concurrency-per-effective-cpu.md), [ADR-0064](../../0064-one-final-research-synthesis.md)

## Context

The research agent can receive successful HTTP responses containing only site errors, navigation, or other text that cannot contribute to the user's question. A character count does not establish usefulness. In two frozen real-search batches, Jev scored six obvious non-content pages at 0.02–0.06; all useful or plausibly contextual pages scored higher. In the complete-page synthesis replay, the unfiltered model already ignored those six pages, so an answer-quality improvement was not demonstrated. The decider nevertheless values removing that noise before synthesis and explicitly accepts it as sufficient reason for this opt-in feature.

The earlier Jev #360 route policy was rejected. This decision is narrower: assess material contribution of **already-scraped text to a composite answer**, not a binary claim that a page fully answers the query. It does not replace source trust, prompt-injection handling, or exact claim-to-passage verification.

## Decision

When `TYPESAFE_API_KEY` is present, the research agent asks pinned Jev `jev-1.13.0` for a continuous material-contribution score for every newly acquired page. The entire acquired page is assessed, in overlapping chunks when necessary; a page's score is the maximum chunk score because one useful facet suffices. A source with a valid score **below 0.10** is omitted from synthesis. This deliberately conservative, configurable starting threshold reflects the observed gap between six obvious junk pages and the next scored result; it is not a calibrated optimum or a claim of validated generalization. Uncertain pages remain available to the language model.

The score is included as `material_contribution_score` in retained source details and the synthesis context. The language model is told that the score estimates possible contribution only; it is not confidence in accuracy, authority, safety, or support for a specific claim. Internal/private or unresolvable source hosts are not sent to TypeSafe. A provider error, timeout, malformed response, or incomplete chunk assessment fails open for that source. Without a key, no Jev call or score metadata is added, and the existing research path remains available.

This filter runs **after** scraping. It does not introduce a source count cap or pre-scrape source selection. Existing acquisition breadth and operational concurrency policies remain separate work; this ADR does not assert that all returned search results are already scraped by today's agent path. The opt-in implementation is confined to the research-agent synthesis path; the answer endpoint and other surfaces are not implicitly changed.

## Consequences and follow-up

- Jev calls add latency and send the user query plus scraped public page text to TypeSafe when the operator supplies the key. Operators must choose this opt-in knowingly; keyless deployments make no Jev request.
- Retained Jev-assessed pages reach synthesis in full, avoiding the research path's historical first-8,000-character source projection. Provider context limits and latency remain real operational constraints, not reasons to label a source irrelevant.
- Filtering obvious junk reduces source-context noise, but the two-batch replay does not prove an answer-quality or latency gain. Scores are not a citation verifier; issue #372 remains relevant.
- Continue issue #371's held-out calibration, missed-value audit, incumbent comparison, and all-result scrape work. Revise or disable the threshold if any required contribution is lost. Do not promote this experimental decision to mainline solely from these batches.
