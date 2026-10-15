# GroktoCrawl X ADR provenance archive

These records preserve the original GroktoCrawl X ADR file names, wording, decisions, status, and experimental acceptance history. Their former sequence overlaps the mainline ADR-0067 and ADR-0068, so they are archived here and are not part of the active mainline ADR numbering or status index.

The records document decisions scoped to the former bounded experiment. A record marked accepted there remains accepted only for the scope stated in that record; it does not by itself authorize a mainline deployment default, public API guarantee, provider use, storage cutover, or production migration.

Mainline adoption is defined by [ADR-0069](../../0069-adopt-bounded-research-substrate.md). It adopts the research substrate in the standard deployment architecture, preserves existing interfaces and legacy persisted state, and retains feature gates during rollout. It does not adopt every archived decision. The archived source branch was `experiment/main` at `9c7744b66f205080a44bfd89808494dbe80eb5a4`.

| Original record | Mainline disposition |
|---|---|
| X ADR-0067–0068 | Historical experiment charter and architecture rationale; adopted only within ADR-0069's bounded mainline scope. |
| X ADR-0069–0072 | Historical knowledge, evaluation, evidence-store, and client protocol decisions; use ADR-0069 for the mainline adoption boundary. |
| X ADR-0073–0079 | Historical runtime, recovery, interchange, publication, import, backup, and storage decisions. Their experiment-specific acceptance and evidence remain scoped to the archived record. |
| X ADR-0080 | Rejected experiment decision; preserved as historical evidence, not adopted. |
| X ADR-0081–0090 | Historical bounded policy, retrieval, intake, verification, barrier, browser, and optional-filter decisions. They do not set mainline defaults. |
| X ADR-0091–0092 | Historical search-acquisition decision and successor. The behavior is subject to the current mainline API and implementation contracts; the archived acceptance alone does not change them. |
| X ADR-0093–0099 | Proposed or bounded experimental changes. They remain proposals or historical experimental outcomes unless a later mainline ADR says otherwise. |

The experiment's original plan and evaluation evidence remain under [`docs/experiments/`](../../../experiments/README.md). Relative links in these archived ADRs are adjusted only where moving the files changed their location; the decision text and status are otherwise retained.
