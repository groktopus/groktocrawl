# W1 incumbent semantic review — 2026-09-09

Status: **primary review complete; comparison remains blocked pending candidate-arm
identity and human adjudication**.

The evaluation owner reviewed all 30 outputs from the sealed W1 incumbent run
against their private questions, source bundles, as-of dates, and fixed required
subquestions. Failed and empty outcomes remained in the denominators. Raw questions,
sources, answers, and item-level rationales remain in the mode-700 private packet;
this record contains only safe aggregates and content identities.

## Result

| Measure | Incumbent observation |
|---|---:|
| Cases reviewed | 30 / 30 |
| Material assertions | 77 |
| Strictly supported or legitimate explicit inference | 54 / 77 (70.1%) |
| Required-subquestion coverage credit | 62 / 85 (72.9%) |
| Correct supporting citations | 54 / 77 (70.1%) |
| Ready to use | 10 / 30 |
| Needs a fix | 15 / 30 |
| Do not use | 5 / 30 |
| Critical findings | 1 |
| Major findings | 15 |
| Minor findings | 4 |
| Failed or empty model outcomes | 2 / 30 |

The critical finding was an answer recommending broad cluster authority for an
orchestrator despite the supplied least-privilege evidence. The baseline therefore
fails the hard acceptance rule of zero unresolved critical authorization findings.
The incumbent remains useful as the paired reference; it is not an acceptable
quality target for the replacement candidate.

Private record identities:

- incumbent results: `sha256:31e7baac53361f7218eaf04d47a37f33c736e3872209b0ba31693bd642320ce8`;
- primary semantic review: `sha256:33523098685f45b6d38fdc4df884b3e3273b7c8625486ce9ce16b61b42136800`;
- corpus and access-log identities remain those in the frozen baseline input record.

## Independent-review outcome

Hermes was invoked through its one-shot CLI. Two otherwise complete responses were
excluded because the normal Hermes fallback chain routed them to the Nous provider,
violating this packet's local-only execution rule. The first excluded call requested
`luna`; the second requested `local` but fell back after the local route failed. The
usage records, outputs, and exclusion reason remain in the private packet and do not
contribute to any score.

A separate temporary Hermes profile removed every fallback destination and pinned
the home-lab LiteLLM `local` route. A trivial route probe passed, but substantive
grading timed out both for five-case batches and for a single compact case. No
local-only Hermes semantic label was obtained. This is a fail-closed reviewer
availability result, not permission to use the excluded labels.

The primary result is consequently provisional. Magnus remains the sole human
adjudicator; all critical findings and any future candidate disagreement require
explicit human disposition before an adoption claim.

The act of primary review exposed this packet to the implementation agent. Its
post-review access-log digest is
`sha256:d6b31f8e034981e5c6a308b3d79da69a3afcc93e28e000d9ad899f935ad82107`,
and the held-out validator now fails the future-isolation check as intended. These
cases remain valid for the incumbent baseline and bound setting. A fresh packet
must be independently curated after candidate B is frozen for any scored A/B claim.

## Proposed comparison bounds

These bounds are derived from the measured incumbent rather than presented as
industry targets:

- zero unresolved critical unsupported, authorization, provenance, or side-effect
  findings in the candidate;
- retain all assigned trials, including failures, and require 100% case accounting;
- for strict support, required coverage, and citation correctness, the candidate's
  paired question-level 95% interval must not show regression; no point estimate may
  be more than two percentage points below the incumbent;
- to support the experiment's claim that the candidate improves on the incumbent,
  at least two of those three primary quality measures must improve by five or more
  percentage points, with no new critical finding;
- candidate answer latency may be at most 1.25 times the incumbent observation:
  p50 at most 8,030 ms and p95 at most 10,354 ms. Timeouts count at the ceiling and
  as failed task coverage;
- compare token and source work transparently. Candidate totals may be at most three
  times the incumbent per-question median unless the paired quality improvement is
  established and the final decision explicitly accepts the additional work;
- use five trials per case and arm, randomize paired order with a frozen seed, and
  bootstrap paired questions with 10,000 resamples. Report the six topic families
  separately and never treat repeated trials as independent questions.

These are ready for decider review. The model route, host-equivalence rule, seed,
paired ordering, call limits, and stop mechanism are now pinned in
`research-preflight.json`. The exact candidate policy and commit remain unresolved,
so candidate execution is still blocked.
