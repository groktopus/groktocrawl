# W9 replacement-readiness decision

Date: 2026-09-30. Technical assessment: **adopt for continued bounded use in the
experimental fork**. Architecture ratification: **approved by Magnus Hedemark on 2026-09-30**.
This report is Codex's evidence-backed recommendation under the standing roadmap
execution authorization; Magnus subsequently approved the bounded ADR recommendations directly in the
project conversation: “I approve the bounded ADR recommendations. Please proceed.”

GroktoCrawl X completed its seven-day frozen pilot with **36 successful declared
operations across three checkpoints**. The final checkpoint passed on the single
permitted retry after a model readiness timeout. This experimental fork remains
separate from mainline; these results do not authorize an upstream replacement,
production migration, Qdrant removal, or unrestricted LangGraph promotion.

## Evidence and gate decisions

| Gate | Finding | Evidence |
|---|---|---|
| Seven days and at least 30 operations | Passed; 36 operations, 3 checkpoints | [Tracked state](evidence/replacement-rehearsal/w9-pilot-state.json), [mechanical closeout](evidence/replacement-rehearsal/2026-09-30-free-model-checkpoint-2/closeout.json) |
| Frozen identity | All packets observed revision `46a528228b1365189cdd38d0bcdb12109a8dc763` and model `free` | [Checkpoint 0](evidence/replacement-rehearsal/2026-09-23-free-model-checkpoint-0/README.md), [checkpoint 1](evidence/replacement-rehearsal/2026-09-26-free-model-checkpoint-1/README.md), [checkpoint 2](evidence/replacement-rehearsal/2026-09-30-free-model-checkpoint-2/README.md) |
| Compatibility and artifacts | Each successful suite completed its 11 inherited operations and cross-client research journey; retained artifact equality passed | Three checkpoint compatibility and research receipts |
| Storage and rollback | PostgreSQL artifact authority, pgvector serving, Qdrant rollback readiness passed | Final research receipt; [preflight matrix](w9-replacement-readiness-preflight.md) links migration, restore, deletion and reconciliation evidence |
| Failure accounting | First final attempt retained with zero credit; one unchanged-deployment retry passed | [Failure receipt](evidence/replacement-rehearsal/2026-09-30-free-model-checkpoint-2/first-attempt-failure.json) |
| Resources and latency | Bounded continued use supported; capacity and steady-state memory unproven | All three snapshots; follow-ups below |
| Architecture approval | Approved by the named decider on 2026-09-30; bounded scopes recorded in ADR metadata | ADRs 0073, 0076–0078, 0083–0087 and preflight disposition table |

The final readiness check succeeded; no new data-loss, authority, deletion, or
rollback divergence failure was observed in the declared suites. The first
attempt's 503 prevents any claim of uninterrupted inference availability.

## Operational limits and follow-ups

Grounded answer latency across successful checkpoints was 4.8, 27.7 and 26.2
seconds; streaming answer latency was 16.5, 7.2 and 31.1 seconds. Agent terminal
latency varied from 0.5 seconds to 252.7 seconds, then 64.7 seconds. Cache reuse
and model behavior differ between runs, so these are observations, not a
controlled speed comparison or service-level guarantee.

Browser memory snapshots rose from 21.46% to 34.79% to 38.48%; scraper memory
rose from 14.56% to 35.25% to 38.60%. The snapshots show neither exhaustion nor
proof of a plateau. [#392](https://github.com/magnus919/groktocrawl-x/issues/392)
requires a bounded soak to distinguish cache growth from a leak.
[#391](https://github.com/magnus919/groktocrawl-x/issues/391) investigates the
model readiness timeout separately from ordinary service health.

The pilot covered one deployment, small fixed fixtures, three scheduled suites,
and ordinary experimental use. It did not prove multi-user saturation,
Internet-wide acquisition quality, physical disaster recovery, production
RPO/RTO, or future model/provider reliability. Keep rollback available pending
separately reviewed retirement work. The frozen runtime is unchanged.

## ADR disposition and final owner decision

Magnus approved accepting ADR-0073 with the imperative reference as default and
LangGraph optional for advanced workflows; ADR-0076 for bounded model-reviewed
publication; ADR-0077 for trusted-server import; and ADR-0078 with PostgreSQL
artifact authority and Valkey execution/receipts/deletion continuity. Retain
accepted ADR-0079. Magnus approved accepting ADRs 0083–0087 at their documented scopes:
prose intake, independent roots, experimental semantic verification, opt-in
obligation continuation, and the generalist default.

These recommendations preserve the rejected universal typed missions, default
accumulated threads, generic specialist fan-out, and default adaptive retrieval.
They do not convert model review into human approval or experiment evidence into
production readiness. ADRs 0073, 0076–0078 and 0083–0087 are now accepted at their bounded scopes.
ADR-0073 retains the imperative default and optional LangGraph; ADR-0078
records PostgreSQL artifact authority with Valkey execution/deletion continuity.
No accepted predecessor body is rewritten or broadly superseded.

Issue #311’s pilot work is complete. Owner ratification completes #312, #1,
#103 and milestone 8 through the accompanying closeout PR. #391 and #392 remain
focused operational follow-ups. No redeployment is required for this decision.
