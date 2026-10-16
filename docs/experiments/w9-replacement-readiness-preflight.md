# W9 replacement-readiness preflight

- Status: **operational pilot passed; final recommendation published, bounded recommendations ratified by Magnus on September 30**
- Candidate repository: [`magnus919/groktocrawl-x`](https://github.com/magnus919/groktocrawl-x)
- Architecture decider: Magnus Hedemark (`magnus919`)
- Decision issue: [#312](https://github.com/magnus919/groktocrawl-x/issues/312)
- Current candidate revision: `46a528228b1365189cdd38d0bcdb12109a8dc763`
- Earliest decision date: **2026-09-30T00:09:25.966168Z**, conditional on unchanged configuration and all remaining gates

**This experimental fork does not replace mainline GroktoCrawl by itself.** A
successful W9 decision can adopt the candidate for continued use in this fork.
Any mainline proposal, production migration, or upstream release requires a
separate decision and its own evidence.

## What can be decided now

The candidate has passed its freeze and isolated deployment, incumbent-compatible
journeys, migration and rollback rehearsal, durable recovery checks, client-path
parity, and the PostgreSQL/pgvector serving cutover. These results support a
recommendation to **adopt the candidate for the experimental fork** after the
completed operational pilot, ratified by the owner on September 30.

The technical recommendation is complete; owner ratification is recorded. Earlier failed and superseded windows remain in
the evidence record. Independent agent testing drove repairs to model readiness,
similarity diagnostics, crawl accounting, and cross-client barrier errors. A
bounded relevance study then retained raw cosine retrieval and rejected two
more complex variants. The former September 20 window passed checkpoint 0 but ended after an agent
replacement; later search configuration also changed. Its dates and counts do
not count for the adoption decision. The September 22 current-main window also
ended before checkpoint 1 when a separate model test recreated the candidate
agent with an older image and the `free` alias. The restored current-main candidate with the durable 14-CPU semantic
allocation passed a checkpoint on `general`, then the owner pinned `free` for
the experimental candidate only. That change ended the `general` window.
The new [checkpoint 0](evidence/replacement-rehearsal/2026-09-23-free-model-checkpoint-0/README.md)
passed all 12 operations, including model-backed text and structured output.
The `free` window had 24/30 operations and 2/3 checkpoints after
[checkpoint 1](evidence/replacement-rehearsal/2026-09-26-free-model-checkpoint-1/README.md)
passed all 12 declared operations. [Checkpoint 2](evidence/replacement-rehearsal/2026-09-30-free-model-checkpoint-2/README.md)
passed on the single permitted retry. Current final credit is 36 operations and
3/3 checkpoints; [closeout and recommendation](w9-replacement-readiness-decision.md)
record the failure, limits and completed owner ratification. The preceding counts are
historical checkpoint-1 context.

## Requirement-by-requirement matrix

| Requirement | Current finding | Evidence | Final action |
|---|---|---|---|
| Candidate is frozen, reproducible, and isolated from mainline | Passed | [Candidate freeze receipt](evidence/replacement-rehearsal/2026-09-11-candidate-freeze/README.md) | Confirm the final pilot used the frozen candidate revision. |
| Inherited user journeys behave like the incumbent | Passed in three matched runs per arm | [Compatibility packet](evidence/replacement-rehearsal/2026-09-11-compatibility/README.md) | Link the final checkpoint receipts; investigate any candidate-only difference. |
| Research quality is strong enough to justify the architecture | Passed only for the bounded architecture assembled from accepted slices; two attempted answer-policy replacements were rejected | [W1 comparison](enterprise-evaluation/w1-comparison-outcome-2026-09-10.md), [W7 comparison](enterprise-evaluation/w7-candidate-d-outcome-2026-09-11.md), [W10 evidence index](evidence/adaptive-policy/w10-final/00-index.md) | State clearly that adoption retains the fixed retrieval and generalist defaults selected by the experiments. |
| Durable execution survives interruption without ambiguous ownership or artifacts | Passed for the bounded experimental route | [Recovery confirmation matrix](research-execution-confirmation.md), [backup and restore packet](durable-backup-restore.md) | Retain the bounded scope and the unproven disaster-recovery limits. |
| HTTP, SSE, CLI, and MCP expose one coherent retained artifact | Passed | [Client protocol](research-client-protocol.md), [conformance matrix](client-protocol-conformance.md), [recovered-journey PR #251](https://github.com/magnus919/groktocrawl-x/pull/251) | All three checkpoints confirmed equality; retain this gate for future runtime changes. |
| PostgreSQL can be the durable authority and pgvector can serve semantic search | Passed for the experimental deployment | [Storage evaluation](storage/pgvector-qdrant-evaluation.md), [application cutover packet](evidence/storage-vector-evaluation/2026-09-09-pgvector-serving-cutover/), [ADR-0079](../adr/experimental/groktocrawl-x/0079-consolidate-retained-and-vector-storage-in-postgresql.md) | Keep Qdrant available until the operational rollback window passes; removal is separate work. |
| Migration to the candidate and rollback to the incumbent are rehearsed | Passed | [Migration and rollback packet](evidence/replacement-rehearsal/2026-09-11-migration-rollback/README.md) | Preserve the tested rollback path through the final decision. |
| Candidate operates over the required real-time window | Passed: all three free-model checkpoints passed 12/12 operations; final current-window credit is 36 operations and 3 checkpoints | [Pilot protocol](w9-operational-pilot-protocol.md), [tracked state](evidence/replacement-rehearsal/w9-pilot-state.json), [checkpoint 0](evidence/replacement-rehearsal/2026-09-23-free-model-checkpoint-0/README.md), [checkpoint 1](evidence/replacement-rehearsal/2026-09-26-free-model-checkpoint-1/README.md) | Completed September 30; see the [final decision](w9-replacement-readiness-decision.md) and zero-error closeout report. |
| Resource use fits ordinary operation on the deployment host | Provisional pass at bounded scale, including the 14-CPU semantic configuration | [Checkpoint 1](evidence/replacement-rehearsal/2026-09-26-free-model-checkpoint-1/README.md), [compatibility resource summary](evidence/replacement-rehearsal/2026-09-11-compatibility/resource-summary.json), [pilot protocol limits](w9-operational-pilot-protocol.md#limits) | The final report compares all three snapshots and opens #392 for memory-soak evidence. Do not claim production capacity. |
| Operator burden is acceptable | Revised after a real failure, two setup-only dependency failures, and one shared model-gateway restart during a checkpoint attempt | [Retained restart failure and correction](evidence/replacement-rehearsal/2026-09-19-pilot-restart/README.md), [current free-model checkpoint](evidence/replacement-rehearsal/2026-09-26-free-model-checkpoint-1/README.md), [operator runbook](../runbooks/experimental-replacement-candidate.md) | The corrected runner completed checkpoints 1 and 2. Retain the final model-readiness timeout and #391 follow-up; preserve persistent private-file placement. |
| Limits and residual risks are explicit | Passed for the preflight | [Pilot limits](w9-operational-pilot-protocol.md#limits), this report | Carry the limits into the final recommendation and create follow-up issues for any newly observed gap. |

## Architecture decisions

The final W9 PR must update status metadata and predecessor relationships without
rewriting decision history. The operational gate passed. The named decider ratified these bounded dispositions on 2026-09-30.

| ADR | Current status | Evidence-based preflight disposition | Remaining gate |
|---|---|---|---|
| [ADR-0073: compare research runtimes](../adr/experimental/groktocrawl-x/0073-compare-research-runtimes-under-one-policy.md) | Accepted (bounded scope) | **Revise and accept** the imperative reference as the default plus optional LangGraph for advanced workflows. The comparison found value in dynamic fan-out, durable interrupts, forks, and capability evolution, without making the graph the authority for application data. | Ratified 2026-09-30; retain an explicit real-user/checkpoint operating gate before broad LangGraph use. |
| [ADR-0076: model-reviewed consolidated publications](../adr/experimental/groktocrawl-x/0076-retain-model-reviewed-consolidated-publications.md) | Accepted (bounded scope) | **Accept for the bounded experimental store.** The implementation retains explicit model provenance and fails closed on structural, support, conflict, freshness, and render-audit failures. | Ratified 2026-09-30; no inference of human approval, public API, or production retention. |
| [ADR-0077: trusted consolidated bundle import](../adr/experimental/groktocrawl-x/0077-trusted-consolidated-bundle-import.md) | Accepted (bounded scope) | **Accept for trusted-server experimental import.** Exact validation, origin authority, bounded grants, idempotent receipts, and deletion/expiry behavior were exercised through the retained-artifact work. | Ratified 2026-09-30; keep public and cross-trust import out of scope. |
| [ADR-0078: durable backup and artifact authority](../adr/experimental/groktocrawl-x/0078-define-durable-research-backup-and-artifact-authority.md) | Accepted (bounded scope) | **Revise and accept.** PostgreSQL is now the authoritative artifact store while Valkey owns bounded execution state, receipts, indexes, and deletion continuity. | Ratified 2026-09-30; preserve the explicit absence of production disaster-recovery RPO/RTO proof. |
| [ADR-0079: consolidate storage in PostgreSQL](../adr/experimental/groktocrawl-x/0079-consolidate-retained-and-vector-storage-in-postgresql.md) | Accepted | **Retain.** PostgreSQL authority and pgvector serving passed cutover and rollback evidence. | W9 passed; Qdrant removal remains a separate proposal and retirement review. |
| [ADR-0083: retain prose intake](../adr/experimental/groktocrawl-x/0083-retain-prose-intake-over-universal-research-missions.md) | Accepted (bounded scope) | **Accept.** Keep prose as the default and reject universal Research Mission normalization. Preserve smaller typed contracts for explicitly triggered workflows. | Ratified 2026-09-30; any successor needs a narrower trigger and a new frozen comparison. |
| [ADR-0084: retain independent research roots](../adr/experimental/groktocrawl-x/0084-retain-independent-research-roots-over-default-threads.md) | Accepted (bounded scope) | **Accept.** Keep independent durable roots and construct explicit comparisons for follow-up research. Reject default accumulated-thread injection. | Ratified 2026-09-30; any continuity feature must prevent stale-current leakage and prove value in a narrower trial. |
| [ADR-0085: independent semantic verification](../adr/experimental/groktocrawl-x/0085-add-independent-semantic-verification-before-claim-publication.md) | Accepted (bounded scope) | **Accept for experimental rollout.** The verifier removed the tested critical false accepts and missed contradictions without rejecting supported claims. | Ratified 2026-09-30; live false-rejection, latency, case-mix, and rollback calibration remain required before stable-path promotion. |
| [ADR-0086: evidence-obligation continuation](../adr/experimental/groktocrawl-x/0086-add-bounded-evidence-obligation-continuation.md) | Accepted (bounded scope) | **Accept as an opt-in experiment.** Continue only for a named open obligation and stop on closure, zero gain, or the shared budget. Fixed retrieval remains the stable default. | Ratified 2026-09-30; automatic obligation authoring and live retrieval yield remain unproven. |
| [ADR-0087: retain the generalist default](../adr/experimental/groktocrawl-x/0087-retain-generalist-over-generic-specialist-fanout.md) | Accepted (bounded scope) | **Accept.** Reject generic specialist fan-out and retain one research owner. Preserve the typed handoff for narrower task-specific experiments. | Ratified 2026-09-30; future specialist work needs a calibrated router, a separable task class, and independent evaluation. |

## D1-D7 decision coverage

| Decision | Record | Preflight result |
|---|---|---|
| D1: execution, knowledge, and rendering boundaries | [ADR-0068](../adr/experimental/groktocrawl-x/0068-separate-research-execution-knowledge-and-rendering.md) | Retain the accepted separation. |
| D2: versioned Knowledge IR and verification | [ADR-0069](../adr/experimental/groktocrawl-x/0069-define-versioned-knowledge-and-verification.md) | Retain the accepted contract and independent verification boundary. |
| D3: evidence retention and vector storage | [ADR-0071](../adr/experimental/groktocrawl-x/0071-store-research-evidence-independently-of-sessions.md), [ADR-0079](../adr/experimental/groktocrawl-x/0079-consolidate-retained-and-vector-storage-in-postgresql.md) | Retain PostgreSQL authority and pgvector serving for the experiment; W9 passed; Qdrant removal still requires separately reviewed retirement work. |
| D4: orchestration runtime | [ADR-0073](../adr/experimental/groktocrawl-x/0073-compare-research-runtimes-under-one-policy.md) | Revise and accept the imperative default with optional advanced LangGraph use. |
| D5: durable execution ownership | [ADR-0074](../adr/experimental/groktocrawl-x/0074-define-research-recovery-before-selecting-infrastructure.md), [ADR-0078](../adr/experimental/groktocrawl-x/0078-define-durable-research-backup-and-artifact-authority.md) | Retain the bounded Valkey execution owner; revise ADR-0078 around PostgreSQL artifact authority. |
| D6: verified client protocols | [ADR-0072](../adr/experimental/groktocrawl-x/0072-expose-verified-research-through-an-experimental-protocol.md) | Retain the accepted experimental protocol and bounded replay behavior. |
| D7: evaluation and adoption | [ADR-0070](../adr/experimental/groktocrawl-x/0070-evaluate-research-policy-and-runtime-separately.md) | Retain the accepted evaluation discipline; bounded adoption was ratified after W9 passed. |

## Final decision procedure

After the final eligible checkpoint:

1. Link both remaining checkpoint packets and update every pending row above.
2. Record whether the seven-day, 30-request, compatibility, artifact, storage,
   rollback, latency, and resource gates passed.
3. If a gate failed, preserve the failure, choose **revise** or **reject**, and
   open a focused follow-up issue for each missing proof.
4. If every gate passed, choose **adopt for the experimental fork**, apply the
   ADR dispositions above, and state the retained limits in the decision.
5. Synchronize the roadmap, experiment index, README, operator runbook, issues
   #311 and #312, milestone 8, umbrella issue #103, and the original plan issue
   #1 in the same change set.

The final report must distinguish three conclusions: fitness for continued use in
the experimental fork, readiness for this home-lab deployment, and any later
proposal to mainline. Evidence for the first two does not decide the third.
