# Retain known model output-limit failures

- Status: accepted
- Date: 2026-10-17
- Scope: live durable research model operations
- Extends: [ADR-0070](0070-execute-live-durable-research.md)

## Context

The model transport rejects a response whose finish reason is `length`, because
an incomplete draft or judgment cannot be published. Previously, that rejection
left only a dispatch intent. Status therefore said `outcome_unknown` even though
the service had received a definite output-limit response.

## Decision

Retain an additive failure receipt for a received JSON envelope whose selected
choice has finish reason `length`. Its stable code is `model_output_limit`.
Retain only HTTP status, that fixed finish reason, and valid bounded integer
input/output token counts. Do not retain response text, provider identity,
endpoints, credentials, headers, or raw exceptions in this receipt.

Persist the receipt under the existing operation identity, input digest and
owner fence before reporting the failure. Recovery raises the same failure
from the retained receipt without another model call. Status and events report
a nonretryable error explaining that no report was published. Terminal callback
projections reuse the same retained terminal error.

A timeout, disconnect, malformed envelope, or other unrecognized rejection
keeps the existing unknown-outcome behavior. If saving the failure receipt is
interrupted, its dispatch remains unknown. Token counts alone do not establish
output exhaustion; historical failed calls are not reclassified.

No request, model setting, prompt, count, token budget, deadline, or retry policy
changes. No store migration is required. Before rolling back to older workers,
drain active research. An older worker cannot interpret the new failure receipt
as a successful response and must not redispatch it.

## Consequences

Users can distinguish a known output-limit response from an uncertain network
outcome. This improves diagnosis, not model completion reliability. A caller
can choose a separate, narrower request; the service does not create it.

## Verification

Exercise a controlled length response during planning, drafting and review
through public HTTP with isolated stores. Require a failed status, a durable
sanitized receipt, no report publication, and unchanged call counts after a
fresh application and idempotent admission. Separately test receipt replay
before terminal projection, malformed diagnostic values, network ambiguity,
unchanged valid responses, ownership fencing and lost-receipt behavior.
