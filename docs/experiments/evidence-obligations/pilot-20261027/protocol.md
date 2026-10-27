# Bounded calibration pilot, 2026-10-27

First-screen budget superseded by [screening-protocol.md](screening-protocol.md) and
[screening-handoff.json](screening-handoff.json). The larger proposal below is historical
and was not executed or admitted.

Status: development only; live comparison blocked, no promotion decision.
Parent: W12.4. This extends its calibration record, not its evaluator or runtime
policy. Repository baseline: upstream `groktopus/groktocrawl` at
`8df43e5dbe029a888a2007d9dcf0baf4869657dc`; runtime image revision `9ee3495`.
The personal fork is not an execution or code source.

## Authority and scope

Owner approved the bounded pilot. No production restart, deployment, configuration,
default, credential, access-policy, evaluator or reference-answer changes.
Correctness repair is separate from policy efficacy. The candidate editor can
write only this isolated checkout. No optimizer has been implemented or run.

Shared installed skills were read from the magnus919 plugin version 1.0.0:
harness-engineering, agent-evals-and-observability, systematic-debugging and
verification-methodology. The GroktoCrawl skill supplied client/source guidance.
No graphify graph is present. ADR-0064, ADR-0068, ADR-0076 and the archived W10 /
W12.4 contracts constrain the work. W12.1's rejected universal normalization and
W10's negative broad-adaptive result remain binding historical evidence.

## Named comparisons and separation

- `current_route`: deployed legacy `/v2/agent` research loop. Its gap detector is
  query-aware, selects 12k characters, and synthesis selects full-source passages.
- `fixed_diagnostic_control`: same acquisition/model/synthesis, one discovery
  pass, no continuation. A diagnostic control, not a claim about all production
  routes or the durable experimental substrate.
- `obligation_bounded`: existing W12.4 control, continued only for one caller-named
  important missing fact. Require a new admitted canonical material source and
  actual closure evidence. Stop on closure, first no-gain query or hard cap.

Do not equate W12.4 `w10_diagnostic` with `current_route`. The existing replay's
three arms have different names and a synthetic scope. It cannot answer this live
comparison without a separately reviewed adapter and paired capture receipts.
The experimental durable route is enabled but remains a separate contract.

## Freeze and labels

`development-cases.json` contains only public/sanitized development questions and
predeclared required-fact slots, legitimate unknowns and citation criteria. No
private interview quotes, confidential employer problem or user data occurs in
external queries. These are not held-out cases. Reference slots awaiting an
actual authoritative capture are explicitly unverified; do not score them as gold.
No candidate tuning occurs before verified capture and the evaluator freeze.

A separate evaluator owner must select new questions AND sites, retain expected
values and reference passages outside candidate access, and enforce that boundary
with identities/filesystem permissions. This checkout shares editor access and
cannot enforce that separation. ADR-0076 requires holding the comparison until
that condition is met; hiding a file by convention is insufficient. After any
exposure, retire those questions to development and select a fresh decision set.

## Proposed live bound (not executed)

One candidate, zero tuning iterations. Four held-out questions at new sites,
three named arms, two repeats = 24 attempts, no discarded failures or replacement
questions. Counterbalance all six arm orders across cases/repeats using seed
20261027; preserve exact sequence. One model alias and resolved target for all
arms; equal maximum budgets, actual usage reported separately. Fresh acquisition
with cache receipts; shared frozen replay for diagnosis, independent live captures
for drift sensitivity. A changed source/model target invalidates the paired
comparison; retain the attempts and mark the affected pair inconclusive.

Per attempt: at most 3 searches, 6 acquisitions, 3 model dispatches (including
retries), 32k source-context characters, 12k gap context, 8,192 output tokens per
model dispatch, 120 seconds total. Entire experiment: at most 72 searches,
144 acquisitions, 72 model dispatches, 589,824 output tokens, 48 minutes execution
and 60 minutes wall time. Bound complete serialized input before dispatch at
64,000 UTF-8 bytes per call (a conservative input-token bound for byte-based
encodings), 4,608,000 aggregate bytes. No unattended polling, new paid service,
credential change, broad normalization or automatic fallback to another model.

These are proposed enforced limits, not claims about what `/v2/agent` currently
enforces. An isolated adapter must count internal retries and outstanding calls,
refuse dispatch beyond any cap, and retain ambiguous operation receipts without
replay. Client timeout alone is not cancellation of server-side work.
Do not run live until this adapter and evaluator isolation are established.

The deployed gateway declares input/output token prices zero for alias `free`,
resolved to `openai/ornith/ornith-1.5-122b:free`. Thus declared model-token estimate
is $0, conditional on that exact route and prices remaining valid. Actual provider
billing, local compute, search/fetch costs and editor costs are not exposed by the
inspected configuration; total dollar cost is unknown, not zero. Stop if route
or billing changes, and request an exact spending ceiling before paid dispatch.
Existing service configured limits: `max_tokens=8192`, call timeout 300s,
max searches per request 5, LLM admission 32; these are not pilot permissions.

## Measures and decision

Freeze the evaluator before candidate exposure. For each attempt record expected
fact coverage (closed slots / all expected slots), unsupported material claims,
exact cited-page and passage support, entity/date accuracy, justified abstention,
failure/timeout, end-to-end and stage latency, search/fetch/browser/model calls,
all input/output/reasoning/cache tokens and prices. Record human interventions.
Citation identity must match the exact retained page, not merely the publisher.
Report unmeasured quantities as unavailable. Record source bytes/hash/time,
query/route/model/revision, operation receipts, cache state and failure provenance.

Cost per verified useful answer = all attempt costs / independently verified useful
answers, keeping failures in the numerator and denominator of attempt rates.
If no answer qualifies or accounting is incomplete, report undefined/unknown.
Separate optimization, replay, grading, capture and serving costs; no invented
amortization or break-even claim. Do not combine deterministic synthetic records
with live scores or treat repeated identical replay as independent samples.

Use W12.4's quality gate as a preregistered screening criterion per category:
closure +10 percentage points, no additional unsupported important obligations,
precision within 5 points, post-gain queries at most 10%, no hard-boundary failure.
Also require no more failures and no material entity/date/citation regression.
Four held-out questions cannot establish a general category effect; any positive
screen is exploratory and needs a larger independent category-specific decision
set. Inconclusive remains inconclusive. No winner is forced.

## Rollout and rollback proposal

Retain current defaults everywhere. Consider only a category that later passes
independent live calibration with complete accounting. Owner must review exact
commit, canary scope/duration, floors, rollback owner and revert before deployment.
Halt on unsupported important claims, citation/entity/date regression, failure-rate
increase, no-gain continuation, route drift or any cap overrun. Roll back the
category routing to its frozen baseline without replaying ambiguous paid work.
No production rollout or rollback was executed by this pilot.
