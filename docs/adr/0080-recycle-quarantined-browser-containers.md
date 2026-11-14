# Recycle Quarantined Browser Containers Without Releasing Uncertain Ownership

- Status: proposed
- Date: 2026-11-14
- Decision authority: Magnus Hedemark authorized issue, ADR, reviewed implementation, merge and production deployment for issue #722. Acceptance follows PR review.

## Context

On November 12, browser-svc entered permanent `cleanup_incomplete` admission
failure after one uncertain teardown. Docker's `unless-stopped` policy cannot
recover a still-running unhealthy process. The scraper used a 30-second response
budget while requesting 45-second navigation, then deleted the session while
navigation was active. The exact failed cleanup resource is unknown because
cleanup reduced timeout, cancellation and exceptions to a boolean. Historical
resource-limit counters cannot establish the incident's cause.

Existing fail-closed ownership is necessary: a late operation may still own
processes. Healthy current process counts do not prove teardown safety.

## Decision drivers

- Restore availability without admitting work beside uncertain resources.
- Preserve container memory/PID bounds and source-network isolation.
- Avoid arbitrary retries of uncertain browser actions.
- Make future teardown failures diagnosable without logging source secrets.

## Considered options

1. Clear quarantine after a timer or low process count: rejected; neither proves
   resource ownership ended.
2. Keep manual restart forever: safe but turns one teardown failure into an
   unbounded outage.
3. Retry Playwright cleanup indefinitely: rejected; cancellation-resistant
   operations can keep resources alive and still provide no proof.
4. Terminate the dedicated container's application process after bounded grace:
   selected, explicitly enabled only in the supported container topology.

## Decision

Keep quarantine and admission rejection intact. Standard Compose opts into
`BROWSER_CONTAINER_RECYCLE_ON_QUARANTINE=1`. After the first quarantine, schedule
one fixed 30-second monotonic grace period, then exit the single application
process with code 75. Docker init exits with it; container teardown terminates
remaining descendants before the restart policy starts a replacement. No
in-memory ownership record is cleared and no operation is replayed.

The flag is an operator assertion that this is a dedicated, single-worker
container with `init: true` and a restart policy. A Docker marker and parent PID 1
check prevent accidental host or child-worker recycling. Do not enable under a
reloader, multi-worker supervisor, shared PID namespace, or supervisor that
keeps the container alive after the application exits. Disabled deployments
retain manual recovery. A normal shutdown cancels recycling and prevents any
new recycle task from being scheduled.

Thirty seconds is a bounded opportunity for other sessions to finish, not a
promise of graceful draining. Admission remains 503 throughout quarantine.
Existing sessions can be interrupted at exit, clients can receive connection
errors, and session IDs are lost. Durable research/job storage is not modified;
its existing uncertain-operation recovery rules still apply (ADR-0072).

Align scraper response timeout to the 45-second navigation budget plus 10 seconds
transport margin. Allow 20 seconds for DELETE, covering the four 2-second close
stages and scheduling margin. These are deadline ordering corrections, not
claims about site speed or a cure for every browser hang.

Log failed cleanup stage, outcome, exception class and elapsed time; do not log
exception messages/tracebacks, URLs, cookies or page content. Keep the existing
quarantine and admission counters. Startup/creation cleanup waits log their
stage when incomplete.

## Consequences

A quarantined container recovers at a resource-destruction boundary instead of
silently freeing capacity. Persistent failure can still cause periodic
restarts; grace and Docker backoff prevent a tight loop, not an infinite one.
Operators must alert on recurring quarantines/restarts, disable the opt-in and
investigate rather than raising process limits blindly. Hard exit intentionally
bypasses potentially unbounded teardown. Some buffered final logs may be lost.

## Confirmation and rollback

Tests cover deadline ordering, redacted cleanup failures, single-flight grace,
shutdown fencing, disabled/host guard and actual subprocess exit 75. Container
acceptance must verify Docker restart and absence of old descendants; production
verification checks health, create/navigate/content/delete and cleared ownership.
Do not induce a cleanup fault in production to test restart.

Rollback: set flag 0 to retain fail-closed/manual restart, or restore previous
browser/scraper image digests and recreate only those services. No data migration.
The [runbook](../runbooks/browser-quarantine.md) owns operational verification.

## Links

- [Issue 722](https://github.com/groktopus/groktocrawl/issues/722)
- [ADR-0077 resource limits](0077-capacity-evidenced-resource-limits.md)
- [ADR-0072 uncertain-operation recovery](0072-valkey-leased-legacy-job-recovery.md)
