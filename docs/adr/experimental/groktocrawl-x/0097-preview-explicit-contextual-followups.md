# Preview explicit contextual follow-ups

- Status: proposed
- Date: 2026-10-07
- Scope: issue #420 in the experimental fork
- Extends: ADR-0084; retains independent roots and rejection of default thread injection

## Context and problem

An agent needs to inspect the meaning of a follow-up before invoking retrieval.
Existing explicit-ref deepen already supplies identity; history-wide entity
resolution would reintroduce contamination and stale-current leakage.

## Decision drivers

Preserve explicit intent, stable evidence identity, bounded work, inspectable
ambiguity, deletion/privacy rules and parity across API, CLI and MCP.

## Considered options

- Inject accumulated thread prose: rejected by ADR-0084 and unnecessary here.
- Model-based rewriting: provider cost and probabilistic incorrect identity.
- Deterministic, user-selected preview: limited linguistic coverage, auditable abstention.

## Proposed decision

Use a dedicated read-only synchronous preview over at most eight explicit
session/root evidence selections. Validate identities through existing authority
checks, never use evidence bodies as instructions, preserve original wording and
return a standalone query only when referents are explicit. Abstain on unresolved
pronouns and unapplied corrections. User override is inspectable and authoritative.
Return typed suggestions requiring confirmation; never execute them.

## Consequences

No provider calls, GPU work, stored thread, new artifact lifetime or implicit
follow-up policy. Legacy sessions retain bearer capability semantics; newly
owned sessions reuse credential-derived scope. Temporal status reports snapshot
age, not factual freshness. Snapshot APIs lacking timestamps are explicitly
unknown. Durable evidence not exposed by existing resolver is unavailable,
not silently imported into context. Normal API execution revalidates refs after
preview. More sophisticated resolution requires separate measured evidence.

## Links

- [ADR-0084](0084-retain-independent-research-roots-over-default-threads.md)
- [Guide](../../../guides/contextual-followups.md)
- [Issue #420](https://github.com/magnus919/groktocrawl-x/issues/420)
