# W1 semantic learning review — 2026-09-10

Status: **complete; human adjudication accepted**

After all A/B outputs were frozen, the home-lab `local` model reviewed every
completed answer against its private question, required subquestions, as-of date,
and source bundle. Failed attempts retained zero task coverage and received no
semantic grade. Item-level material remains private.

| Completed-output measure | Arm A | Arm B |
|---|---:|---:|
| Completed outputs | 77 | 39 |
| Successfully graded | 74 | 37 |
| Strict support among graded assertions | 208 / 249 (83.5%) | 79 / 121 (65.3%) |
| Subquestion coverage among graded outputs | 62.5 / 78 (80.1%) | 32 / 37 (86.5%) |
| Full-study coverage with failed attempts at zero | 62.5 / 150 (41.7%) | 32 / 150 (21.3%) |
| Citation correctness among graded citations | 85 / 97 (87.6%) | 41 / 58 (70.7%) |
| Automated critical flags | 13 | 10 |

The reviewer failed to return a usable grade for three completed Arm A outputs and
two Arm B outputs. Those gaps remain explicit.

## Critical-flag review

The implementation reviewer inspected all 23 automated critical flags against the
private evidence. None is upheld as a critical authorization, provenance,
side-effect, or dangerously unsupported conclusion. Many rationales contradict the
answer and sources they purport to grade: for example, they mark correct refusals,
least-privilege constraints, and warnings against blind retries as dangerous. Some
answers still have ordinary support, precision, or citation problems, but those do
not meet the frozen critical threshold.

Magnus accepted this disposition on 2026-09-10. Zero of the 23 automated critical
flags are upheld. This closes the critical-flag adjudication only: it does not
repair the ordinary support, precision, coverage, or citation problems, and it
does not change Candidate B's operational rejection.

## What this teaches us

When Candidate B completed, it covered slightly more of each requested question
than completed Arm A answers. It was substantially worse on strict support and
citation correctness, and its much lower completion rate reduced full-study
coverage to about half of Arm A's. The architecture therefore added ceremony and
model work without producing more dependable grounded answers.

The next design should keep typed evidence and the deterministic publication gate,
but should extract small source-bound answer units instead of asking the model to
author a whole graph and then review its own graph. Citation identity should be
assigned by application code from selected passages. Model judgment should focus
on semantic support and uncertainty, with the application owning structure,
counting, provenance, and final citation rendering.

## Private evidence identities

- item review: `sha256:854c09da48d2fdd7177b65951b2e125417c46daee7a9a1eec54ced563e289bfe`;
- aggregate summary: `sha256:cc7e0016bd1dc35e44bf310cbf0c9e22328c1f59f59d6d50d2a7daf4a239c4f2`.

The semantic reviewer used the same local model route involved in generation. This
is a learning review, not an independent judge study, and it cannot support a
production-adoption claim by itself.
