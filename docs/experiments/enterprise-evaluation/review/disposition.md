# Hermes design review disposition

One fresh one-shot review was requested by Codex under Magnus's authorization.
`prompt.txt` and `response.txt` preserve the exact reviewed initial packet and raw
response. `metadata.json` records their hashes and reported invocation metadata.
Hermes reported model alias `luna`, provider `custom`; this is not evidence of model
family/training independence. Billing status was unknown. No human labels, blind
outcome review or comparative run occurred.

Hermes judged the initial packet **needs revision**. The following revisions address
its actionable design findings; Hermes has not re-reviewed this updated version.
Do not report its initial review as approval of the revised packet.

| Finding | Disposition in revised packet |
|---|---|
| P1 exposure not operationally controlled | Current candidate split renamed exposed calibration, exact repeated dev questions removed, exposure ledger says zero held-out-eligible cases. Future sealed curator/access boundary and invalidation rule specified. Disagree only with discarding useful exposed *calibration* items; they are barred from held-out scoring. |
| P1 30 questions could mean repeated templates | Future gate explicitly requires 30 unique non-template questions; current template families remain clustered and are not held-out evidence. |
| P1 adverse denominator ambiguous | Manifest gives question numerator/denominator separately; runtime plan declares six adverse of 12 planned scenarios per arm. Zero scenarios executed by this packet. |
| P1 textual refusal not an authority test | Actual hostile/mirror source cards added. Separate runtime catalog specifies tool/approval/effect/receipt/cancel events and independent stub-effect oracles. No executor/coverage claim. |
| P1 scoring/acceptance incomplete | Per-question formulas, partial credit, undefined denominators, errors/timeouts, independent metric failures, critical finding veto and inconclusive rule added. Numeric performance bounds still require baseline, as the ADR mandates. |
| P1 author expectations could become gold | Explicit blinded human calibration sequence and prohibition on author-expectation gold labels. No calibration has happened; execution/quality acceptance stays blocked. |
| P2 A/B/B/C attribution incomplete | Changed factor, paired unit, constants and arm-implementation gaps added in comparison matrix. |
| P2 invariant coverage uncounted | Planned runtime scenario inventory and event denominators distinguish design from actual coverage. Missing oracle results fail; no zero-risk claim. |
| P2 randomization/retry/cache rules missing | Seeded paired order, no retries of scored trials, retained harness reruns, cold/warm separation and state controls specified for future freeze. |
| P2 runtime workload/measurement bounds | Planned catalog supplies task identities/oracles; end-to-end admission-to-terminal timing, timeout and failure categories defined. Executable workloads and hardware remain blocked inputs. |
| P2 cost/resource boundary | Requested/actual tokens, cache/reasoning fields, attempts, queue/cleanup time and billing provenance required; unknown cost is not zero. |
| P2 calibration failure undefined | Stratified 12-case debugging protocol, false critical accept veto, unresolved material disagreement block and fresh-item recalibration after judge changes added. One-human conclusions remain provisional. |

The revised packet is a proposal suitable for review and harness/rubric preparation.
It is not an execution approval or adoption claim. Human calibration, real-world
curation, sealed questions, implemented arms, measured baseline and frozen numerical
bounds remain prerequisites for the applicable future comparison.
