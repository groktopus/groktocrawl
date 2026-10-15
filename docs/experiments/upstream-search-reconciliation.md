# Current search baseline and remaining research questions

Reviewed 2026-10-03 against SlopSearX main
[`6bb683d4a9e154186c9d76af1b55e4faa24cf0e4`](https://github.com/magnus919/SlopSearX/tree/6bb683d4a9e154186c9d76af1b55e4faa24cf0e4)
and GroktoCrawl X main
[`9d3ffcfd97059e8c50b331533abdeaef3cf8360d`](https://github.com/magnus919/groktocrawl-x/tree/9d3ffcfd97059e8c50b331533abdeaef3cf8360d).
This is a source and evidence reconciliation, not a live deployment audit or a
new answer-quality comparison. The fork remains experimental.

## What upstream already provides

- **Semantic result ordering:** the shared search service uses optional Jev
  `jev-1.13.0` Score judgments over the first 40 deterministic candidates.
  Successful advice orders that pool across general and specialist tiers;
  unscored results retain deterministic order behind it. It reorders existing
  results rather than excluding them. Explicit engine scopes can still be
  reranked. Sensitive scopes are excluded, and unavailable advice falls back.
  The source-fusion `score` is not a Jev score or factual confidence.
  See [the upstream contract](https://github.com/magnus919/SlopSearX/blob/6bb683d4a9e154186c9d76af1b55e4faa24cf0e4/docs/JEV_RERANKING.md).
- **Scholarly work grouping before fusion and reranking:** recognized DOI,
  PMID, PMCID and arXiv identities, plus explicit version relations, collapse
  duplicate publications. Titles alone never establish identity. Each engine
  contributes once per grouped work; provenance and version information remain
  internally available. Group bounds or conflicting identifiers can decline
  a merge. Public output retains compatible Paper fields, not arbitrary
  internal member records. See [the grouping contract](https://github.com/magnus919/SlopSearX/blob/6bb683d4a9e154186c9d76af1b55e4faa24cf0e4/docs/SCHOLARLY_WORK_GROUPING.md).
- **Research orchestration and monitoring contracts:** upstream also provides
  [caller-directed adaptive research](https://github.com/magnus919/SlopSearX/blob/6bb683d4a9e154186c9d76af1b55e4faa24cf0e4/docs/ADAPTIVE_RESEARCH.md),
  [staged search](https://github.com/magnus919/SlopSearX/blob/6bb683d4a9e154186c9d76af1b55e4faa24cf0e4/docs/STAGED_SEARCH.md),
  and [saved-search events](https://github.com/magnus919/SlopSearX/blob/6bb683d4a9e154186c9d76af1b55e4faa24cf0e4/docs/SAVED_SEARCH_EVENTS.md).
  These require separate MCP grants and storage; the ordinary HTTP consumer
  does not automatically use them. Adaptive research records caller-selected
  follow-ups, budgets and subquestions; the caller still judges sufficiency.
  Staged fallback requires fully observed clean-empty coverage, not timeouts or
  partial failure. Saved-search events provide durable retrieval-change reports,
  not judgment of page changes against user intent. Use these as available
  execution/receipt primitives in future integration designs rather than
  rebuilding them. Their existence does not answer #370 or #373.
- **GroktoCrawl integration already improved:** merged PR #406 preserves
  returned engine provenance and compatible scholarly fields through search
  results. Experimental research uses `limit=None` and admits every distinct
  returned URL under ADR-0092, subject to explicit caller budgets and operational
  guards. ADR-0090's optional post-scrape contribution filter is implemented;
  its retained pages reach synthesis in full. The keyless document projection
  still defaults to 8,000 characters and needs a late-passage control.

The owner's observation of materially better search is consistent with these
implemented changes. The older EXP-004/005 and EXP-016–023 studies remain historical
results on their recorded workloads and baselines. Their inconclusive outcomes
must not be generalized into a verdict against the current implementation.
The focused upstream run passed 71 reranking/grouping tests; its process exited
nonzero because the repository-wide 80% coverage gate was applied to this two-file
subset (29.77% overall coverage). This is not a full upstream CI pass.
Contract tests establish ordering, identity and fallback behavior; they do not
quantify current real-world answer improvement. Upstream's reranking guide also
explicitly limits its effectiveness claim.

## What remains for GroktoCrawl X

| Issue | Revised question | What we should not repeat |
|---|---|---|
| [#371](https://github.com/magnus919/groktocrawl-x/issues/371) | Does the existing post-scrape filter retain every useful contribution and improve synthesis on the improved search baseline? Include contradictions, complementary sources, late passages and duplicate versions. | Building another result-card reranker or treating retired acquisition quotas as the current baseline. |
| [#370](https://github.com/magnus919/groktocrawl-x/issues/370) | After the improved first pass, does another search close a specific evidence gap? Count distinct works and obligations, not engines or mirror URLs. | Re-evaluating upstream engine selection as if it were the research continuation decision. |
| [#372](https://github.com/magnus919/groktocrawl-x/issues/372) | Does the exact cited passage support the generated claim, including qualifiers, contradictions and version-specific claims? | Treating relevance, source agreement or a contribution score as proof of claim support. |
| [#373](https://github.com/magnus919/groktocrawl-x/issues/373) | Does a monitored page change matter to the user's stated intent? Distinguish actual content change from search reordering, grouping or representative changes. | Rebuilding upstream saved-search result-change reports. |

These remain follow-on studies, not unfinished W0–W13 requirements. Start with
#371's evidence-retention audit, then #372's exact support checks. Use the resulting
labeled obligations for #370; #373 can remain a separate monitoring study.

## Shared baseline requirements before new model calls

1. Record the deployed search revision separately from this reviewed source
   revision. Verify successful reranking through its explanation, and preserve
   final order, engine outcomes, cache state, available publication identifiers,
   publication/version notices and the actual returned result set. A configured
   key alone does not establish that advice was applied. Do not export keys or
   private deployment details.
2. Freeze real public search snapshots from that baseline and acquire every
   eligible distinct returned URL. Record every failure or exclusion. Preserve
   original result metadata and exact cited passages. Multiple engines or URLs
   identifying one paper are one work, not independent corroboration; do not
   invent absent work/version metadata or merge corrections by title.
3. Compare full acquired evidence without a contribution filter against the
   **existing** opt-in ADR-0090 filter on identical snapshots, model and synthesis
   instructions. Hold full-text projection constant to isolate filtering; assess
   the shipped keyless 8,000-character projection separately. The retired
   early-stop path is an optional historical negative control only.
4. Pre-label required contributions, contradictory evidence and source identity.
   Freeze calibration and validation sets. Report missed required passages,
   supported claim coverage, independent-work coverage, latency and separate
   search, scrape and Jev costs. Ranking position alone is insufficient.
5. Do not turn this reconciliation into a deployment, a new default, or an
   assumption that both installations already run the reviewed upstream code.
   Preserve the historical experiment packets and accepted ADR scope.

## October 3 follow-on evidence and decisions

The bounded complete-set upstream studies are published in
[SlopSearX PR #512](https://github.com/magnus919/SlopSearX/pull/512), merged at
`35b60dc282f47e84d462f8299312a05e400b6c9e`.
[PR #513](https://github.com/magnus919/SlopSearX/pull/513) reconciles the experiment
ledger. These are evidence commits, not reranker deployment changes.

- [EXP-034](https://github.com/magnus919/SlopSearX/blob/main/docs/experiments/EXP-034-whole-set-jev-reranking.md)
  found complete-set requests feasible on two observed pools, but shared-state
  batch scores were composition-sensitive and relevance improvement was not established.
- [EXP-035](https://github.com/magnus919/SlopSearX/blob/main/docs/experiments/EXP-035-card-local-whole-set-reranking.md)
  bound each Score question to its own result card. This reduced batch drift;
  the exposed quality sample still did not justify adoption. The constructed
  80-card probe establishes bounded request feasibility, not a natural search
  pool or production queue/deadline behavior.
- [EXP-036](https://github.com/magnus919/SlopSearX/blob/main/docs/experiments/EXP-036-heldout-complete-set-reranking.md)
  captured fresh complete pre-rerank pools and compared the same candidates.
  Mean nDCG gains across the seven valid original query pairs were +0.0104 and
  +0.0327 under the two assistant references, below the +0.05 improvement gate.
  The broad climate/cardio extension returned 38/44
  cards. Cardiac ranking regressed under both references and promoted a
  title-only academic lead above substantive supplied evidence. Only one fresh
  natural pool exceeded 40, so tail benefit remains inconclusive. One candidate
  response failed validation; partial engine coverage and disputed labels remain visible.

**Upstream disposition: retain the shipped first-40 policy.** Do not activate
whole-set advice from these findings. A promising publication to acquire and a
passage that already contributes evidence are different judgments. Future work
should evaluate them separately, after acquiring content where needed, rather
than calling an academic title weak research in itself. This is a bounded
no-adoption conclusion, not a verdict against Jev or academic sources.

### Downstream research dispositions

- **Contribution retention (#371):** [PR #410](https://github.com/magnus919/groktocrawl-x/pull/410)
  audits 20 acquired pages and 28 labeled contribution spans. The frozen filter
  retained every page; this positive-heavy corpus cannot establish specificity
  or a benefit from exclusion. The 8,000-character projection left five spans
  absent and one partial, while full text preserved all 28. This is a structural
  evidence-retention result, not an answer-quality improvement. A 34-URL direct
  scraper replay completed 170 requests at widths one, three, and five: 165
  returned content, and the same PDF failed once in each pass. Cache state and
  broader research-job capacity were not established. The synthesis comparison
  stopped on its first request at the frozen output limit; the other arms were
  not attempted, and no complete answer was graded. The earlier accidental
  attempt with unknown provider delivery remains recorded separately.
  **Revise before adoption:**
  filtering benefit, contradiction handling, chunk aggregation, and comparative
  answer quality remain unmeasured.
- **Follow-up evidence (#370):** [PR #412](https://github.com/magnus919/groktocrawl-x/pull/412)
  records the corrected staged judgment pilot. [PR #414](https://github.com/magnus919/groktocrawl-x/pull/414)
  adds four public SLSA obligations and an already acquired evidence replay.
  All 11 Jev requests validated. Two public gaps had directly contributing
  pages in the frozen pool; general advice could not supply a private deployment
  value. The incumbent gap prompt recommended topics for all four cases,
  including the already answered obligation. Jev received assistant-written
  gap hypotheses while the incumbent generated topics, so this is a descriptive
  contrast, not proof that Jev caused better decisions. No search or comparative
  answer was produced. **Revise before adoption:** the next trial needs genuine
  contradiction cases, held-out judgments, and measured answer/citation outcomes.
- **Claim support (#372):** [PR #409](https://github.com/magnus919/groktocrawl-x/pull/409)
  records 25 constructed claim/passage pairs, including 13 validation pairs.
  Neither Jev nor the adapted verifier control falsely supported any of the nine
  non-supported validation pairs. Jev was faster, but the reference labels are
  assistant assessments and the control was not the production verifier route.
  Runtime precedence, failure fallback, and unchanged generated answers were
  not tested. **Remain in shadow testing:** this sample does not establish
  incremental answer quality or integration readiness.
- **Page-change significance (#373):** [PR #415](https://github.com/magnus919/groktocrawl-x/pull/415)
  records 11 judgments over nine real public revision pairs. The seven validation
  cases agreed with their assistant reference labels at the frozen 0.50 cut;
  these are small, clustered observations, not a production accuracy estimate.
  The same invite-link edit scored 0.83 for contributor onboarding and 0.07 for
  cryptographic verification. Navigation metadata likewise scored differently
  for content and navigation interests. This supports a larger shadow study
  of intent-sensitive judgments. **Remain in shadow testing:** independent
  labels, calibrated thresholds, and a reviewed notification policy are still
  needed before changing user-visible behavior. Failed acquisition was a
  separate zero-call control, not an immaterial-change judgment.

These research dispositions do not change deployment defaults.
Provider response validity, assistant label agreement, and lower call latency
do not establish better research answers or calibrated action thresholds.

### Frozen cardiac replay follow-up (October 4)

[Issue #416](https://github.com/magnus919/groktocrawl-x/issues/416) and the [replay report](cardiac-jev-replay-2026-10-04.md) reuse all 44 cardiac cards with no new searches or page fetches. The new visible-evidence question retained ten useful cards in every tested ordering, while the old all-44 question ranged from five to nine. Fetch priority remained context-sensitive, and no useful tail promotion was demonstrated. These are exposed-corpus development findings; keep the shipped policy and separate evidence ranking from retrieval triage.
