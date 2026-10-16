# W1 paired A/B outcome — 2026-09-10

Status: **execution complete; Candidate B fails the operational acceptance gates;
semantic review remains open for learning**

The authorized clean run executed all 300 scheduled attempts: 30 private cases,
five trials per case, and both frozen arms in randomized paired order. Every failed
attempt remains in the result. The private questions, sources, answers, and
item-level errors remain outside the repository.

## What happened

| Measure | Arm A: incumbent | Arm B: evidence-first |
|---|---:|---:|
| Scheduled attempts | 150 | 150 |
| Completed answers | 77 (51.3%) | 39 (26.0%) |
| Failed attempts | 73 (48.7%) | 111 (74.0%) |
| p50 attempt latency | 2,189 ms | 17,570 ms |
| p95 attempt latency | 3,202 ms | 22,367 ms |
| Provider calls | 150 | 293 |
| Reported tokens | 49,387 | 777,108 |
| Mean reported tokens per scheduled attempt | 329 | 5,181 |

Across the 150 matched case/trial pairs, both arms completed 24, only Arm A
completed 53, only Arm B completed 15, and neither completed 58.

Arm A's 73 failures were all citation-shape failures: the model returned a response,
but the citations did not match the allowed private source identities. Arm B failed
95 times because its publication gate rejected an ineligible claim, eight times
because freshness evidence could not authorize a pass, seven times on strict
construction-shape validation, and once because no model review judgment was
accepted. These are fail-closed outcomes rather than missing records.

## Decision evidence

Candidate B exceeds the accepted latency ceilings of 8,030 ms p50 and 10,354 ms
p95 by more than two times. Its total reported model work is 15.7 times Arm A's
across the same number of scheduled attempts, well beyond the three-times resource
ceiling. It also completes roughly half as many attempts. These independent hard
failures are sufficient to reject Candidate B as the current replacement, even
before semantic scoring of the completed answers.

This does not show that the evidence-first substrate is the wrong direction. Most
Candidate B failures came from the new system refusing to publish claims it could
not prove, which is behavior we want to preserve. The result shows that the current
policy asks a small local model to construct and then police a rich evidence graph
in two large calls. That design is too brittle and expensive today.

The next candidate should retain typed evidence, deterministic mechanical checks,
and fail-closed publication while reducing how much schema and judgment the model
must produce. The completed private outputs should receive semantic review to learn
whether Candidate B's 39 accepted answers are materially better when it succeeds.
That review cannot overturn the operational rejection, but it can guide the next
architecture iteration.

## Evidence identities

| Private artifact | Digest |
|---|---|
| Run manifest | `sha256:81c796f90df768a96e1f3fa5201196a40e5c10ac08719777d8fce753476cc14a` |
| Frozen schedule | `sha256:8d3d0f4fef37a7610ab1718c9eb248078b76b2cdbdddfa2c01eac120630b496a` |
| Attempt results | `sha256:b2c633a69280f3f587d66fbfe5a08b0fd460f355faa285212290a4c436873604` |
| Model receipts | `sha256:b8783595c21de3e9afff2cc738d1bba534fd83db473ba8b966f74b92d640ca2d` |

No production adoption, mainline replacement, or automatic removal of the
incumbent follows from this result.
