# Competing explanations and decision pre-mortem

## Competing explanations

| Hypothesis | Result | Evidence and interpretation |
|---|---|---|
| H0: fixed retrieval already closes the material gaps | Supported for the tested cases | Fixed retrieval closed 86.4% of weighted anchor claims. Full adaptation closed 53.0%, and no challenge type cleared its replacement gate. This establishes sufficiency only for the frozen cases and environment. |
| H1: broad or drifting follow-up queries caused the W8 weakness | Partly supported | Binding queries to gaps and gating proposals did not rescue the full policy. Across the anchor, only 5 of 45 accepted follow-ups produced a declared gain. Query control matters, but the tested proposal gate accepted too many low-yield continuations. |
| H2: weak evidence admission diluted useful retrieval | Supported, without establishing a replacement | The full policy's marginal-value filter raised anchor precision from 43.0% before admission to 51.0% after admission. That nearly matched fixed retrieval's 52.6%, but closure still fell sharply. Admission filtering removed weak material but could not recover evidence the policy failed to retain or recognize. |
| H3: searches continued after marginal value collapsed | Strongly supported | The full policy's useful-query yield was 11.1% and its unnecessary-query rate was 88.9%. Its interim `all gaps closed` judgment disagreed with the separate final assessment in 21 of 40 applicable trials. The tested stopping signal is not dependable. |
| H4: adaptation helps only recognizable challenge types | Not demonstrated | No challenge type passed in any repetition. Removing one of twelve challenge cases selected `missing_primary` before the anchor gate, so there is a fragile narrow signal, not evidence for adoption. |
| H5: the W8 result was mainly grading or weighting sensitivity | Weakened | Independent blinded adjudication changed many individual judgments but preserved `retain_fixed_default`. Equal claim weights also preserved the failed anchor gate. Case composition still deserves attention because one leave-one-out result changed the pre-anchor selection. |

The linchpin is the anchor closure loss. Precision alone was within the declared
margin, and every trial respected its hard bounds, but a policy that loses 33.3
percentage points of weighted closure on the established cases cannot replace
the fixed default. The very low useful-query yield and unreliable stop judgment
explain why additional work did not translate into additional research value.

## Decision pre-mortem

Assume retaining fixed retrieval proves wrong in a year. The most plausible
failure is that search engines and models improve enough that a stateful
continuation protocol begins recovering evidence that one-shot search misses,
while this decision is mistakenly treated as a permanent ban on adaptation.
That would leave difficult research questions with avoidable gaps.

The guard against that failure is to preserve the evidence-state, receipt,
candidate-disposition, budget, and stop contracts as an experimental seam. W11
may compare SlopSearX recorded continuation with the fixed control, and future
studies may test materially different triggers. They must retain matched cases,
independent final assessment, useful-query yield, and the anchor
non-inferiority gate.

Another failure would be optimizing future policies to this small frozen set.
The twelve challenge cases measure defined failure modes rather than the full
population of research questions. New studies must add preregistered cases and
report old and new strata separately instead of replacing the anchor or tuning
against hidden labels.

A third failure would be interpreting the agent adjudicator as ground truth.
Agreement was 64.9% on source usefulness and 30.4% on claim status. The decision
survived that sensitivity, but future adoption still requires evidence that its
benefit is robust to judge choice and does not depend on silently repairing
malformed outputs.

## Reversal evidence

Reconsider the decision when a materially different adaptive controller, on a
frozen matched comparison, clears every per-type value and safety gate in at
least two of three repetitions and keeps closure and precision within two
percentage points of fixed retrieval on the W8 anchor. The controller must also
show that accepted follow-ups usually produce observable evidence-state gains,
that its stop decision agrees with an independent final assessment, and that the
result survives blinded adjudication and case-removal sensitivity.

## SOURCES

- [Policy effects](policy-effects.md)
- [Boundary and sensitivity analysis](boundaries-and-sensitivities.md)
- [Independent adjudication](../03-dossiers/adjudication.md)
- [Frozen protocol](../../../../adaptive-policy/w10-frozen-protocol.md)
