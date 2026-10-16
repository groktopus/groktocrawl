# Retain Model-Reviewed Consolidated Publications

- Status: accepted for bounded experimental use
- Deciders: Magnus Hedemark
- Accepted: 2026-09-30; direct owner approval of the bounded W9 recommendations
- Date: 2026-09-07
- Scope: bounded experimental PostgreSQL retention in `magnus919/groktocrawl-x` only
- Plan: issue [#103](https://github.com/magnus919/groktocrawl-x/issues/103), W3
- Supersedes: none; extends ADR-0075 within the experimental storage namespace

## Context and Problem Statement

The consolidated PostgreSQL adapter currently stores only fixture publications.
The real-query runner now constructs and reviews model-originated candidates, but
those candidates are ephemeral and cannot be reopened or re-rendered. Treating
fixture provenance as a database invariant prevents the storage experiment from
testing the lifecycle that the replacement architecture requires.

Retention must preserve the distinction between fixture, model and human review.
Removing a fixture-only check must not make a model response trusted, human-approved,
current, or eligible for publication. Existing execution receipts, source closure,
canonical digests, report audits, scope fencing, quotas, deletion and expiry remain
the authority for a retained candidate.

## Decision Drivers

- Reopen exact model-reviewed bytes after the short-lived execution session expires.
- Preserve an explicit provenance marker without manufacturing human approval.
- Keep old schema-10 readers and fixture rows valid during the migration window.
- Make the change reversible through a forward migration and isolated namespace.
- Avoid changing the incumbent stack, public API, vector store, or runtime owner.

## Considered Options

| Option | Benefit | Cost or risk |
|---|---|---|
| Keep fixture-only storage | Smallest surface and strongest current restriction | Cannot test W3 retention for the real research path |
| Add a separate model-publication table | Strong physical separation | Duplicates lifecycle, retention, export and deletion logic |
| Relax the consolidated provenance check to a boolean marker | Reuses tested transaction and read paths; preserves explicit origin | Requires strict eligibility and reviewer provenance checks at every write/read |

## Decision Outcome

Use the third option in schema 11. `consolidated_publications.fixture_only` remains
required and is retained as a boolean provenance field. Schema 10 remains readable;
schema 11 removes only the tautological fixture-only check and keeps the column,
canonical document checks, source ledger, root fencing, quotas, expiry and deletion
behavior unchanged. A model-reviewed row is retained only when the caller has
executed the complete structural, conflict/coverage, support, freshness, assessment
and render-audit checks through the registered model reviewer, and
`prepare_publication()` accepts the exact candidate. Human review is never inferred.

The migration is opt-in and isolated. It does not select PostgreSQL for production,
remove Qdrant, expose a public route, or authorize provider spending. A model row
with failed, indeterminate or unknown-freshness checks remains uncommittable. The
existing `ConsolidatedFixtureJourney` and schema-10 fixture workflows remain valid.

## Inherited Decision Impact

| Record | Relationship | Scope |
|---|---|---|
| ADR-0075 | Extend | Consolidated storage now records either fixture or model provenance while retaining the accepted 1 MiB and lifecycle bounds |
| ADR-0071 | Extend | PostgreSQL remains bounded experimental exploration; vector consolidation and production adoption remain open |
| ADR-0069 | Retain | Claim, evidence, verification and human/model provenance distinctions remain required |
| ADR-0072 | Retain | No public protocol or provisional-text behavior changes |
| ADR-0047 | Retain | Storage retention does not provide restart-safe execution or recovery ownership |

## Consequences

The experimental store can exercise the same retention and reopening path for a
future model candidate. The provenance bit is auditable, but it is not a quality
verdict or an authorization. A migration is required before model rows can be
committed, and old workers must not assume every row is fixture-only. Model-reviewed
publication remains blocked by semantic and freshness failures; the current local
gateway reliability limitation therefore remains visible.

## Confirmation

Add migration and database tests for schema-10 compatibility, schema-11 model-marker
round trips, exact source/output reopening, bounded consolidated export and offline
revalidation, quota/expiry/deletion behavior and rejection of ineligible candidates.
Run the existing hosted PostgreSQL probes and full CI. Report any live model failure
separately from storage success. Revisit this proposal before cross-scope import,
format freeze, API exposure, recovery adoption or production traffic.

## W9 pre-approval disposition — 2026-09-30

The [final W9 recommendation](../../../experiments/w9-replacement-readiness-decision.md)
supports this record at its bounded experimental scope after the completed
three-checkpoint pilot. At that pre-approval checkpoint, status remained proposed
pending Magnus Hedemark’s ratification; successful implementation evidence does not supply owner approval.

## Owner ratification — 2026-09-30

Magnus Hedemark approved the [bounded W9 recommendations](../../../experiments/w9-replacement-readiness-decision.md)
in this project conversation: “I approve the bounded ADR recommendations. Please proceed.”
This accepts the documented experimental scope only; the earlier pending note
is retained as decision history. It does not authorize production migration,
upstream replacement, or expansion beyond this record’s confirmation gates.
