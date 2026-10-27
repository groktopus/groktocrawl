# First live screening gate: two public cases, three arms, one repetition

Status: adapter implemented and offline tested; **NOT READY for question admission
or live dispatch**. The 24-attempt proposal in the original protocol is superseded
for the first screening. It was never executed. Expansion needs a separate decision
after accounting and isolation succeed; this screen cannot establish policy uplift.

Parent has independently frozen two public practical cases and keeps their source
truth and labels in a separate cloud workspace. No question or label has been
received by this candidate editor. Do not access or infer parent label paths.
Freeze the complete runnable candidate and arm mapping before receiving questions;
make no candidate edits between receiving them and closure of independent grading.
The existing ADR-0076 identity/tool separation requirement remains documented in
`evaluator-handoff.md`; parent must verify the chosen evaluator arrangement.

## Concrete enforced dispatch boundary

`agent.experimental.calibration_budget` is isolated experiment code, not imported
by any application route and not deployed. Its defaults and hard ceilings are:

| Quantity | Per attempt | Six-attempt aggregate ceiling |
|---|---:|---:|
| Search HTTP dispatches | 2 | 12 |
| Lightweight acquisition HTTP dispatches | 4 | 24 |
| Model HTTP dispatches | 3 | 18 |
| Total HTTP dispatches at this boundary | 9 | 54 |
| Requested model output tokens | 256 per dispatch | 4,608 reserved tokens |
| Serialized model-request UTF-8 bytes | 12,000 per dispatch | 216,000 bytes |
| Response bytes | 1 MiB per dispatch | 54 MiB |
| Attempt duration | 45 seconds | at most 270 seconds active attempts |
| Experiment wall deadline | — | 300 seconds |

One repetition means one attempt for each case/arm combination, not a claim of
statistical replication. Arm order must be retained and counterbalanced. Equal
ceilings apply to all arms; actual usage is reported without filling unused slots.
There is no automatic retry, replacement case or expansion. A hard-boundary failure,
ambiguous operation, missing model usage or changed/missing gateway retry receipt
halts the entire screen. Failures remain in the attempted denominator.

Reserve and fsync an operation receipt **before** sending. The journal uses exclusive
creation; a prior journal cannot be overwritten or automatically resumed. A crash
with a reserved operation yields `outcome_unknown`, never an automatic replay.
Timeout/cancellation/disconnection does not confirm server cancellation. An uncertain
operation remains unresolved and globally prevents another dispatch in that run.
A caller catching an exception cannot mark a latched stop as a completed success.

Use fresh configured upstream clients in an isolated process. The installer wraps
both default and existing proxy-mounted httpx transports without discarding proxy
routing. It rejects underlying HTTP transport retries or an unknown transport.
Do not install into a shared running API process. Exact service endpoints come
from existing configuration, never from case questions. Endpoint changes,
unsupported acquisition formats/options, model route changes and sampler changes
are rejected before sending. Credentials remain in the existing client internally;
receipts contain hashes and allowlisted status/usage metadata, never auth headers
or raw question/source bodies.

Acquisition requires the existing `/scrape` internal client contract with
`lightweight_only=true` and markdown only. `/v2/scrape` is deliberately rejected
because it does not forward that bound. No browser/robots override or recovery
fallback is authorized. A frozen blocked-source hash set must include all four
unresolved URLs in `capture-reconciliation.json`; no repeat is admitted without
an independent terminal receipt. Source acquisition must preserve all existing
SSRF, DNS, robots, access and barrier controls.

## Opaque backend accounting: not falsely closed

The 54 bound counts service HTTP sends, including retries initiated by the caller.
It does **not** claim to count every Internet request inside SlopSearX or the scraper.
Their HEAD/llms/content negotiation and engine fanout need separately bounded,
recorded contracts. Model bodies receive request-local `num_retries=0`,
`max_retries=0`, `disable_fallbacks=true`, and `max_tokens=256`; production settings
are not changed. Returned router retry/fallback headers must both equal zero.
Absent/nonzero headers or missing token usage halt the run.

Read-only gateway inspection found global `num_retries=4`. Installed Router source
supports a request-level zero, but a configured retry policy can override it. The
inspected YAML has no retry-policy override. Provider SDK source also accepts
`max_retries`, but contains a separate two-iteration message-format retry loop.
The proxy's actual forwarding of these request fields and all lower-layer attempts
have NOT been verified by a live call. Do not claim 18 exact provider dispatches
or complete all-token accounting from successful gateway usage alone. A conditional
bound of two provider-format attempts per gateway dispatch would be 36 attempts /
9,216 requested output tokens, **only after** the zero-retry/fallback contract and
absence of other overrides are independently established. This is not yet an
admitted provider-wide cap.

Every response labels its accounting scope `service_http_boundary` and retains
`accounting_complete=false` for total cost, with a separate boundary-completeness
flag. Successful response prompt/completion usage is retained; absent data stays
null. Raw request and response hashes bind the receipt to parent-retained artifacts.
The free route declares $0 token prices, but actual billing, discarded retry usage,
local compute and search/fetch cost remain unknown. No new paid service or paid
route may be used. Stop on any route or price change.

## Arm implementation/readiness boundary

The current legacy entry point and focused fixed diagnostic control exist upstream.
All arms would need the same request-local output cap, 4k evidence context profile,
model/sampler, initial question, source access controls and acquisition budget.
A bounded screen is not an estimate of uncapped production latency/quality.

W12.4's existing `execute_policy` is a deterministic replay boundary that consumes
candidate acquisition/materiality/closure inputs. It is **not** a live retriever.
A reviewed live `obligation_bounded` adapter has not yet been provided. It must
obtain a named important missing fact without parent gold labels or universal
mission normalization; bind one follow-up to it; admit only genuinely new canonical
material evidence; and stop on closure, first no-gain query or the frozen cap.
Do not use hidden labels to author runtime closure, relabel `w10_diagnostic` as the
current route, or count mere changed bytes/lexical overlap as verified material gain.
No six-arm execution can be admitted until that adapter, complete runner artifact,
and the backend/accounting contracts are independently reviewed and frozen.

Thus the dispatch adapter is ready for review/offline integration, while full
three-arm live execution is explicitly not ready. Parent should withhold questions
until `screening-handoff.json` readiness blockers have been closed. This packet is
not permission to relax acceptance criteria or manufacture a live winner.

## Checks completed

37 budget-boundary tests exercise pre-send reservation, exclusive journal recovery,
concurrent oversubscription, timeouts, cancellation, missing usage/receipts,
request-local caps, preserved proxy mounts, forbidden endpoint/model/format changes,
previous unresolved-source refusal and the real upstream LLM client under fixtures.
The combined gap/source/budget/obligation regression selection has 87 passes.
Ruff and focused mypy pass. No live source/model request was issued by these tests.

## Correction candidate after CI diagnosis

Correctness follow-up `d36cc84` supersedes the original repair as the current
correctness candidate. It uses the existing structured-output request contract for
gap detection; no protected twin/evaluator/corpus file was changed. Its focused
115-test selection and unchanged broad answer-eval baseline pass as documented
in `ci-diagnosis.json`; Compose checks for the repaired head are still pending.
The budget adapter remains frozen at `80164b6`. No parent questions were received
and no candidate was tuned on held-out inputs during this follow-up.
