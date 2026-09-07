# Scraper Scale-Out with Bounded Capacity and Atomic Origin Pacing

* Status: accepted
* Deciders: GroktoCrawl maintainers
* Date: 2026-09-04

## Context

Issue #629 requests a supported scale-out path for browser-heavy acquisition.
The default Compose publishes a fixed scraper port. Browser admission and API
admission are process-local (ADR-0051), so arbitrary API replication would
multiply budgets. Per-origin future-slot reservations were also local despite
sharing historical timestamps through Valkey.

## Decision

Provide an opt-in `docker-compose.scaleout.yml`: one API process, a stable
HAProxy gateway, and one to four scraper replicas. The gateway dynamically
resolves Docker service records, uses least-connections routing and active
readiness checks, and does not replay failed POST requests. Remove replica
host ports; publish only the gateway. Existing replica CPU/memory/PID limits
remain, and each replica permits four browser lifecycles. Physical browser
capacity is `4 * replicas`, at most 16. The sole API process has browser budget
128 weighted units (weight 8), light-fetch budget 64, and LLM budget 32
(weight 4). This is a deployment contract, not distributed API admission.

Enable distributed politeness in this topology. An atomic Valkey Lua script
uses server time to reserve per-origin slots. Distinct reservation keys cannot
be overwritten by legacy request timestamps. Queues beyond 30 seconds are
rejected; coordination failure fails closed only in this opt-in mode. A
cancelled reserved slot is not reclaimed because subsequent callers may
already own later slots. TTL bounds the unused reservation. Per-tier checks
remain read-only. The ordinary single-node policy is unchanged.

Scaling browser-svc sessions or API processes and adding restart-safe execution
are outside this topology. ADR-0047 remains in force. Rolling replacement must
drain gateway backends before stopping them; generic Compose scale-down cannot
promise completion of active requests.

## Consequences

Independent research sessions can use additional browser capacity without
removing resource limits. Single-session speedup remains bounded by its own
fetch concurrency and provider latency. Operators must provision CPU/memory
for each replica and validate their workload instead of assuming linear gains.
The opt-in topology adds a gateway and requires Compose 2.24.4+ for port reset.
Shared origin pacing trades availability for preserving configured politeness
when Valkey fails. Model/LLM capacity remains a separate bottleneck.

## References

- [HAProxy DNS service discovery](https://www.haproxy.com/documentation/haproxy-configuration-tutorials/proxying-essentials/dns-resolution/)
- [Compose override/reset semantics](https://docs.docker.com/reference/compose-file/merge/)
- [ADR-0051](0051-global-admission-control-and-cancellation.md)
