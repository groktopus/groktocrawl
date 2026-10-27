# Parent evaluator handoff

First-screen budget superseded by [screening-protocol.md](screening-protocol.md) and
[screening-handoff.json](screening-handoff.json). The larger proposal below is historical
and was not executed or admitted.

The candidate correctness revision is frozen at
`626b01acb4ab38a5024f32513dd6eac221632ab5`; its code patch does not implement a new
live retrieval policy. The comparison plan and development corpus are separate.
The existing W12.4 replay implementation cannot be relabeled as a live adapter.

## Smallest arrangement and exact contract

ADR-0076 requires development and held-out questions **and sites** to be separated
before tuning, held-out data/results unavailable to the editor, and those boundaries
enforced outside the candidate workspace using restricted identities/tools and
network policy. A writable local hidden file, different directory under the same
readable account, or a promise not to inspect labels does not meet that requirement.
The present executor can read the filesystem root and SSH using the existing
production account; moving labels elsewhere on auriga/gpuslut01 is insufficient.

Use a parent-owned evaluator in an existing cloud execution/state environment that
this candidate editor cannot inspect, with no shared-filesystem label mount and no
candidate-editor credentials/tools in the evaluator. The parent keeps the questions,
sites, reference values, passages and exact outputs. Supply the evaluator an
immutable upstream revision plus adapter artifact hash, never a writable checkout.
The evaluator executes the frozen adapter; only the inference runtime receives
question inputs, while labels stay exclusively in the separate evaluator. Source
access must use existing authorized service clients and preserve access controls.
The parent must verify that its cloud environment actually has that separation;
this document does not assert it has been established.

If the parent instead proposes a process that exposes held-out questions to this
editor after the code freeze, the ADR says exposed cases become development and a
fresh decision set is needed. Temporal freezing/no further tuning is useful, but
does not alone demonstrate the stated filesystem/tool boundary. The owner may
explicitly revise scope/acceptance, but the candidate must not weaken it implicitly.
No new credential or security-setting change is needed for the proposed existing
cloud boundary. If that environment cannot execute the immutable adapter with
existing authenticated tools, report that exact capability gap before dispatch.

## Admission packet (parent-only)

```json
{
  "schema_version": "w12.4-live-calibration-admission/1",
  "experiment_id": "parent-assigned",
  "baseline_commit": "8df43e5dbe029a888a2007d9dcf0baf4869657dc",
  "correctness_commit": "626b01acb4ab38a5024f32513dd6eac221632ab5",
  "adapter_sha256": "REQUIRED: reviewed live adapter, not replay runner",
  "protocol_sha256": "from freeze.json",
  "evaluator_sha256": "parent-only frozen evaluator artifact",
  "dataset_sha256": "parent-only new heldout questions AND sites",
  "reference_sha256": "parent-only verified authoritative facts/passages",
  "isolation_verdict": "REQUIRED: direct evidence of editor access denial",
  "model_alias": "free",
  "resolved_model": "openai/ornith/ornith-1.5-122b:free",
  "arms": ["current_route", "fixed_diagnostic_control", "obligation_bounded"],
  "cases": 4,
  "repeats": 2,
  "total_attempts": 24,
  "seed": 20261027,
  "order_sha256": "parent-retained randomized/counterbalanced order",
  "caps": {
    "search_dispatches": 72,
    "acquisition_dispatches": 144,
    "model_dispatches_including_retries": 72,
    "aggregate_input_utf8_bytes": 4608000,
    "aggregate_output_tokens": 589824,
    "per_attempt_seconds": 120,
    "total_wall_seconds": 3600,
    "new_paid_service_spend_usd": 0
  },
  "actual_total_cost_status": "unknown until complete accounting"
}
```

Before admission, confirm model-route stability, source/citation receipt capture,
server cancellation semantics, all internal retry counts, and actual token input
and output accounting. No runner may treat client timeout as completed server-side
cancellation. The aborted capture is accounted for separately and must not be
replayed blindly. All development reference slots remain pending verification.

## Per-attempt receipt (retain privately with the parent)

Extend the existing W12.4 record: `sequence`, `case_id`, `stratum`, `policy`,
`repetition`, `outcome`, `scores`. Add these live-calibration fields:

```json
{
  "schema_version": "w12.4-live-calibration-attempt/1",
  "sequence": 1,
  "case_id": "opaque-parent-id",
  "stratum": "parent-category",
  "policy": "current_route",
  "repetition": 0,
  "candidate_sha256": "immutable adapter hash",
  "started_at": "UTC timestamp",
  "ended_at": "UTC timestamp",
  "status": "completed|failed|timed_out|outcome_unknown|budget_exhausted",
  "question_sha256": "parent-retains-question",
  "model_alias": "free",
  "resolved_model": "exact actual target",
  "sampling": {"temperature": null, "seed": null},
  "cache_state": "receipt required",
  "outcome": {
    "answer_sha256": "parent-retains-exact-answer",
    "source_set_sha256": "parent-retains-exact-URL-bytes-date-hash-span mapping",
    "closed_obligation_ids": [],
    "stop_reason": "closure|no_gain|cap|failed|fixed_complete",
    "operation_receipt_ids": [],
    "ambiguous_operation_receipt_ids": []
  },
  "scores": {
    "expected_facts": 0,
    "covered_facts": 0,
    "unsupported_material_claims": 0,
    "unsupported_important_obligations": 0,
    "exact_page_supported_claims": 0,
    "all_material_claims": 0,
    "entity_date_errors": 0,
    "abstention": "justified|unjustified|not_applicable|ungraded",
    "verified_useful_answer": false,
    "grading_status": "pending|complete|disputed|blocked"
  },
  "usage": {
    "search_dispatches": 0,
    "fetch_dispatches": 0,
    "browser_dispatches": 0,
    "model_dispatches_including_retries": 0,
    "input_tokens": null,
    "output_tokens": null,
    "reasoning_tokens": null,
    "cache_tokens": null,
    "latency_ms": 0,
    "cost_usd": null,
    "accounting_complete": false
  },
  "human_interventions": 0
}
```

Null means unmeasured, never zero. Failed/ambiguous attempts remain in the 24-case
attempt denominator. The evaluator retains both rendered answer and exact cited
page bytes so it can verify entailment and qualifiers. Every follow-up binds to
one important missing fact and has an observed material-evidence delta; a model's
claim of closure is not a verified fact. No automatic model-generated universal
mission/obligation normalization.

Return only aggregate category counts, arm metrics, hard-invariant violations,
accounting completeness, uncertainty, and an inconclusive/hold recommendation to
the candidate editor. Keep questions/sites/answers/labels and per-case details
private until the decision set is formally retired. Once any case is exposed,
mark it development and forbid use as fresh held-out validation.
