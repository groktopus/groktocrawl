# Pilot outcome: correctness repair, efficacy inconclusive

Candidate correctness commit: `626b01acb4ab38a5024f32513dd6eac221632ab5`.
Baseline and route evidence: [baseline.json](baseline.json).
The actual local execution host is auriga (Darwin); an initial self-SSH probe was
a discovery mistake. It failed host-key verification with no existing auriga
known-host entry, not evidence of a changed production key or invalid GitHub auth.
No trust/security settings changed. Direct existing gpuslut01 SSH succeeded.

## Confirmed correctness defect

At upstream baseline, a direct deterministic probe returned `[]` for provider
failure, malformed JSON, `{}` and `[1]`; a whitespace-only topic was accepted.
The legacy loop used an empty list to stop as though coverage were adequate.
Nine initial regression cases produced seven failures and two passes before the
repair. This is a reproduced error-state defect, not a demonstrated policy gain.

The repair rejects invalid topic lists and JSON with a typed provider-output
error. Provider failures, cancellation and rate-limit exceptions propagate rather
than becoming adequate votes. Polling raises, streaming emits a terminal error
for provider-output/rate-limit failures and never synthesizes a successful answer.
Retry delay survives the streaming error. Successful decisions and exhausted
source-credit budgets have separate coverage status values. No new search or
retry is added. One older test mock now returns valid gap JSON for that stage.

## Verification

- 46 targeted gap, synthesis-ordering, source-registry and budget tests passed.
- 82 adjacent research/streaming/filter/answer-eval tests passed.
- Four existing obligation policy tests passed.
- Ruff checks/format and `git diff --check` passed for the changed code.
- Focused mypy passed for both changed production modules.
- Broad answer evaluation: 11 pass, one expected negative mis-citation failure;
  pinned broad baseline matches exactly. This is hermetic advisory evidence,
  not real-model quality. The initial research-only selection passed all four
  selected cases but mismatched the all-target baseline because eight cases were
  excluded; the full-target rerun resolves that selection mismatch.
- An exploratory sandbox full suite had 4,728 passed, 51 failed, 106 errors and
  35 skipped. Local socket/DNS restrictions explain many failures; this run is
  not a green suite. A non-external full-suite rerun with local socket access is
  completed with 4,834 passed, 28 skipped, zero test failures and 58 setup errors
  (missing isolated durable Redis/PostgreSQL configuration). It is not a fully
  green suite; no production stores were substituted.

The focused runs emitted pre-existing AsyncMock/deprecation warnings. Tests do
not verify production deployment, real retrieval or reference-answer correctness.

## Existing replay reproduced, not new live uplift

The unchanged W12.4 runner produced 108 deterministic records (12 cases x three
arms x three repeats). Its unchanged analyzer reproduces the existing experimental
control decision in all three repeats: challenge closure 0.9 for the obligation
arm versus 0 for its constructed fixed control; 22 versus 12 queries per repeat.
The obligation arm still has one unsupported high-importance obligation. Its
post-gain rate is 1/22. These synthetic construction results are not live effect
sizes, sample prevalence or new independent observations. The diagnostic arm is
`w10_diagnostic`, not the deployed current-route arm. See retained replay artifacts.
No new candidate optimizer, model policy or evaluator was tuned or executed.

## Development capture incident and accounting limit

A bounded plan named six public primary URLs, one request each, no retries,
15-second client timeout and lightweight acquisition. A first process failed
at import (`requests` unavailable) before dispatch. It was replaced with the
service-installed httpx client and existing configured API authentication.

During that attempt, route inspection showed `/v2/scrape` ignores the supplied
`lightweight_only` field and forwards ordinary scraper options. Therefore the
claimed lightweight bound was not enforced. The SSH client was interrupted;
read-only process inspection identified the still-running exact `python -`
subprocess PID 192275 inside the agent container, and that subprocess alone was
terminated. The agent service was not stopped or restarted. Buffered output was
empty, so the number of dispatched/completed source operations, any downstream
recovery/indexing/model operations and actual cost are UNKNOWN. At most six
client-level scrape requests were scheduled by that script. Client cancellation
cannot prove cancellation of server-side acquisition or indexing. None was
retried; no resulting source artifact is used as verified evidence or a gold label.
The script is intentionally not a ready-to-run live runner.

This failed attempt belongs in optimization cost/failure accounting. It must not
be recorded as zero cost or successful retrieval. No current/fixed/obligation live
answer comparison was dispatched. The declared zero model prices are configuration
metadata, not a billing measurement; compute/editor/search/fetch cost is unknown.

## Blockers and next concrete action

1. **Held-out comparison pending, not denied:** ADR-0076 requires candidate edits
   to be unable to read held-out questions/sites/labels and evaluator definitions.
   This shared editor workspace has no such enforced boundary. Establish a
   separate evaluator identity/workspace before any decision-set run. Do not
   weaken security or call a visible file hidden.
2. **Authoritative labels pending:** eight development cases cover the requested
   categories, but exact expected-value slots await successful public captures.
   They are explicitly not gold. Keep private user data out of all external work.
3. **Live budget enforcement pending:** an isolated adapter must enforce the
   protocol's search/fetch/model/retry/input/output/time caps and retain operation
   receipts. The normal `/v2/scrape` route cannot enforce lightweight-only intent;
   use the existing upstream `ScraperClient.scrape(lightweight_only=True)` contract
   in an authorized isolated capture environment, without bypassing access rules.
   Reconcile the interrupted attempt before considering any repeat of its URLs.
4. **Money accounting unresolved:** actual billable units/prices and infrastructure
   cost are unavailable. The proposed bounded run estimates $0 declared token
   charges only for the inspected free route. Any paid route requires an exact
   approved monetary cap before dispatch.

Recommendation: retain current defaults for every category. No category benefit
is established; live evidence remains inconclusive. The gap-error repair is
reviewable independently and has no claimed quality uplift. Rollout/rollback and
halt criteria are proposed in the protocol, with parent/owner review required.
