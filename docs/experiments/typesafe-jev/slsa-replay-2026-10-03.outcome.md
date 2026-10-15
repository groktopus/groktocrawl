# SLSA bounded continuation replay: outcome

Protocol and case packet were frozen before calls (signed freeze `1f0fa03`, corrected packet/protocol `9c9c04f`, execution safeguards `af95c6b`). The four-case packet SHA-256 is `01f362a5ed691a970c58169afb33ee05fc45f313eed2022f00683f929179625c`. The public search snapshot contains 20 acquired pages; first-pass state uses excerpts from its first three results and the remaining 17 pages are evaluator-only.

## Execution

The shadow Jev arm completed **11/11** sequential requests; all responses passed request/model/schema validation and returned `jev-1.13.0`. No retry or search request occurred. Actual search calls dispatched: **0**. The 11 requests used 11,354 input and 243 output tokens. Median response latency was 203 ms for sufficiency (4 calls), 172 ms for missing-information review (4), and 204 ms for public addressability (3). Provider token usage and elapsed time describe these requests only; no monetary cost was returned.

The separate incumbent control completed **4/4** sequential calls using alias `free`; all four responses parsed as arrays under the existing incumbent parser. The returned alias/model was `free`; the underlying checkpoint is unknown. The harness saved response digests and parsed topics, not raw provider responses. No searches were dispatched. Calls took 7.3–14.6 seconds each. Each topic list is a recommendation, not a verified gap, evidence, or proof that a query would retrieve useful material.

## Case outcomes

| Case | Frozen best-effort reference | Jev yes-probabilities (sufficiency / gap missing / public addressability) | Fixed-pool replay evidence | Incumbent topic output |
|---|---|---|---|---|
| Builder identity field (already supplied) | Met; no further evidence needed | 0.84 / 0.11 / not called | No additional page contribution expected | Five broad expansion topics despite the excerpt already naming `builder.id` |
| Transitive Build requirements | Unmet but publicly answerable | 0.05 / 0.97 / 0.85 | Existing rank-07 official FAQ and rank-12 secondary page directly address independent artifact assurance. Two distinct URLs contribute; this does not establish independent corroboration. | Five targeted follow-up topics about transitivity and dependency levels |
| Official SLSA v1.0 labels | Unmet authority/label-completeness check; the first-pass secondary excerpt's “four build levels” count is compatible with L0–L3 | 0.09 / 0.96 / 0.90 | Existing rank-11 official levels page lists Build L0–L3. One distinct page supplies the authoritative enumeration. This is not a contradiction-positive case. | Five generated query topics include an unsupported L1–L4 phrase. This is a query recommendation, not an observed claim or answer. |
| Private signing-key interval | Public evidence cannot supply the organization-specific private value | 0.03 / 0.98 / 0.27 | General key-management advice in ranks 13 and 16 does not establish a particular private interval; no direct answer in the bounded public pool | Five generic key-rotation topics; they do not resolve the requested private value |

These are probabilities that each exact Noul proposition is true, not model confidence. The 0.5 binary gate was frozen as an experiment control and was not tuned here. Under that gate, the two public missing obligations pass to addressability and are judged addressable; the met control stops before addressability, and the private-value case is judged not publicly addressable. This is a small assistant-labeled feasibility screen, not accuracy, calibration, or a validated search policy.

The replay audit and the model recommendation arm are separate. The two cases with positive Jev addressability also had direct supporting pages in the already acquired pool. This does not show that Jev caused their retrieval, added evidence, or improved an answer. Jev judged externally supplied hypotheses; the incumbent had to generate topic suggestions, so this is a descriptive same-evidence contrast, not a matched comparison or causal Jev advantage. Search and answer uplift were not run. The incumbent topics are not used to redefine what the fixed pool contains.

## Issue #370 disposition

| Acceptance item | Result |
|---|---|
| Freeze public cases, first-pass evidence, obligations, questions, and boundaries before calls | Met for four bounded SLSA cases; reference labels are assistant-authored best effort, not independent gold |
| Cover met, unmet, contradictory, and unanswerable states | Met control, two unmet cases, and private/unanswerable case included; no aligned contradiction-positive case, so that stratum remains untested |
| Compare incumbent and shadow decision on identical evidence | Partial: exact incumbent gap prompt and Jev judgment stages used the same excerpts, but incumbent generates topic suggestions while Jev judges an externally supplied gap hypothesis; these are not equivalent tasks or a causal comparison. The alias checkpoint is unknown |
| Replay follow-up evidence and report distinct contribution | Met as deterministic review of an already acquired 20-page pool; case 2 has two contributing URLs, case 3 one, case 4 none; this is not a fresh search experiment |
| Count actual search calls and savings | Actual calls were zero. Search savings and marginal retrieval contribution are unmeasured |
| Missed necessary and unnecessary searches, answer/citation change | Not established: the shadow arms did not dispatch searches or produce a comparative answer/citation |
| Separate calibration from validation | Not applicable to this feasibility screen; no threshold selection or tuning was performed |
| Latency, cost, and failure fallback | Jev latency and tokens recorded; monetary cost and failure fallback were not evaluated because no calls failed |

**Recommendation: revise and validate on new held-out cases before any adoption.** The pilot supports further evaluation of decomposed sufficiency, explicit externally generated gap review, and bounded public addressability. It does not support production adoption or claims of search savings or answer uplift. The incumbent control's extra topics on the met case and incorrect L1–L4 suggestion show that a nonempty topic list is not itself a correct continuation decision.

## Scope limits

There are four cases from one technical domain, assistant-authored labels, and no independent blind adjudication. Only the three included evidence strata were covered, with no aligned contradiction positive. Replay used previously acquired search pages rather than live retrieval, and no search or synthesis response was compared. The arms perform different functions: Jev receives the frozen proposed gap, while the incumbent generates topics. The free alias's underlying checkpoint is unknown. No actual search was dispatched, no search charges or savings were measured, and no production behavior was changed.

The exact per-call probabilities, request/response digests, usage, and parsed incumbent recommendations are in [`slsa-replay-2026-10-03.results.json`](slsa-replay-2026-10-03.results.json). Raw provider responses were not committed.
