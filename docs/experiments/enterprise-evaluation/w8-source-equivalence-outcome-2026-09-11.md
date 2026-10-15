# W8 blinded source-equivalence outcome — 2026-09-11

Status: **audit complete; retain fixed-query retrieval and use adaptive planning only for declared evidence gaps**

The audit revisited every unique query/source pair returned by the W8 fixed-query
baseline and bounded adaptive-planning runs. Retrieval arm and rank were hidden
until all 1,028 candidate decisions were validated and frozen. The reviewer used
the accepted rubric to distinguish an exact reference, substantively equivalent
evidence, related but insufficient material, unavailable or indeterminate material,
and unrelated material. Exact-reference labels were allowed only when URL or
redirect identity established the match deterministically.

The private pool contained 965 unique candidate URLs and 1,028 query/source pairs.
Acquisition produced usable reviewed text for 894 candidates and recorded 82 as
unavailable. The local model graded acquired sources from query-directed excerpts;
the acquisition gate assigned the unavailable label deterministically. All model
responses were validated against their per-item label and reference constraints.

## Results

| Measure | Fixed-query baseline | Bounded adaptive planner |
|---|---:|---:|
| Result sightings | 463 | 778 |
| Exact references | 17 | 14 |
| Substantively equivalent | 383 | 579 |
| Related but insufficient | 15 | 68 |
| Unavailable or indeterminate | 33 | 64 |
| Unrelated | 15 | 53 |
| Useful sightings | 400 (86.4%) | 593 (76.2%) |
| Descriptive 95% Wilson interval | 83.0–89.2% | 73.1–79.1% |
| Cases with a useful source | 24/24 | 23/23 |
| Mean reciprocal rank of first useful source | 0.958 | 0.971 |

The adaptive arm produced no distinct pooled source for one of the 24 cases, so
its case denominator is 23. Both arms found useful evidence for every case in
which they produced a candidate. The first useful source was near rank one in
both arms, with a small descriptive advantage for the adaptive arm.

The larger difference is source-set precision. The baseline's useful-source rate
was 10.2 percentage points higher. Adaptive planning added 193 useful sightings,
but it also added 122 insufficient, unavailable, or unrelated sightings and spent
45 additional searches plus 24 model calls in the underlying planning run. The
Wilson intervals describe sightings only; repeated results are clustered by case
and search, so they are not an independent-sample significance test.

## Decision

The audit confirms that exact-URL scoring materially understated retrieval quality.
The earlier comparison counted a known-useful URL in only 14 of 24 baseline cases;
the blinded semantic audit found useful evidence in all 24. The exact-URL result
was reproducible, but it was too narrow to represent actual evidence coverage.

This strengthens the earlier W8 decision. Keep ordinary SlopSearX retrieval as the
default acquisition path. Do not run the evaluated adaptive planner on every
question: baseline retrieval already supplied useful evidence for every audited
case, at materially higher source precision and far lower cost and latency. Retain
bounded query formulation as a recovery capability invoked by explicit missing-
evidence signals. Its future value depends on better sufficiency detection and
source-quality selection, not broader indiscriminate fan-out.

The result also agrees with the W8 source-diversity decision: source count is not
evidence quality. Selection should collapse canonical copies, favor primary and
independent publishers, and stop when the declared evidence need is met. Search
snippets remain discovery hints; exact acquired bytes and provenance remain the
evidence boundary.

## Limits and private evidence

This is a retrospective audit of one frozen 24-question packet. A single local
model applied the rubric to bounded excerpts, and unavailable pages could not be
semantically judged. The study measures the evaluated retrieval runs; it does not
establish Internet-wide quality, future engine behavior, or production latency.

Private item-level records remain outside the repository. The public result is
bound to these digests:

- arm-blinded review pool: `sha256:426f89a502b898c15f534e894530b9ca5b5f9b2e64e578bb7b19fb64219d0b33`;
- accepted rubric: `sha256:c58fa83f7a02870472c9139401559ba7a897b1753c4de4d3001fcbac948e1d38`;
- frozen blind grades: `sha256:4794bb042d8f241423e2a74407a2797bdee48cbff2a9f032a8c973dd3d229882`;
- sealed arm map: `sha256:fc9d8156330c8a22a08a62b46147fb4b7d5cfcefff0384c94f5acbf730cbdd22`.

W8 is complete. The next gate is W9 replacement rehearsal: deploy the isolated
candidate, prove compatibility, rehearse migration and rollback, and run the
bounded side-by-side operational pilot before making an adoption decision.
