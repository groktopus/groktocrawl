# W1 incumbent baseline — 2026-09-08

Status: **incumbent answer run complete; held-out eligibility approved; comparison
remains blocked pending bounds and protocol approval**.

Magnus approved the W1 packet proposal in [issue #116](https://github.com/magnus919/groktocrawl-x/issues/116#issuecomment-5587198116).
The separate packet was curated by a Hermes one-shot process in a private mode-700
temporary directory outside the repository and tuning workspace. The packet itself
is intentionally not committed here; publishing its questions or source text would
destroy the isolation needed for the eventual review.

## Packet integrity

The structural validator passed the candidate packet without exposing its contents:

| Field | Value |
|---|---|
| Cases | 30 |
| Topic categories | 6, five cases each |
| Template families | 6 |
| Adverse/abstention cases | 6 (20%) |
| Candidate corpus digest | `sha256:659200d31ea23b17dc497b658c6c44db277005ed91a74fb962b10c6cebbf27c3` |
| Access-log digest after named review | `sha256:41e0d1be2c6ae5a8e35a628c5cb0c8bc7ec7d4dfce875bf98634b9817957d155` |
| Structural result | `candidate_validation_passed` |
| Held-out eligibility | `true` after the named reviewer accepted the access/isolation record |

The validator also found no case-ID or normalized-question overlap with the exposed
development corpus. A digest proves integrity, not independence; the eligibility
flag therefore remains false by design.

## Incumbent answer run

The incumbent was run first through the existing internal LiteLLM gateway using the
`local` alias and zero external provider spend. The runner made one bounded answer
call per case and retained failed and malformed responses as outcomes.

| Measurement | Observation |
|---|---|
| Cases attempted | 30 |
| Calls dispatched | 30 |
| Valid answers | 29 |
| Failed answers | 1 malformed JSON response |
| Reported model | `local` |
| Total prompt tokens | 5,944 |
| Total completion tokens | 2,444 |
| Total tokens | 8,388 |
| Answer latency p50 | 6,424 ms |
| Answer latency p95 | 8,283 ms |

These are baseline observations only. No semantic labels, quality score, regression
bound, runtime comparison, or adoption conclusion is inferred from this run.

## Next gate

The packet remains private after the named reviewer accepted the curator, access log,
and isolation method. The frozen input record is in
[w1-baseline-inputs-2026-09-08.md](w1-baseline-inputs-2026-09-08.md). Numerical
bounds, candidate arm identities, execution limits and the scoring protocol still
need review before a paired quality or runtime series can be authorized. Failed,
timed-out, and malformed trials remain in every denominator.
