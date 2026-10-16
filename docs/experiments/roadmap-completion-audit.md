# Roadmap completion audit — 2026-09-30

The approved experimental roadmap is complete. This audit checks the original
plan and R1–R6 deliverables against delivered code, current CI, direct experiment
receipts, the frozen deployment, and owner acceptance. It does not substitute
closed issues or documentation labels for experimental evidence.

## Requirement-to-evidence audit

| Requirement | Authoritative evidence and audit result |
|---|---|
| W0 fork identity and isolated engineering lanes | README and AGENTS identify the separate experimental repository; fork Runtime CI uses hosted runners. GitHub reports Docker publication, Release Please, both Droid workflows and Trusted Live Calibration as manually disabled. The audit found missing branch protection and installed and read back the active rules described below. |
| R1 / W2 actual acquisition, claims, verification and three outputs | The acquired-source journey and consolidated-publication implementations are covered by `tests/unit/test_consolidated_journey.py`, `test_research_publication.py`, `test_publication_gate.py` and retained-publication tests. [Consolidated journey](research-consolidated-journey.md) and [client conformance](client-protocol-conformance.md) record the runnable bounded route and its limitations; current Fast Tests run unit/service and pinned LangGraph tests. The W9 live model text/structured probes and experimental cross-client journey passed. Model outputs retain model provenance. |
| R2 / W1, W4, W7, W8, W10–W12 measured evaluation | [W1 paired comparison](enterprise-evaluation/w1-comparison-outcome-2026-09-10.md), [W7 comparison](enterprise-evaluation/w7-candidate-d-outcome-2026-09-11.md), [runtime evidence](runtime-comparison/report.md), [W10 evidence ledger](evidence/adaptive-policy/w10-final/00-index.md), and the W11/W12 tracker link retained attempts, thresholds, failures and dispositions. Candidates B and D and default adaptive policy were rejected; approval does not turn those losses into quality gains. |
| R3 / W3 retained workflows | [Consolidated storage](research-consolidated-storage.md), [format compatibility](research-ir-compatibility.md), retained/import/expiry unit tests and PostgreSQL storage probes cover artifact retention, access, export/import, deletion, compatibility, successor authority and re-render. Current W9 research receipts preserve matching artifact digests across clients. |
| R4 / W5 operating and recovery contracts | [Recovery matrix](research-execution-confirmation.md), [combined restore evidence](durable-backup-restore.md), durable-research/publication tests, and [migration/rollback packet](evidence/replacement-rehearsal/2026-09-11-migration-rollback/README.md) prove the bounded ownership, retry, cancellation, restore and reconciliation scopes. Production physical-loss RPO/RTO remains unproven. |
| R5 / W6 compatibility and stack decision | [Matched compatibility summary](evidence/replacement-rehearsal/2026-09-11-compatibility/summary.json) records three successful runs for each arm across declared API/SSE/CLI/MCP/webhook journeys; all three final-window suites passed. [Vector evaluation](storage/pgvector-qdrant-evaluation.md) links actual migration, scale, fault and serving receipts; accepted ADR-0079 selects pgvector while Qdrant rollback remains available. |
| R6 / W9 deployable candidate and final decision | [Final checkpoint](evidence/replacement-rehearsal/2026-09-30-free-model-checkpoint-2/README.md) and recomputed closeout verify seven days, three packets, 36 operations, exact revision, `free` identity, receipt digests and completed artifacts. Candidate health was read back as healthy at the expected identity. [Owner-ratified decision](w9-replacement-readiness-decision.md) preserves the model timeout, limits, memory trend and separate follow-ups. |
| W13 normalized recovery | Accepted ADR-0088, merged PR #364 and barrier-recovery tests establish the shared scraper boundary used by direct scrape, research and crawl, with CLI/MCP receiving classified results. No unbounded retry or success claim for inaccessible content is implied. |
| ADRs, docs and project delivery | PR #394 merged at `4198fbf33c6114429fe6dc6880cfacc05dfc0c9f` with required checks green. Nine proposed ADRs are now accepted at owner-approved bounded scopes; accepted predecessor bodies were preserved. All roadmap milestones and umbrella issues are closed; current issue summaries were reconciled during this audit. |

## Verified repository enforcement

On September 30 the completion audit found no rulesets or classic protection on
experimental `main`. It corrected that incomplete W0 requirement rather than
ignoring it. GitHub’s effective branch-rule endpoint now reports:

- active `main required checks`: `Code Quality Gate` and `Runtime Gate`, with
  strict up-to-date check enforcement and no bypass actors;
- active `main review policy`: pull requests, one approving review, dismissal of
  stale approvals and resolved review threads; repository administrators may
  bypass review only through a pull request;
- branch deletion and non-fast-forward updates are blocked.

The maintainer review bypass does not bypass the separate required-check rule.
Inherited release workflows remain disabled until experimental publication
identities are explicitly reviewed. This documentation does not enable them.

## Outcome and residual work

The measured improvement is a substrate with tested durable execution, explicit
artifact authority, recoverable retained knowledge and coherent client protocols
at the approved experimental scopes. The studies do not prove that new research
policies produce universally better answers than the incumbent. The decision
retains fixed retrieval, prose intake and the generalist default where evidence
favored them, and preserves optional advanced runtime/verification contracts.

[#391](https://github.com/magnus919/groktocrawl-x/issues/391) and
[#392](https://github.com/magnus919/groktocrawl-x/issues/392) remain focused
operational follow-ups. They are explicitly retained limitations in the accepted
adoption decision, not hidden unfinished pilot gates. Qdrant retirement, broad
LangGraph promotion and mainline/production adoption require separate decisions.

## Contribution process correction

The audit also detected omitted DCO trailers on three recent authored W9
documentation commits. The [corrective attestation](w9-contribution-attestation.md)
records the defect and certification in a signed corrective commit without
rewriting merged history. It does not falsely claim the originals were signed.
