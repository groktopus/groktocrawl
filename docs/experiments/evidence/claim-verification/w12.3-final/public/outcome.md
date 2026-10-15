# W12.3 independent claim-verification outcome

Decision: **adopt the bounded verifier for source-bound publication candidates**

## Frozen gates

| Gate | Result | Evidence |
|---|---|---|
| Critical false accepts fall at least 30% in two repetitions | Pass | 100% reduction in all three |
| Missed contradictions fall | Pass | 2 to 0 in every repetition |
| False rejection rises no more than 10 points | Pass | No false rejection |
| Inappropriate abstention rises no more than 10 points | Pass | No inappropriate abstention |
| Calibration improves | Pass | Brier 0.667 to 0.129, 0.129, and 0.024 |
| No authority, provenance, or hostile-instruction failure | Pass | Zero hard failures |
| High-risk p95 latency at most 120 seconds | Pass | 22.3 seconds |
| Mean incremental calls at most 1.2 | Pass | 1.0 call |
| Terminal failures below 10% | Pass | 0 of 36 |

## What the verifier changed

The structurally eligible control published all 12 cases in each repetition. Four
were truly supported. The other eight included unsupported certification language,
missing residency evidence, contradictory delivery evidence, a tentative roadmap,
stale pricing, derivative-only adoption claims, ambiguous product identity, and a
hostile instruction embedded in source text.

The verifier published all four supported claims and withheld all eight unsafe or
unresolved claims in every repetition. It cited the required spans in every case.

## Architectural implication

Add a semantic verification record between source-bound claim construction and
publication eligibility. Keep it blinded to generator rationale and expected
answers. The verifier may recommend publication, but the existing publication gate
retains authority and must validate the exact input, evidence identities, verdict,
confidence threshold, and policy version.

Begin as an experimental, observable stage. The General alias must be pinned by
receipt, failures must remain fail-closed, and live traffic must monitor false
rejection, latency, route drift, and production prevalence before removing the
feature flag.
