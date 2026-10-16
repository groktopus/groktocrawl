# W11 general-comparison execution packet

Status: **retrieval complete; caller-authority hard gate failed; diagnostic grading pending**

W10 selected fixed retrieval for every challenge type. The original private
trial directory was unavailable after the authorized pause, so this packet
derives the exact single-query control plans from the committed challenge cases
and binds them to the final W10 selection and complete public accounting.

- `w11-fixed-control-handoff.json` records the 36 derived case/repetition
  controls and the frozen eight-result search limit.
- `w11-work-order.json` counterbalances 72 flat HTTP and recorded-continuation
  trials across 12 cases and three repetitions.
- `preflight.json` records the live release identity, exact enabled and disabled
  grants, and fail-closed probes for every disabled specialist workflow.
- `http-compatibility-before.json` records a passing SearXNG-compatible HTTP
  surface before measurement.
- `http-compatibility-after.json` records the same passing surface after all
  retrieval and diagnostic work.
- `scope-equivalence.json` proves that both arms start with the same frozen
  seven-engine search scope without dispatching a query.
- `w11-freeze.json` binds the 72-trial design, exact source and container
  identities, model route, resource ceilings, analysis plan, and every public
  input hash before the first scored query.

The freeze gate now distinguishes the two policy-only grants, `jobs` and
`science`, from grants written to the Compose arm manifest. Both still have to
appear in the live disabled set and return `tool_disabled` before measurement
can begin.

An initial execution attempt was rejected before scoring. All 36 recorded
continuation trials reached SlopSearX but the client treated the workflow's
successful plain-text completion acknowledgement as an error. Six of 36 flat
HTTP trials also encountered HTTP 429 responses. The 30 partial HTTP checkpoints are not a
valid comparison and will be discarded with the failures. The corrected runner
accepts the text acknowledgement, still rejects structured errors, and allows
at most five HTTP attempts with bounded `Retry-After` handling. The
freeze's request ceiling includes those attempts. Measurement restarts from an
empty checkpoint directory under a new frozen manifest.

The private handoff records contain the committed case queries and remain
outside the repository. This packet contains no result content, model response,
credential, or private network address. The recovery path fails closed if W10
selects any adaptive policy.

The completed retrieval has 36 matched pairs and no missing or failed final
trials. Query plans, query hashes, and engine scopes match in every pair, and
the result-set Jaccard score is 1.0 for every pair. Recorded continuation added
a median 26.731 ms.

The retrieval hard gate nevertheless failed. Every recorded workflow reached
its eight-result budget and entered `succeeded` with
`stop_reason: result_budget_exhausted` before GroktoCrawl's completion update.
The update was acknowledged, but the terminal record retained
`caller_completed: false`. This violates the frozen ownership rule that
GroktoCrawl alone declares research complete. Quality grading continues only as
a diagnostic and cannot override this hard-gate failure.

The first diagnostic-grading attempt is excluded. The `general` route used its
completion allowance for reasoning on nine trials and returned no JSON; five
more responses omitted required candidates or claims. A minimal probe confirmed
that low reasoning effort plus the provider's completion-token parameter
returns strict JSON. The replacement diagnostic uses that setting and an
8,000-token combined reasoning/output ceiling. It receives its own post-gate
freeze and does not change the retrieval result.

`diagnostic-grading-accounting.json` closes that diagnostic with 11 attempted,
five completed, six failed, and 61 deliberately unattempted trials. No grade
from this incomplete packet contributes to the W11 decision.
