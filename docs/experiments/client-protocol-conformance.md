# Client protocol conformance status

The bounded contract fixture in `agent.experimental.client_protocol` defines one
application-owned `research/1` event model for status, SSE replay, CLI and MCP
projections. It rejects terminal payloads on progress events, requires an
accepted-first contiguous trace with exactly one terminal event, rejects foreign
or out-of-range cursors, and produces identical terminal projections for all three
client surfaces. The machine-readable traces under
`docs/experiments/client-protocol/golden/` freeze completed-conflict, failed-audit
and cancelled outcomes for cross-client parity checks.

The opt-in `GET /experimental/research/v1/capabilities` adapter now exposes this
boundary when `FEATURE_EXPERIMENTAL_RESEARCH=true`. A second opt-in gate,
`FEATURE_EXPERIMENTAL_RESEARCH_RUNS=true`, enables retained run/status/cancel/event
and artifact/evidence reads. New fixture admission additionally requires the
development-only `FEATURE_EXPERIMENTAL_RESEARCH_FIXTURE_RUNS=true` opt-in;
leaving it false preserves those reads and recovery without creating new runs.
For [isolated demo verification and checkpoints](../runbooks/experimental-replacement-candidate.md#verify-and-freeze-the-receipt),
persist the true setting in the dedicated private candidate env file used by
all Compose recreations, not just in a one-command shell prefix. After the full
sequence, reset that file to false and recreate the demo agent. Production
configuration keeps fixture admission false throughout. A third opt-in gate,
`FEATURE_EXPERIMENTAL_RESEARCH_DURABLE=true`, wires admission, lease fencing,
checkpoint identity, terminal status projection and cancellation authority to Valkey.
The additional `FEATURE_EXPERIMENTAL_RESEARCH_POSTGRES_ARTIFACTS=true` gate requires
`DURABLE_RESEARCH_POSTGRES_DSN` and schema 14. Under that gate, PostgreSQL atomically
owns the manifest and complete summary, analysis and dossier bytes. Valkey retains
only identities, digests, public URLs, lifecycle state and event projections. A
reclaimed run uses a deterministic artifact-set identity, so a PostgreSQL commit
followed by process loss can be replayed without creating a second artifact set;
deletion reaches PostgreSQL before the Valkey tombstone. Capabilities report
`fixture_run_adapter`/`process_local`, `durable_fixture_run_adapter`/`valkey_fenced`,
or `postgres_artifact_authority_fixture_adapter`/`postgres_artifacts_valkey_fenced` accordingly. Session attachment remains
`attachment_only`; all flags are off by default, and inherited `/v2` routes are unchanged.

The tests cover completed, failed and cancelled terminal outcomes, duplicate replay
deduplication, sequence gaps, foreign cursors and no-terminal/terminal-order
violations. The run adapter tests also cover foreign-scope reads and mutations plus
deleting a root while execution is still running; a late completion remains tombstoned
from status, events, artifacts and session attachment. The run adapter is still a
bounded fixture implementation, not production research. CLI/MCP journey parity is
implemented in [PR #130](https://github.com/magnus919/groktocrawl-x/pull/130).
Bounded scope-isolation and deletion-tombstone
evidence merged in [PR #133](https://github.com/magnus919/groktocrawl-x/pull/133). The
durable status-recovery and cancellation route slice is merged in [PR #139](https://github.com/magnus919/groktocrawl-x/pull/139); bounded artifact-byte and terminal event-history recovery is merged in [PR #142](https://github.com/magnus919/groktocrawl-x/pull/142). PostgreSQL artifact authority and combined PostgreSQL/Valkey backup evidence passed in W5; this adapter does not claim real-provider effect recovery or production readiness.

[PR #251](https://github.com/magnus919/groktocrawl-x/pull/251) completes the W6
recovered-client gate. After clearing all process-local route state, one completed
run was recovered through Valkey and PostgreSQL and produced matching HTTP status,
manifest identity, exact artifact bytes, terminal SSE replay, CLI download bytes,
and decoded MCP bytes. The same journey retained foreign-scope denial and deletion
tombstones after another simulated restart. Hosted Runtime CI run
[34416888418](https://github.com/magnus919/groktocrawl-x/actions/runs/34416888418)
passed Integration Tests, PostgreSQL Storage Probes, Twin Contracts, and Runtime
Gate. W6 is complete for the bounded experimental route. Live-provider quality,
production authorization, and production readiness remain W7 concerns.

See [ADR-0072](../adr/experimental/groktocrawl-x/0072-expose-verified-research-through-an-experimental-protocol.md),
the [proposed client protocol](research-client-protocol.md), and
[issue #112](https://github.com/magnus919/groktocrawl-x/issues/112).
