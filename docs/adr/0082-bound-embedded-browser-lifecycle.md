# Bound Embedded Browser Lifecycles and Quarantine Uncertain Cleanup

- Status: accepted
- Date: 2026-11-15

## Context

The scraper's embedded Playwright tier already limited concurrent work and set
timeouts on selected browser operations. Setup, extraction, and teardown could
still exceed any whole-request deadline. On cancellation, a stuck browser close
could hold request tasks indefinitely; a failed close could be treated as
finished and return the browser permit even when the native process might still
exist. Startup-only browser probing also did not represent later cgroup PID
pressure or cleanup quarantine.

The lightweight HTTP tiers do not require Playwright and must remain available
when browser capacity is degraded.

## Decision drivers

- Bound setup, navigation, extraction, cancellation, and cleanup time.
- Keep browser permits reserved until process cleanup is confirmed.
- Reject new launches before cgroup PID pressure prevents cleanup itself.
- Preserve the non-browser scrape path and avoid logging source details.

## Considered options

1. Raise the container PID limit: rejected; this does not prevent stuck browser
   trees or provide a recovery signal.
2. Apply `asyncio.wait_for` around the lifecycle: rejected; it can wait for
   cancellation handlers and cleanup to finish, so the caller's deadline is not
   necessarily a wall-clock bound.
3. Release admission after a cleanup timeout: rejected; the browser process may
   still exist and consume the resources represented by that permit.
4. Observe cancellation with a separate bounded wait, retain uncertain
   ownership, and recover at the dedicated container boundary: selected.

## Decision

Run each embedded Playwright lifecycle under a 120-second observed deadline.
On deadline or request cancellation, cancel the lifecycle and observe its
unwinding for at most five seconds. `asyncio.wait` observes task completion
without waiting indefinitely for cancellation handlers. If cleanup completes,
the permit may be released; if it remains pending, quarantine browser-tier
admission and retain the semaphore and weighted permit. Do not admit another
browser operation based on elapsed time or process-count heuristics.

Legacy close errors and browser-pool close failures have the same quarantine
effect. The optional pool keeps its closing-process reservation on uncertain
cleanup and rejects further acquisition. Browser readiness reports cgroup PID
headroom, active and queued browser work, pool availability, and quarantine
reason. Unknown cgroup capacity is reported as unknown rather than healthy;
`GET /ready/browser` returns 503 unless both the browser probe and current
admission state are ready. `/health` remains a liveness response and reports
degraded browser readiness in its JSON body.
Low capacity rejects new embedded browser work before launching Chromium;
lightweight scraper routes continue serving.

The minimum 100-task reserve aligns with the existing browser-service launch
reserve. It is a conservative admission threshold, not a measurement of every
embedded-browser workload; cgroup headroom cannot guarantee that an individual
launch will succeed.

The standard Compose scraper container opts into one fixed 30-second delayed
exit after uncertain cleanup. This is supported only for the dedicated
single-process container with Docker init and a restart policy. Host execution
rejects the configured recovery guard. With recovery disabled, browser
admission stays quarantined until operator restart. The container boundary is
the only recovery that can prove descendant processes have been destroyed.

## Consequences

Individual browser calls keep their existing timeouts and extraction behavior.
The 120-second deadline is a ceiling for the complete embedded browser tier;
timeouts can fall through to existing scraper recovery tiers. A cleanup timeout
causes temporary browser-tier unavailability and, in the standard container,
a delayed restart that may interrupt in-flight requests. Lightweight requests
are not gated on browser readiness. Cgroup capacity is an admission signal, not
a guarantee that Chromium can launch.

## Validation

Synthetic tests cover deadline completion, cleanup that resists cancellation,
request cancellation, permit retention, low task headroom, and degraded health
reporting. No production fault is induced for verification.

## Links

- [ADR-0077 resource limits](0077-capacity-evidenced-resource-limits.md)
- [ADR-0080 quarantined browser container recovery](0080-recycle-quarantined-browser-containers.md)
