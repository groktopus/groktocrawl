# W7 Candidate D comparison outcome — 2026-09-11

Status: **execution and review complete; revise the successor; do not adopt
Candidate D**

Candidate D does not qualify as the replacement research policy. It completed
only 4 of 150 scheduled attempts, compared with 134 for the incumbent. That
failure is decisive under the first hard gate. The result rejects this frozen
implementation, not the evidence-first architecture or the optional future use
of LangGraph.

The comparison used the independently curated private 30-case packet, five
trials per case and randomized paired ordering recorded in the authorized
proposal. All 300 attempts remain in the denominator. Generation and grading
artifacts were frozen before analysis; questions, sources, answers and item-level
reviews remain private.

## Operational result

| Measure | Arm A: incumbent | Arm D: lean successor |
|---|---:|---:|
| Scheduled attempts | 150 | 150 |
| Completed answers | 134 (89.33%) | 4 (2.67%) |
| Failed attempts | 16 (10.67%) | 146 (97.33%) |
| p50 attempt latency | 10,513 ms | 24,223 ms |
| p95 attempt latency | 49,441 ms | 70,583 ms |
| Provider calls | 150 | 163 |
| Calls per scheduled attempt | 1.000 | 1.087 |
| Reported tokens per scheduled attempt | 9,198 | 6,155 |

Candidate D's p50 latency was 2.30 times the incumbent and exceeded the 1.5-times
limit. Its p95 was 1.43 times the incumbent and remained within that limit. Its
reported token use was lower than the incumbent and within the two-times limit.
Those resource results cannot compensate for the completion failure.

Candidate D failed 72 attempts on construction-shape validation, 72 on its
fail-closed publication policy, and two on timeouts. Its four completed answers
included two with deterministic complete coverage and two with partial coverage.
Arm A failed thirteen attempts on citation validation and three on model
transport.

## Blinded semantic review

The blinded grader received all 138 completed generation outputs without arm
labels. It returned 126 valid grades. Twelve Arm A grades were invalid because
their subquestion denominator differed from the frozen case definition; those
remain missing rather than being repaired or treated as failures after unblinding.

| Valid-grade result | Arm A | Arm D |
|---|---:|---:|
| Valid grades | 122 | 4 |
| Do not use | 17 | 1 |
| Needs a fix | 4 | 1 |
| Cannot tell from supplied evidence | 101 | 2 |
| Source support: pass / partial / fail / indeterminate | 0 / 118 / 2 / 2 | 0 / 4 / 0 / 0 |
| Citation correctness: pass / partial / fail / indeterminate | 2 / 37 / 0 / 83 | 0 / 0 / 0 / 4 |
| Subquestion coverage: pass / partial / fail / indeterminate | 0 / 46 / 2 / 74 | 0 / 0 / 0 / 4 |

The grader flagged 72 answers for high-consequence review. Every flag was
reviewed individually under the frozen distinction between an unusable answer
and actionable dangerous advice. All 72 answers had a material error, omission
or unsupported action that prevented safe reliance on the answer. Thirty-two
also contained a concrete dangerous unsupported recommendation; all 32 belonged
to Arm A. The one flagged Candidate D answer was an unusable framework summary,
but it did not direct a dangerous recovery action. Forty dangerous-advice flags
were dismissed because the response was empty, refused, expressed uncertainty,
or gave generic ungrounded guidance without a concrete harmful action.

The four Candidate D grades are far too sparse to establish a quality advantage.
They do establish that no dangerous Candidate D recommendation survived review
among its four completed answers. This satisfies the narrow Candidate D safety
condition while leaving the comparative quality condition unevaluable.

## Gate decision

| Candidate D gate | Result |
|---|---|
| Complete at least as many attempts as Arm A | **Fail:** 4 versus 134 |
| No upheld dangerous unsupported recommendation | Pass within four completed answers |
| Improve support and citations without reducing full-study coverage | **Insufficient evidence:** only four valid Candidate D grades |
| p50 and p95 no more than 1.5 times Arm A | **Fail:** p50 2.30 times; p95 1.43 times |
| Reported tokens per scheduled attempt no more than 2 times Arm A | Pass: 0.67 times |

The decision is **revise**. Candidate D must remain disabled and must not replace
the incumbent. ADR-0080 is rejected as an implementation decision for this
candidate. Its useful constraints remain design input: application-owned source
identities and citations, visible missing coverage, deterministic validation and
runtime-neutral publication contracts.

W8 may now begin. Its acquisition, replanning, diversity and freshness studies
must improve the evidence entering the research system before another answer
policy is frozen. A future candidate needs a new ADR, a new identity, a fresh
held-out packet and a separately authorized comparison. It should simplify the
construction contract and validate model-schema compatibility before any large
run.

## Evidence identities

| Private artifact | Digest |
|---|---|
| Generation freeze | `sha256:35e0e0d79cd66270ef4a56828336322c9003909b1b03cc4898b28653dd8cbf57` |
| Generation results | `sha256:b5dbf87d329eee00be68beff254806929e965372a87e0589381370608cd7fe2b` |
| Generation receipts | `sha256:2aa1d87e3161f4b36b30c5ab382cb023a77e8f9ab12ee1f0b950ad0c86d810a8` |
| Grading freeze | `sha256:1348600ee4b1618787801d3aef5e9232b856832b71e8491e66a7a39e4ef74fdc` |
| Blinded grades | `sha256:c1eea591b1520bc84862b40adf4ab4f897fa01b3e9261d257bfb1dcd071fde05` |
| High-consequence review | `sha256:670898065235d9c2c92873f337abf9775dfa4b71c69deda895e4035e491d280a` |

This result does not authorize production adoption, mainline replacement,
deployment, migration or deletion of retained evidence.

