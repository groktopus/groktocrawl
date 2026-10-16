# TypeSafe Jev spike research log

## 2026-09-20 — preflight implementation

- Confirmed issue #360 remains an exploratory GroktoCrawl X spike.
- Recorded the separate SlopSearX engine-routing result as prior motivation only.
- Selected passage routing as the first candidate because it preserves the same
  constrained-choice shape without changing search-engine behavior.
- Added a direct HTTP adapter rather than an SDK dependency or `LLMClient` mode.
- Added strict response validation, bounded response size, HTTPS-only endpoint
  validation, a two-second default timeout, sanitized fallback reasons, and
  content-free receipts.
- Added deterministic fixtures for absent key, success, authentication,
  validation, rate limit, overload, generic provider failure, timeout, malformed
  JSON, incomplete answers, and inconsistent choice probabilities.
- Added twelve exposed synthetic calibration cases and a bounded runner.
- Verified the endpoint, typed response contract, pinned model, published limits,
  pricing, retry guidance, and public data-handling terms against current TypeSafe
  documentation; recorded them as vendor claims rather than measured evidence.
- The isolated checkout has no `TYPESAFE_API_KEY`; no live provider call was made.

## Initial open gates (resolved or bounded below)

- Confirm the account-specific legal/data-retention/publication terms before
  sending any non-synthetic GroktoCrawl content.
- Prepare a representative private frozen corpus and, if possible, obtain an
  independent labeler/adjudicator; absent that, retain the quality caveat.
- Freeze practical-effect, recall-loss, latency, cost, and provider-failure gates.
- Execute and retain the private matched comparison.
- Record adopt-one-use, revise, or reject/defer without inferring broader value.

## 2026-09-21 — bounded live technical smoke

- The owner supplied a TypeSafe key outside the public repository. Its local
  permissions were restricted to owner-only before use; the key was not printed,
  committed, or placed in a GroktoCrawl request body.
- A private technical-smoke freeze captured the pinned code/model/corpus, exact
  case order and digests, 12-call limit, two-second per-call timeout, two-way
  concurrency, no-retry policy, synthetic-only input class, and $1 ceiling.
- The exposed synthetic corpus was exercised once in shadow mode. Detailed
  provider receipts and the best-effort disagreement review remain in an
  owner-only local evidence directory outside Git. No Brave Search call or
  user-visible GroktoCrawl behavior change was involved.
- This was not the representative matched comparison required by #360. The
  assistant performed the preliminary review at the owner's request; it is not
  independent or blinded. No provider-specific performance figure is published
  here, and no confidence threshold or adoption decision follows from the smoke.
- The next study needs a revised route rubric, representative frozen cases,
  explicit incumbent arm, and predeclared quality/recall/cost gates. Any quality
  conclusion without a separate reviewer must be marked provisional.

## 2026-09-21 — matched comparison stopped; no ship

- A private synthetic scenario comparison froze its corpus, self-reviewed labels,
  calibration/validation split, threshold algorithm, repetitions, cost ceiling,
  and quality/operational gates before live execution. Its material remains in
  an owner-only directory outside Git. No non-synthetic content was submitted.
- The first local attempt encountered sandbox DNS failure and was retained as a
  separate failed ledger. A network-enabled execution then reached the frozen
  operational-failure stop during calibration, so validation was not dispatched.
  Limited diagnostics showed that individual requests can complete, but did
  not establish the batch failure's cause or reverse the stop decision.
- No quality effect, calibrated threshold, supported-answer improvement, or
  citation benefit can be claimed. Provider-specific receipts and measurements
  remain private pending publication review. The public [outcome](outcome.md)
  originally recorded reject/defer and no ADR. That final disposition was
  premature because the operational hiccup had not been diagnosed and the
  product-value test had not run. No user-visible behavior changed.

## 2026-09-21 — issue reopened; bounded evaluation completed

- The owner challenged the premature closure. Issue #360 was reopened and the
  public record corrected. The first failed batch remains part of the audit
  trail, not the final basis for deciding product value.
- Direct transport and adapter probes succeeded. A second, separately frozen
  synthetic execution used the existing cases, labels, split, and threshold
  algorithm; its validation completed and selected a conservative rule.
- The account owner confirmed that the published TypeSafe terms apply. A new
  corpus of exact public first-party GroktoCrawl X document excerpts, labels,
  repetitions, and stop gates was frozen before live calls. The synthetic rule
  was applied without retuning. No private, authenticated, personal, or
  sensitive text was submitted.
- Both public-source repetitions completed with stable actions. Required and
  premise-challenging passages were retained, but useful-evidence precision
  improvement missed its predeclared practical-effect minimum. Best-effort
  self-review of model/reference disagreements did not reverse that result;
  labels were not independent or blinded.
- The comparison missed its practical-effect gate at the synthetic-selected
  cutoff. This showed a limitation of that policy, not a final Jev verdict.
  Final-answer and citation quality were not tested. Provider-specific
  measurements and ledgers stay private. No ADR, production activation, Brave
  call, SlopSearX change, or user-visible GroktoCrawl change followed.

## 2026-09-21 — owner-directed real-score calibration

- The owner identified that the conservative cutoff had been selected on
  synthetic examples without learning how Jev's scores separate real
  GroktoCrawl passages. The no-ship disposition was withdrawn; #360 stays
  open while the cutoff is calibrated and separately validated.
- A further set of public first-party document excerpts and self-reviewed
  labels was frozen before live calls. Two bounded repetitions completed.
  The original and new real-document sets are now exploratory calibration
  material. A less restrictive score cutoff is a candidate, not a validated
  production rule; its apparent benefit on these inspected cases cannot be
  reported as held-out evidence.
- Choice and Noul outputs are continuous model scores, not calibrated source
  trust probabilities. Source authenticity/provenance and prompt-injection
  barriers remain separate. A new unseen validation set and end-to-end
  answer/citation check are still needed before an adopt/reject decision.

## 2026-09-21 — bounded public-web validation

- The real-data candidate cutoff was frozen before new model calls. The owner
  clarified that Brave charges per search request, not per result, so four
  bounded GroktoCrawl searches requested a wider result pool. One returned
  no web results. The first CLI attempts were rejected by request validation;
  a documented minimal-contract amendment produced the actual search
  results. GroktoCrawl scraping supplied public pages; failed scrapes remain
  in the private acquisition record.
- Exact public excerpts, source digests, query/passage labels, answer
  obligations, two repetitions, safety and usefulness gates, and the
  synthetic injection mutation were frozen before Jev saw this packet.
  Labels were assistant-reviewed, not independent or blinded. The packet
  was constructed rather than sampled from production traffic.
- Both repetitions retained passages labeled necessary and
  premise-challenging while removing unrelated material. Most exclusions
  were easy cross-topic or unanswerable cases, not the hard near matches.
  The separate safety gate failed: benign security guidance triggered its
  injection alert, and the injected control's Choice route conflicted with
  its injection score. One invalid response failed open by retaining its
  passage. Provider-specific measurements and disagreement details remain
  in owner-only evidence storage.
- Under the original frozen stop rule, no answer/citation comparison or
  post-hoc safety-threshold tuning followed. That combined rule remains an
  audit fact, not a valid veto on evidence-value assessment. The keyless and
  user-visible paths remain unchanged; no ADR selecting Jev.

## 2026-09-21 — owner correction to evaluation structure

- The owner identified that the harness, not Jev, must combine multiple
  scores into a decision. Relevance and injection risk are separate questions:
  a passage may be useful but still require safety quarantine. A deterministic
  precedence rule can implement that policy, but it cannot make Jev's
  injection classification itself deterministic or eliminate false positives.
- The draft reject/defer disposition based on the combined gate was withdrawn.
  The evidence-selection component passed its passage-level gate; the separate
  safety-classifier component failed. Neither implies a final ship decision,
  because supported-answer and citation outcomes remain unmeasured and the
  relevance packet overrepresented easy negatives. Issue #360 stays open.

## 2026-09-21 — relevance-only ranked-result comparison and disposition

- Two additional GroktoCrawl searches acquired public pages in returned rank
  order. One scrape failed; the other acquired pages supplied a frozen packet
  without manually planted off-topic candidates. The assistant labeled exact
  first-6k excerpts before Jev calls, marking factual disputes separately.
- The previously frozen exclusion rule retained every passage in both repeats.
  It caused no required-source loss, but it also produced no useful-evidence
  precision gain. Operational checks passed. This is a negative result for the
  tested passage-triage policy on this more natural ranked set, not a verdict
  about every possible Jev use or cutoff.
- A deterministic research-only harness check confirmed that an injection
  alert takes precedence over relevance and an invalid response returns to
  incumbent behavior. The alert also quarantined benign security guidance;
  this policy is not enabled. Safety classification and evidence value are
  reported separately.
- Final-answer and citation quality were not run with a generative model:
  control and treatment had identical source sets in the ranked packet, so
  this rule supplied no controlled input difference. No user-visible benefit
  is claimed. The [outcome](outcome.md) defers integration under #360. No
  production activation, SlopSearX change, or Jev architecture ADR follows.
