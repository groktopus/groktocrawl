# Frozen SLSA follow-up replay pilot

Status: **case packet and protocol frozen before provider calls; parent review
of references is pending. No Jev or incumbent-control call or replay has been
made for this pilot.**

Study ID: `jev-slsa-deterministic-replay-2026-10-03`  
Contract: `continuation-evidence-gap-addressability/2`  
Requested model: `jev-1.13.0`  
Frozen case packet:
[`slsa-replay-2026-10-03.case-packet.json`](slsa-replay-2026-10-03.case-packet.json)  
Packet SHA-256: `01f362a5ed691a970c58169afb33ee05fc45f313eed2022f00683f929179625c`

The packet freezes four SLSA research obligations from an already acquired
public search snapshot. Its query was “software supply chain provenance SLSA
reproducible builds attestations verification”; the 20-result search response
and page-acquisition index are pinned by content digest in the packet. The
frozen first-pass evidence uses a bounded excerpt from one of the first three
ranked pages. The other 17 previously acquired pages are a hidden,
evaluator-only replay pool. The full source pages remain in the owner-held
acquisition cache; the packet records source and passage hashes, exact offsets,
and short exact evidence excerpts. No private or authenticated source material
is used.

## Questions and references

1. **Met / unnecessary-follow-up control.** The v0.1 provenance schema excerpt
   names `builder.id`; the supplied research question asks which field
   identifies the builder. A frozen false missing-information proposal tests
   whether the supplied fact is incorrectly treated as absent.
2. **Unmet / publicly answerable.** The first-pass Build Track overview does
   not explain whether level requirements extend to transitive dependency
   artifacts. The fixed replay pool includes the public SLSA FAQ and a
   separate public explanation of independent artifact ratings.
3. **Unmet / authority-check and label-completeness case.** The first-pass
   secondary excerpt says “four build levels, each building on the previous”;
   that count is compatible with the official Build L0–L3 enumeration. The
   excerpt does not supply the exact labels or authoritative version-specific
   enumeration. A later result in the fixed pool is the official SLSA v1.0
   levels page, which lists Build L0–L3. This is a missing-authoritative-check
   case; it is **not** labeled as an aligned first-pass contradiction or as a
   contradiction-detection positive.
4. **Unanswerable / private-state control.** The question asks for a specific
   organization's private signing-key rotation interval. The public corpus
   has general key-management advice but no value for a particular private
   deployment, which is excluded from search.

Reference labels and gap proposals are assistant-authored best-effort
assessments and remain pending parent review. They are not independent gold,
and the selected technical domain is not representative of all research.
Parent review is for obvious scope or label defects, not a blinded adjudication.

## Frozen sequence and decision boundaries

For each case in packet order:

1. Jev Noul judges whether the supplied first-pass evidence adequately answers
   its exact obligation.
2. A separate, frozen research-agent proposal names one specific missing item
   with a validated source span. Jev does not generate or repair gaps.
3. Jev Noul judges only whether that exact information is missing from the
   supplied first-pass excerpts. The case-03 proposal tests whether the
   version-specific authoritative level list is missing, not whether the
   secondary page itself contains a claim.
4. Only if the validated stage-3 yes-probability is greater than 0.5 does Jev
   judge whether bounded public sources could address that exact gap. This
   binary argmax is only a stage gate; it is not a calibrated threshold or a
   search instruction. An exact 0.5 tie does not advance.

The output is shadow-only. It does not formulate a query, dispatch a search,
change an adaptive-search decision, or affect any user-visible answer. The
fixed retrieved pages are replayed only after recording the model outputs.
Offline evaluators then distinguish (a) whether the frozen pool contains a
direct source span that closes the exact obligation, (b) how many distinct
result URLs contribute that evidence, and (c) recommendation-only or
unresolvable outcomes. Multiple pages are not automatically independent
corroboration. Because the result set was acquired in an earlier run, actual
new search requests and search savings are both zero and are not inferred.

## Incumbent gap-detector comparator

A separate, sequential, four-request maximum comparator arm freezes the exact
configured `agent-svc/agent/research/gaps.py::_detect_gaps` system prompt and
user prompt, including the original question, first-pass excerpt context, and
the 12,000-character cap. The context uses the same exact excerpt sent to Jev,
rendered with `SourceArtifact.to_document`'s URL/domain header. The prompt is
stored per case in the frozen packet and hashed. The prompt describes the
configured source behavior; because this isolated arm calls model alias
`free`, and because the candidate runtime/checkpoint can differ, its results
do not establish identical loop conditions, search dispatch, or production
route equivalence.

There is one call per case, sequentially, with no retry and no search dispatch.
Record requested alias, returned alias/model when present, latency, a digest
of the complete proxy response, and parse status separately. Parse statuses
distinguish valid JSON arrays, invalid JSON, invalid shape, proxy/provider
failure, and interrupted/unevaluated calls; do not map a failure to `[]`.
Record valid topic recommendations as recommendations only. Nonempty output
is not evidence gain, a verified gap, or a correct search query. This arm is
separate from Jev's 11-request cap and may use at most four additional calls.

## Call budget and failure rules

- Hard maximum: **11 Jev requests**, one in flight at a time, no retries.
  Planned allocation is 4 stage-1 + 4 stage-3 + up to 3 stage-4 calls. The
  four-case sequence and packet order are fixed; no case is selectively
  dropped after observing a response.
- If an unexpected positive on the met control makes a dependent stage exceed
  the 11-call hard cap, record the eligible stage as `not_called_budget_exhausted`
  and stop. The cap cannot be raised and no replacement request is allowed.
- Invalid, failed, or interrupted requests remain unevaluated. Downstream
  stages for that case are skipped. A runner failure receipt is distinct from
  a completed all-cases receipt.
- Earlier authorized calls used 39 of the total 50-request allowance for the
  related Jev research work. This pilot consumes at most the remaining 11; the
  combined cap is not reset or reallocated.
- Incumbent comparator maximum: **4 additional sequential calls** to alias
  `free`, with no retries; this has a separate allowance and does not authorize
  a higher Jev budget.
- The provider sees only the exact stage-specific state, question, and
  hypothesis. It receives no evaluator labels, hidden replay passages, search
  result pool, full source blobs, or private data. The runner verifies each
  serialized request against the pre-call digest.

## Reporting

Report exact calls, response validation, stage probabilities, usage and
latency if supplied, incumbent parse outcomes, and replay evidence
contribution separately. Keep
`answers[id].noul` as a proposition-specific yes-probability; do not substitute
provider confidence. A failed acquisition, provider call, or validation is
unevaluated, never irrelevant. Do not claim independent-label accuracy,
calibration, live search quality, query savings, retrieved evidence uplift,
answer/citation uplift, or production utility from this retrospective
four-case replay. Any further calls require a new signed freeze and renewed
budget review.
