# W10 adaptive-query policy research brief

## Decision

Decide whether any bounded adaptive-query policy should replace fixed retrieval
for identifiable evidence gaps in the experimental research substrate. Fixed
retrieval remains the default unless the frozen comparison clears every primary
gate.

## Research question

On matched cases with declared material claims, does gap-triggered adaptation
close more important evidence gaps than fixed retrieval without losing more than
five percentage points of admitted-source precision or exceeding three searches,
three model calls, or 180 elapsed seconds per case?

## Competing explanations

- H0: fixed retrieval already closes the material gaps.
- H1: W8 generated broad or drifting follow-up queries.
- H2: useful retrieval was diluted by weak evidence admission.
- H3: later searches continued after marginal value collapsed.
- H4: adaptation helps only recognizable challenge types.
- H5: W8's result is sensitive to grading, unavailable pages, or clustering.

The linchpin evidence is a repeated, challenge-stratum improvement in weighted
claim closure that survives sensitivity checks while maintaining precision and
work bounds. Without that evidence, fixed retrieval stays the default.

The interpretation and boundary analysis are declared in
`w10-analysis-plan.md`. That plan does not alter the frozen cases, outcomes, or
decision gates.

## Inclusion and exclusion rule

Include only trials produced from the exact frozen source, case, protocol, and
runner digests that pass the independent run validator. Preserve but exclude
interrupted trials and trials affected by a discovered accounting, attribution,
stopping, or proposal-state defect. Do not infer a policy result from partial
evidence. Every exclusion and its evidence-tree digest remains in the protocol
and research log.

## Confidence and scope

The study targets agentic engineering software factories in enterprise settings.
It uses the frozen W8 corpus as an anchor plus a deliberately difficult stratum.
The result can select a policy for this research substrate; it cannot establish
Internet-wide retrieval quality or generalize to every research domain.

## Pre-mortem

The favored policy could look successful by choosing cases that fixed retrieval
was designed to fail, counting many low-value sources as closure, treating copied
reporting as independence, letting the grader infer support from snippets, or
stopping only after seeing the desired outcome. The protocol counters these risks
with separate strata, claim-level weights, acquired-source grading, canonical and
publisher clustering, frozen stop rules, blinded arm labels, and sensitivity
analyses that remove unavailable and linchpin cases.
