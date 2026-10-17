# Publish research about captured sources with an explicit scope

- Status: accepted
- Date: 2026-10-16
- Scope: newly admitted live durable research
- Extends: [ADR-0070](0070-execute-live-durable-research.md)

## Context

A useful documentation page often has no publication date. Research can correctly
explain what that page says without proving that every statement is true today.
The live constructor previously allowed claims about current facts, while its
capture adapter supplied only capture dates. Such claims could not pass the
existing freshness rules. The result was a failed research run, even when the
captured text supported a useful, carefully qualified answer.

## Decision

New admissions use `durable-live/4`. Their construction request explicitly asks
for statements about the captured sources, with historical scope. The response
schema requires that scope, and application validation rejects a model response
that instead asserts current scope. Before independent review, the application
prefixes each claim with "The captured sources report:". Every report explains
that it describes captured content and does not independently confirm present-day
validity. Sources retain their genuine capture dates and original bytes.

This is a narrower answer contract, not a new way to certify current facts.
Disagreements and missing evidence must remain visible. A question requiring
present-day confirmation beyond the captures must be marked unresolved. The
model still judges support and freshness, and the existing deterministic checks
and final publication gate remain authoritative. A missing capture date cannot
be invented. A capture made after an explicit `as_of` target cannot prove what
was available at that earlier time. Existing age limits still apply.

Old admissions retain their original request bytes and semantics. Version 4 does
not reinterpret their claims, rewrite their terminal results, or repeat their
paid operations. The constructor defaults to the previous behavior for existing
callers; only version 4 opts into the new mode. Model request receipts, fencing,
publication transactions, and callback delivery retain ADR-0070's contracts.

Version 4 drafting and model review use the admission's existing shared
180-second deadline. Shorter model-stage timers must not abandon a call while
that shared deadline still permits it. Earlier policies keep their original
stage limits; no retry is introduced.

The new construction prompt digest includes its actual scope and excerpt
instructions. The report renderer and audit use version 3 for this mode, while
previous research retains renderer version 2.

## Alternatives

- Passing every freshness check would hide missing evidence. Rejected.
- Changing existing model claims to historical scope after review would
  misrepresent the review. Rejected.
- Requiring publication dates for every report would prevent useful answers
  about what sources actually say. Rejected for the new source-bound contract.
- Making a second model attempt after failure would repeat spending without
  correcting the evidence contract. Rejected.

## Consequences

The platform can provide useful, checked descriptions of captured documentation
without pretending that a capture date proves current truth. Reports must keep
their source scope visible. Users who need independent present-day confirmation
still need stronger evidence; this change does not provide that guarantee.
Versioned admissions preserve old runs at the cost of maintaining both policies.

## Verification and rollback

Require source-bound construction and report tests, rejection of current-scope
model responses, missing capture dates, unsupported claims, and captures after a
requested past date. Compare old request digests byte for byte. Verify version 4
and old-policy recovery with isolated stores, and exercise admission, retained
reports, and status recovery through HTTP. Before delivery, verify the real
configured model on saved public sources without repeating searches. Production
rollout must verify an objective-dependent report and real source citations.

Rollback restores the prior agent image. Retain storage, old operation receipts,
and additive policy identifiers. Never replay ambiguous dispatches during rollback.
