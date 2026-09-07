# ADR-0065: Stream Discovery Acquisitions as Queries Complete

- **Status:** accepted
- **Date:** 2026-09-04

## Context

Deep research runs its subqueries concurrently, but the previous discovery
implementation waited for every query before starting any page acquisition.
That barrier delayed the first useful source when one query was slow.

## Decision

Run a bounded producer/consumer acquisition loop. Searches run concurrently;
acquisition admits a contiguous resolved prefix of the planned query order.
Each query's candidates are filtered and ranked before entering the queue, and
that batch order is frozen. A fast later query cannot consume credits before
an earlier query resolves. The first resolved query can begin fetching while
later queries are pending. A slow first query remains a deliberate admission
barrier for deterministic budget allocation.

At most five scrapes run at once, with at most twenty novel attempts or the
remaining credit budget, whichever is smaller. Stop speculative acquisition
when three novel sources succeed; reusable artifacts do not crowd out new gap
evidence. Keep successful admitted artifacts in final ranked source order.
This prioritizes planned query batches for admission over a global ranking
that would require waiting for all searches. Final ordering remains ranked;
coverage and domain mix require evaluation when changing query plans.

Callbacks publish pending search results before corresponding scrape events.
All public generator adapters close nested discovery iterators explicitly;
cancellation drains both searches, scrapes, and queue waiters. Partial search
failure degrades to healthy results; retryable capacity errors propagate.

## Consequences

The first source can arrive before the full search fan-out finishes, without
an unbounded prefetch budget or completion-order credit allocation. Final
synthesis sees stable assembled context; progress reflects actual fetch
completion. The existing registry preserves artifact compatibility and reuse.

## Alternatives considered

- Wait for all searches before scraping: preserves the barrier and delays the
  first source.
- Start an unbounded scrape task for every result: reduces latency at the cost
  of uncontrolled outbound work and credit violations.
- Emit source events after discovery: preserves event ordering but hides the
  actual acquisition progress from streaming clients.
