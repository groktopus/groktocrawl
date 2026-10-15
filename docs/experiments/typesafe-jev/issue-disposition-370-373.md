# Jev follow-on research decisions: #370–373

**Decision: revise before adoption; retain existing defaults.** The bounded
studies provide research dispositions, including negative and incomplete
comparisons. Closing these research spikes does not mean their production
adoption criteria have been met. No study activates a feature or changes a
notification, publication, search, or deployment policy.

Jev returns probabilities for frozen propositions. Application code owns
search dispatch, budgets, source validation, fallback, and final action. The
reference labels below are best-effort assistant assessments, not independent
gold. Probability agreement does not establish calibration or factual truth.

## #370: deciding whether another search could help

The original Choice screen and corrected eight-case staged Noul pilot remain
separate historical packets. The subsequent [four-case SLSA replay](slsa-replay-2026-10-03.outcome.md)
returned 11 valid Jev judgments and four parsed incumbent gap arrays. Two
externally supplied public gaps had contributing passages in the fixed replay
pool; general public advice could not provide a private deployment value.
The incumbent proposed topics even for the already satisfied obligation.
This is a descriptive contrast: Jev judged carefully supplied gap hypotheses,
while the incumbent generated its own topics. Neither fresh searches nor
comparative answers were produced.

| Requirement | Evidence and remaining limit |
|---|---|
| Frozen obligations, questions and source spans | Covered in the pilot and replay packets; the staged route still lacks a positive contradiction case. |
| Same-state incumbent comparison | Exact first-pass excerpts were reused, but hypothesis judgment and topic generation are different tasks. No causal advantage is established. |
| Bounded follow-up evidence | Already acquired passages were replayed; zero new searches. Mirror URLs do not establish independent corroboration. |
| Necessary/unnecessary search and answer outcomes | No live dispatch, search-cost saving, or answer/citation improvement was measured. |
| Calibration and fallback | No action threshold was fitted; operational response validation does not validate a production policy or failure fallback. |

**Disposition: revise, with no integration.** A further trial would require
aligned tasks, contradiction coverage, independent held-out references, and
measured search/evidence/answer outcomes. It is a separate prospective study,
not an unreported extension of these packets.

## #371: retaining useful acquired evidence

The [retention audit](../evidence/jev-retention-2026-10-03/supply-chain-jev-report.md)
identified 28 contribution spans across 20 acquired pages. All pages passed
the frozen contribution rule. Full text preserved the spans; the 8,000-character
projection left five absent and one partial. Because every page was retained,
this corpus cannot establish specificity or improvement from exclusion.

The [direct-scraper study](../evidence/jev-retention-2026-10-03/exp036-capacity-outcome.md)
preserves the failed setup, partial R1, and prospective R2 separately. R2
completed 170 requests over the fixed 34-URL pool: 165 HTTP 200 and the same PDF
returning HTTP 502 once in each of five passes. Cache state and whole-sweep
wall-clock time were not retained; these per-request observations cannot select
a production concurrency width.

The frozen synthesis comparison stopped on its first R1 request when the response
reported `finish_reason=length` with a requested 1,600-token output limit.
Actual token usage was not retained. No complete answer was graded; the other two unique
arms were not attempted. The prior uncertain-delivery transport attempt and
zero-call import setup failure remain recorded. No retry or output-limit change
was made after the stop.

| Requirement | Evidence and remaining limit |
|---|---|
| Full acquisition and missed-value audit | All 20 audit pages acquired; 28 spans labeled. The snapshot is partial and does not prove current upstream reranking ran. |
| Filter versus unfiltered synthesis | Identical membership on this positive-heavy corpus; the attempted answer comparison failed. Filtering benefit remains unmeasured. |
| Long-page handling | Structural late-span loss is demonstrated; query-aware chunk aggregation and answer/citation benefit are not. |
| Operational acquisition | Fixed-pool scraper replay observed mixed tiers and a repeated page failure; controlled slow/timeout, resource-queue, cancellation, progress, and multiple research jobs were not tested. |
| Contradictions and complementary evidence | Contribution annotations are retained; comparative synthesis handling was not established. |

**Disposition: revise, with no new activation or width selection.** Preserve
ADR-0090 and ADR-0092 scope. A future trial needs a registered synthesis budget,
a meaningful mixed-value corpus, and measured research-job lifecycle behavior.
This study does not qualify those adoption requirements.

## #372: support for an exact claim in an exact passage

The [claim-support report](exact-claim-passage-report.md) compares 25 constructed
pairs, split into 12 calibration and 13 validation cases. Jev and the adapted
verifier control each falsely supported zero of nine non-supported validation
pairs. Jev was faster on this packet. The control was not the actual production
verifier route, and no unchanged generated-answer comparison was run. Annotated
hard negatives did not exercise runtime precedence or combined gates.

**Disposition: revise; remain shadow-only.** Representative unchanged
answer/citation pairs, the production verification route, independent labels,
and runtime precedence/failure tests are needed before integration. This packet
does not establish incremental answer quality or a safe publication decision.

## #373: whether a page change matters to a user's interest

The [external revision-pair study](../evidence/jev-change-373/outcome.md) keeps
the earlier six-case pilot separate. Nine real public revision pairs produced
11 pair-by-intent judgments: four calibration and seven validation. All requests
validated. At the frozen 0.50 cut, all seven validation judgments matched their
assistant references: zero false-immaterial among five material cases and zero
false-material among two immaterial cases. The same invite-link edit scored
0.83 for onboarding versus 0.07 for cryptographic verification; navigation-weight
scores also differed by intent. Repeated diffs and sources are clustered
observations, not independent cases or a production accuracy estimate.

**Disposition: revise; remain shadow-only.** Genuine version/status, removal,
formatting, navigation and ambiguous cases improve coverage, but contradiction,
price/date, template/advertising churn, independent labels and calibrated
notification policy remain unqualified. Failed acquisition is an unevaluated
zero-call control. Deterministic source identity, diff reporting and notification
behavior remain authoritative; no notification was suppressed.

## What this closeout permits

The research spikes can close with these reproducible revise/no-adoption
findings once their evidence is merged. The [upstream reconciliation](../upstream-search-reconciliation.md)
records why complete-set reranking also remains unadopted on the bounded quality
results. Any future adoption study needs a new frozen protocol and explicit
coverage of the unmet requirements above. The original W0–W13 roadmap stays
complete; these findings neither reopen it nor expand accepted ADR scope.
