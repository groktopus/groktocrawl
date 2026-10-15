# Historical research experiment records

These records preserve the plans, decisions, protocols, and evidence from the bounded research-architecture work formerly maintained in GroktoCrawl X. They are historical project material, not a current roadmap, deployment-status page, or separate active branch. Mainline adoption and present compatibility boundaries are defined by [ADR-0069](../adr/0069-adopt-bounded-research-substrate.md); the original X decisions are preserved in the [ADR provenance archive](../adr/experimental/groktocrawl-x/README.md).

## How to read these records

The workstream summaries and pilot results below report what was recorded at the time. They do not establish current production deployment, current issue ownership, or current runtime health. GitHub issue and milestone links are retained as historical provenance. Use the [mainline ADR index](../adr/README.md), current deployment configuration, and current implementation for present behavior. Deployment-specific hostnames in retained records are normalized to reserved `.example` names for repository hygiene; those placeholders are not endpoint configuration or experiment results. The affected files are sanitized copies, not byte-identical originals. Their exact original and sanitized-copy hashes, and the source commit for verifying originals, are listed in the [hostname sanitization manifest](evidence/hostname-sanitization-manifest.json). Hashes embedded in historical freeze records continue to refer to source bytes and are not claims about the sanitized copies.

## How the experiment records fit together

| Document | Purpose | Update policy |
|---|---|---|
| Research brief or analysis plan | Explains the question, comparison, and intended decision | Freeze before measurement; amend visibly if the design changes |
| Protocol or manifest | Pins inputs, bounds, revisions, and pass/fail rules | Treat as immutable once a run begins |
| Research log | Records execution, deviations, and operational observations | Append while work is active |
| Evidence packet | Preserves machine-checkable inputs and results | Never rewrite a completed packet; supersede it with a new run |
| Outcome or decision report | Explains what the evidence supports and what it does not | Correct factual errors visibly; use a successor for a new conclusion |
| ADR | Records an architecture decision and its consequences | Follow the immutability and successor rules in the ADR index |

## Historical workstreams and outcomes

| Area | Primary documents | Current position |
|---|---|---|
| Architecture and baseline | [Roadmap](research-architecture.md), [W1 comparison](enterprise-evaluation/w1-comparison-outcome-2026-09-10.md) | Foundation complete; Candidate B rejected |
| Runtime orchestration | [Runtime comparison](runtime-comparison/report.md), [future-capability study](future-runtime-scenarios.md) | Imperative reference retained; LangGraph available for advanced workflows |
| Storage and recovery | [Storage evaluation](storage/pgvector-qdrant-evaluation.md), [ADR-0074](../adr/experimental/groktocrawl-x/0074-define-research-recovery-before-selecting-infrastructure.md), [ADR-0079](../adr/experimental/groktocrawl-x/0079-consolidate-retained-and-vector-storage-in-postgresql.md) | Recovery and pgvector decisions accepted for the experimental deployment |
| Replacement rehearsal | [Operational pilot protocol](w9-operational-pilot-protocol.md), [readiness preflight](w9-replacement-readiness-preflight.md), [evidence](evidence/replacement-rehearsal/) | Seven-day pilot passed: 36 operations and three checkpoints; [final decision](w9-replacement-readiness-decision.md) approved by Magnus on September 30 |
| Adaptive research policy | [W10 brief](adaptive-policy/w10-research-brief.md), [frozen protocol](adaptive-policy/w10-frozen-protocol.md) | Complete. The bounded adaptive policy was rejected; accepted [ADR-0081](../adr/experimental/groktocrawl-x/0081-select-a-bounded-adaptive-research-policy.md) retains fixed retrieval as default and contracts for future trials. |
| SlopSearX research substrate | [W11 findings](slopsearx-substrate/w11-findings.md), [protocol](slopsearx-substrate/w11-protocol.md), [operator assessment](slopsearx-substrate/w11-operator-assessment.md) | Complete. Narrow adoption keeps HTTP search as default, rejects broad recorded continuation, and retains selected opt-in provenance and recovery contracts. |
| Research Mission contract | [W12.1 brief](research-mission/w12.1-experiment-brief.md), [protocol](research-mission/w12.1-protocol.md), [analysis plan](research-mission/w12.1-analysis-plan.md), [research log](research-mission/w12.1-research-log.md), [v1 freeze](research-mission/w12.1-freeze.json), [v2 freeze](research-mission/w12.1-freeze-v2.json) | Complete. The universal typed mission was rejected in the [final comparison](evidence/research-mission/w12.1-final/00-index.md); accepted [ADR-0083](../adr/experimental/groktocrawl-x/0083-retain-prose-intake-over-universal-research-missions.md) retains prose intake. Earlier failed launches remain historical evidence. |
| Longitudinal Research Thread | [W12.2 brief](research-thread/w12.2-experiment-brief.md), [protocol](research-thread/w12.2-protocol.md), [research log](research-thread/w12.2-research-log.md), [v1 freeze](research-thread/w12.2-freeze.json), [v2 freeze](research-thread/w12.2-freeze-v2.json), [v3 freeze](research-thread/w12.2-freeze-v3.json), [evidence](evidence/research-thread/w12.2-final/00-index.md) | Complete. The tested default Research Thread is rejected: it was less accurate on fully observed pairs, slower, more token-intensive, and produced two stale-current leaks. Accepted ADR-0084 retains independent roots and explicit comparisons. |
| Independent claim verification | [W12.3 brief](claim-verification/w12.3-experiment-brief.md), [protocol](claim-verification/w12.3-protocol.md), [research log](claim-verification/w12.3-research-log.md), [v1 freeze](claim-verification/w12.3-freeze.json), [v2 freeze](claim-verification/w12.3-freeze-v2.json), [evidence](evidence/claim-verification/w12.3-final/00-index.md) | Complete. The bounded verifier passed every frozen gate in all three repetitions. Accepted ADR-0085 adds it as an experimental stage before source-bound claim publication. |
| TypeSafe Jev evidence routing | [Research brief](typesafe-jev/research-brief.md), [protocol](typesafe-jev/protocol.md), [research log](typesafe-jev/research-log.md), [exposed corpus](typesafe-jev/exposed-routing-corpus.json) | A bounded live technical smoke is complete on exposed synthetic cases. The adapter remains shadow-only and keyless-safe; no matched product-value measurement or adoption decision exists. |
| October 3 search and Jev follow-ons | [Upstream reconciliation](upstream-search-reconciliation.md), [issue criteria and decisions](typesafe-jev/issue-disposition-370-373.md) | Bounded studies recommend revision or continued shadow testing. Whole-set ranking did not pass its quality gate; contribution-filter answer quality remains unmeasured after a stopped synthesis attempt. No defaults or production policy change. |

Update this guide and the roadmap whenever an ADR changes state, a workstream
starts or finishes, or a new experiment becomes part of the replacement decision.

The [frozen cardiac Jev replay](cardiac-jev-replay-2026-10-04.md) reuses the 44-card upstream corpus with zero new searches. The split evidence question was more stable in this replay; fetch judgments remained context-sensitive. All 28 calls and the initial validation rejection are retained. No runtime change.
