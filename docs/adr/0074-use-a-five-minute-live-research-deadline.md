# Give new live research five minutes to finish

- Status: accepted
- Date: 2026-10-17
- Scope: newly admitted live durable research
- Extends: [ADR-0070](0070-execute-live-durable-research.md) and [ADR-0073](0073-publish-source-bounded-research.md)

## Context

The three-minute limit came from the bounded research experiment. Live checks
have since measured a model draft taking 153 seconds and model review taking
35 seconds on a separate run. Combining those observed stages with planning
and page capture would exceed three minutes. Shorter nested timers also stopped
waiting for drafts that the model gateway later completed successfully.

This is a time allowance for completing bounded work, not a performance target.
A longer wait does not justify more searches, larger model outputs, or retries.

## Decision

New live admissions pin `durable-live/5` and have one absolute 300-second deadline
measured from admission. Planning, search, capture, drafting, review and report
preparation share that allowance. Model transports and model-stage wrappers may
wait within it; they cannot each consume a separate five minutes.

The worker selects the allowance from the stored policy version, including on
recovery. Earlier admissions retain their 180-second overall limit and original
policy-specific behavior, including their original model-stage limits. Future
policy versions must preserve these mappings.

Search, source, byte, model-call and token limits do not increase. External call
intents, exact receipts, cancellation fences, ownership checks and atomic report
publication are unchanged. An unfinished external dispatch still stops as
`outcome_unknown`; it is never silently repeated. Model or source content cannot
select a longer deadline. The capabilities endpoint advertises the new limit.

## Consequences

A normal draft and review have more room to complete without a new external
call. Slow or unavailable providers can still exhaust the finite allowance.
Five minutes is a cap, not a promise that research will be complete or useful.
Measured latency and report quality remain separate observations.

There is no storage migration, new feature flag, provider change or CLI change.
Older workers do not understand version 5: rollback must wait until new-policy
admissions are terminal and active work is zero. Retain all receipts and data;
do not start an old worker to reinterpret unfinished version-5 operations.

## Verification

Test the measured stage combination against both limits: it fits within 300
seconds and exceeds 180. A run exceeding 300 must still stop. Exercise real
cancellation and refusal to replay during planning, drafting and review. Verify
old policy deadlines and exact request reuse, and check the admitted policy and
advertised allowance through the public HTTP route with isolated stores.
Production verification must retrieve real reports on each deployment.
