# Expose a Thin Retained Research Workspace

- Status: proposed; implementation is bounded experimental work authorized in issue #422
- Date: 2026-10-07
- Scope: experimental fork only
- Extends: ADR-0078 durable artifact authority and ADR-0084 independent roots

## Context and Problem Statement

Returning clients need to discover and inspect permitted retained roots, resolve
citations after process loss, and invoke named operations. Existing reports were
retained, but exact cited source bytes were process-local. A browser history is not
an artifact authority.

## Decision

Expose scoped list/select and revision-guarded named operations over the existing
experimental run ledger and artifact authority. Retain audited knowledge and exact
source bytes atomically with reports in schema 16, under the same scope, retention
fence and deletion lifecycle. The Valkey-only fixture adapter retains bounded
knowledge/source material in its existing terminal projection. No separate
workspace database, implicit thread or provider execution is introduced.

The human portal delegates to the same APIs. Private bodies are never placed in
localStorage; its fragment contains only an opaque selected run identifier. Output
uses text nodes, not executable Markdown. Mutation requests permit a fixed route
and method set and reject foreign origins. Named actions require the observed
immutable artifact revision; session attachment also requires its own revision.

## Consequences and Limits

Capabilities remain opt-in and fixture-backed. Durable execution does not imply
live research quality. PostgreSQL-backed admissions fail before work unless schema
16 is present; old report-only records remain readable and explicitly lack recovered
citation material. No automatic migration or production deployment is authorized.

The 1 MiB terminal bound and 32 MiB aggregate authority bound remain explicit:
oversized material fails admission/commit, never truncates. Source metadata cannot
trigger acquisition. Existing audited report rendering remains authoritative;
workspace export copies exact bytes. Roots retain their identities and explicit
comparisons; default longitudinal threads remain rejected under ADR-0084.

## Validation

Deterministic HTTP lifecycle fixtures, Valkey restart recovery, actual Chromium
selection/reload/evidence/deletion/foreign-scope QA, and PostgreSQL schema/replay/
expiry/delete probes are required. PostgreSQL probes run in the isolated Runtime
CI storage lane; local macOS checks do not substitute for them.
