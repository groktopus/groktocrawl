# W10 pre-execution smoke test

This packet preserves the failed smoke test that changed the acquisition
allocation before comparative execution. It is diagnostic evidence and is
excluded from W10 outcome calculations.

- Source revision: `416ad08`
- Host role: isolated experimental candidate
- Model route: `local` through the existing OpenAI-compatible gateway
- Case: `w10-01-unsupported-1`
- Policy reached: `gated`
- Result: read timeout at the frozen 90-second case limit
- Record timestamp: `2026-09-11T17:53:21Z`
- Record SHA-256: `e13a755266cdc017b04fc91f067624f7412374241bc74371f95a742fd0140e96`

Candidate API logs showed that search and page acquisition completed. The
single assessment request then exhausted the remaining case budget because it
contained as many as 24 acquired excerpts. The protocol now uses a declared
eight-page acquisition allocation while retaining metadata and exclusion
reasons for every returned result.

## Bounded-packet smoke

Revision `e84a23e` ran the same case after the acquisition correction:

| Policy | Result | Elapsed | Record SHA-256 | Manifest SHA-256 |
|---|---:|---:|---|---|
| fixed | completed | 41.792 s | `b1bdca6060912b4059ff866025f38126bea2f2954da3ebf7ed6c0d5eb836f502` | `30f3319669af97ac659f10072ed9d303d23e2e2734f5a7127fbcb4e6ac9eb34d` |
| full | completed | 78.897 s | `3a90ad5fb96cb01b7a14d9b6630cf6ce17ccb22b348048383aae61f3a32f96c5` | `a07f5af905974c4289dffda36e27a6d3008ad56e929d9f201edb2038d6110215` |

The full policy exposed a second pre-execution defect: the prompt did not define
`marginal_value`, and publisher identity was model-generated. The grader marked
useful claim evidence as non-marginal, causing the deterministic admission layer
to reject every source. Before comparative execution, publisher identity was
moved to URL-derived code and the grader schema was expanded to distinguish
claim value, authority gain, currency gain, and contradiction resolution. These
smoke outputs are excluded from outcome calculations; their complete records and
private acquisitions remain on the experiment host under `/tmp/w10-smoke-*`.

## Final runner acceptance

Revision `df86fd4` completed all five policies for the smoke case with no failed
trial. Its manifest digest is
`acff700c55fba239b27f936e30684d83bcba5e53718b4d9f50b13a587f96604c`.
That run exposed and verified the correction for LangGraph's default recursion
limit, but preceded deterministic derivative rejection.

Revision `cc0fd92` then completed the full policy in 37.121 seconds with all
bounds enforced. It admitted six sources, rejected two as derivative reporting,
retained thirteen unselected search results, and conservatively left all three
claims open. The record digest is
`3119aa6c146b0da938b044396e72b00a89b5d0dd93f05fd20bfbf70ce322ecce`;
the manifest digest is
`a97b758360ad79b9a5b9064d893aa3ec81a36b29131f6ad1c64345a0a3a6c286`.
This is the accepted pre-execution runner behavior.

## Excluded work-order pilot

The first attempted challenge execution completed ten technically valid trials
before a data-science design audit found that the global shuffle did not satisfy
the protocol's per-case counterbalancing requirement. Its policy counts were
uneven (`fixed=4`, `gated=3`, `gap=2`, `unconstrained=1`, `full=0`). The process
was stopped, and none of those observations are used in the W10 comparison.

The ten public record digests, sorted by path and hashed together, produce
`e22b423580e8ed5dc794f5542eb5c9f14928d2433578a0ebb802a08a11ce2799`.
The complete public and private packet is retained on the experiment host as
`challenge-excluded-global-shuffle`. The accepted runner writes a hashed order
manifest and rotates every policy through distinct within-case positions across
the three repetitions.

## Excluded incomplete-failure-evidence run

The first counterbalanced challenge execution was stopped and excluded after 21
outcomes (18 completed and three grader-schema failures). Although the runner
correctly refused incomplete candidate grades, failed trials did not retain
their query and acquisition ledger for manual adjudication. That violates the
frozen evidence-completion gate.

The excluded tree remains on the experiment host as
`challenge-excluded-incomplete-failure-evidence`, with composite SHA-256
`01809ef31116e0391d5edd4403e6a29a71fbd1e93adac339887bd4b6eaefc4fb`.
The correction adds public/private pre-grade checkpoints and archives them on
failure. It does not change cases, policy behavior, budgets, scoring, work order,
or decision gates. The formal comparison restarts from an empty output directory.

An initial corrected-run launch used loopback port 4000 instead of the configured
home-lab gateway route. It was stopped after 25 immediate connection failures
and contains no completed outcomes. The checkpoint correction preserved the
partial ledgers as designed. This launch is excluded at
`challenge-excluded-wrong-llm-route`, with composite SHA-256
`c4fbe49b90991a566ec722dbd9fb007f58359303c97a18c885bd62630b16c098`.
