# Inherited GroktoCrawl Roadmap

> This file preserves upstream roadmap context at the fork point. For
> `magnus919/groktocrawl-x`, follow the [experimental research architecture plan](experiments/research-architecture.md).
> This experiment does not replace or redirect mainline GroktoCrawl.

A lightweight, best-effort view of where GroktoCrawl is heading. It is **not a
commitment** to ship any specific item by a date. Priorities shift as
contributors engage, bugs surface, and the project's direction evolves. See
[Contribution intake](../CONTRIBUTING.md#contribution-intake-and-triage) if
you want to influence what appears here.

Buckets:

- **Now** — actively being worked or the immediate next focus.
- **Next** — high-signal, plausible near-term work that needs design or a
  champion to move into *Now*.
- **Later** — exploratory or dependent on earlier work; nothing here is planned.

## Now

- **Contributor skills directory.** Add `.agent/skills/` with reusable,
  documented skills so contributors can pick up common tasks quickly
  ([#394](https://github.com/groktopus/groktocrawl/issues/394)).
- **Community process hygiene.** Keep the contribution intake, release/dependency
  triage routine, and this roadmap accurate as the backlog changes.

## Next

- **Restart-safe async job execution.** Jobs currently run in-process and are
  not resume-safe after a restart (see [ADR-0047](adr/0047-defer-restart-safe-execution.md)).
  Making execution durable needs an explicit design (ownership, leases,
  retries, cancellation, artifact consistency, webhook idempotency) before any
  queue technology is chosen.
- **Semantic index operations.** Smarter retention and a migration path for
  embedding models are on the table as design work
  ([ADR-0027](adr/0027-smarter-index-retention.md),
  [ADR-0028](adr/0028-embedding-model-migration-path.md)).
- **Adopter-driven hardening.** Surface reliability gaps reported through the
  intake route that are scoped and well-evidenced.

## Later

- **Multi-user / multi-tenant authentication.** Explicitly out of the MVP; may
  become relevant if adoption grows beyond self-host single-operator use.
- **Managed / hosted offering.** Deliberately out of scope today — GroktoCrawl
  is self-hosted and MIT-licensed. If this ever changes, it would be separate
  from this repository.
- **Additional site adapters.** Grows organically with community contributions
  through the intake route.

## How items move between buckets

The maintainers review the open backlog and this roadmap periodically and
re-balance the buckets as contributors engage and new evidence surfaces. A
well-scoped issue with a reproducible bug report or a concrete feature proposal
— raised through the [intake route](../CONTRIBUTING.md#contribution-intake-and-triage) —
is the most reliable way to get a candidate into **Now** or **Next**. Triage is
best-effort; there is **no guaranteed response or resolution SLA**.
