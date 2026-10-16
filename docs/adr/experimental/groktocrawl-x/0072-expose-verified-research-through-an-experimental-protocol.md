# Expose Verified Research Through an Experimental Protocol

- Status: accepted
- Deciders: Magnus Hedemark
- Date: 2026-09-04
- Scope: experimental research clients; `magnus919/groktocrawl-x` only
- Plan: D6 / W1 and W6; issue [#9](https://github.com/magnus919/groktocrawl-x/issues/9)
- Supersedes: experimental scope only, as classified below; inherited endpoints remain unchanged
- Experimental successor: Extended by ADR-0075 at the knowledge/artifact format boundary; public delivery and principal authentication remain separate work. [ADR-0075](0075-consolidate-research-interchange-contracts.md), accepted 2026-09-06.

- Accepted: 2026-09-05 by Magnus Hedemark, following the W1 review packet.
- Acceptance scope: foundation contracts for a bounded fixture-backed prototype;
  implementation/evidence gates remain. No storage/runtime/recovery selection or
  external-provider spending is authorized by this acceptance.
- Acceptance record: [issue #15](https://github.com/magnus919/groktocrawl-x/issues/15).

## Context and Problem Statement

ADRs 0068–0071 propose retained evidence, versioned knowledge and audited render
sets. The inherited agent/answer streams emit synthesis tokens before that new
render audit exists. Their current terminal and session contracts cannot silently
acquire a stronger meaning merely because the execution engine changes.

A client must distinguish acquisition progress from publishable knowledge, recover
from a dropped connection without buying another research run, and reopen the same
artifact after its session expires. Transport replay is not execution recovery.
D5 still owns restart guarantees, leases, retries and webhook delivery durability.

## Decision Drivers

- Do not present unaudited prose as verified output.
- Preserve existing HTTP, CLI, MCP and session behavior by default.
- One pinned, scoped artifact set across streaming, polling and reopening.
- Explicit coverage, conflict, cancellation, missing-evidence and failure outcomes.
- Bounded progress retention and predictable client recovery without exposing
  framework checkpoints, internal reasoning or provider-specific events.
- Define observable contracts before selecting a workflow runtime.

## Considered Options

### Protocol placement

| Option | Benefit | Cost |
|---|---|---|
| Replace inherited `/v2/agent` and `/v2/answer` behavior | One surface | Breaks timing, response and cancellation expectations before migration evidence |
| Add a version opt-in to existing requests | Reuses routes and clients | Multiple lifecycle meanings on the same endpoints; easy for adapters to lose the opt-in |
| Separate experimental research route family | Explicit capability and compatibility boundary | Additional routes, CLI commands and MCP tools to maintain |

### Answer emission

| Option | Benefit | Cost |
|---|---|---|
| Verified-only final result after whole-render audit | One immutable answer with coherent citations and conflict qualifications | Longer time to first answer text |
| Independently verified chunks | Earlier usable prose | Cross-chunk qualifications and final revisions require a new composition audit contract |
| Explicit provisional revisions | Earliest draft feedback | Every client must implement invalidation, revisions and provisional labels; drafts can escape downstream |

## Decision Outcome

Recommend a **separate experimental route family with verified-only final output**.
The proposed protocol identifier is `research/1` and route prefix is
`/experimental/research/v1`. Neither is implemented by this ADR. Keep inherited
routes and client defaults unchanged. Do not route existing calls into this path
until a separately reviewed migration establishes compatibility.

Use asynchronous create/poll as the common lifecycle. A separate GET event stream
observes the run; connection loss does not cancel it. Explicit cancellation is a
mutation. The server advertises experimental enablement and recovery capability;
clients must reject an unsupported major version and must not fall back by silently
executing the same request through an inherited endpoint.

### Publication and terminal results

Acquisition, verification and rendering may emit bounded progress metadata, never
answer tokens, raw prompts, chain of thought or framework node names. Discovery
candidates are not final citations. Only after IR verification, whole-render audit
and atomic publication of the complete summary/analysis/dossier manifest may a
`done` event contain `result`. Polling returns the identical committed terminal
projection. No `token` events or provisional text exist in `research/1`.

A result carries `run_id`, `research_id`, `ir_revision_id`, `artifact_set_id`,
execution outcome, answer coverage, stop reason, unresolved questions and stable
artifact links. The manifest pins renderer/audit versions, exact digests and
statement-to-claim mappings under ADRs 0069/0071. `done.result` contains bounded
summary text plus links to all three retained layers; it does not concatenate an
unbounded dossier into the event. Reads expose the stored artifact bytes, not a
new LLM rendering. Bounds and their defaults must be frozen in the W6 schema and
measured against the W1 corpus before shipping; oversize publication fails before
`done`, never silently truncates text or reference mappings.

`completed` means the run followed its declared stopping policy, not that every
question was answered. Coverage remains `complete`, `partial` or `insufficient`.
A qualified report of contradictory evidence can pass the audit and complete with
partial coverage. A provider error produces failure unless the versioned policy
explicitly permits publishing the already verified partial result. Required audit
failure cannot become an ordinary factual answer. Failed and cancelled terminals
have `result: null`; they do not expose staged outputs as published artifacts.

Terminal outcomes are immutable. Publication and cancellation must resolve through
one guarded terminal transition: if publication wins, cancellation returns the
completed state; if cancellation wins, a late writer cannot publish. Retrying an
ambiguous mutation resolves its receipt before repeating external work. This is a
required D5/W6 invariant, not a claim about current restart-safe execution.

### Identity, access and retention

IDs are opaque, stable and never authorization credentials. Authorize every run,
artifact, evidence and replay read using the server-derived scope. Do not trust a
client-supplied scope or accept cached/index content without checking authoritative
access and deletion state. Outside-scope and nonexistent IDs both return 404.
An authorized expired/deleted resource may return 410 while its minimal tombstone
is retained; after tombstone expiry it returns 404. Responses reveal no retained
text after deletion. Temporary storage failures return retryable unavailability,
not a fresh research run or a fabricated empty answer.

Numeric citations are local presentation mappings for one pinned artifact set;
stable source/claim IDs and snapshot locators are the evidence identity. Fresh
research creates a new run and revision. Re-rendering creates a new artifact set
against an explicit retained revision. Neither operation mutates previously
published bytes or claims that old evidence is fresh. Missing or purged required
evidence invalidates normal artifact access under ADR-0071, even if a stale event
still contains a formerly valid result. Replay and webhook dispatch must enforce
this invalidation; a cached terminal is not an authorization shortcut. Bytes
already delivered to a client cannot be recalled.

### Polling, streaming and replay

The [worked protocol](../../../experiments/research-client-protocol.md) defines the
proposed routes and traces. Application events have run-scoped ordered IDs and a
bounded retained cursor window. `Last-Event-ID` resumes after a retained cursor;
duplicate delivery is permitted and clients deduplicate by run/event ID. An
invalid cursor returns 400, an expired cursor returns 410 with an authorized status
link, and a cursor from another run is rejected without exposing that run. Never
silently restart the stream at event one or restart research.

Polling is authoritative; clients reconcile a replay gap by reading current state.
A completed, still-accessible run can be reopened without its old progress log.
A stream ending without a terminal is an unknown transport outcome, not success.
Backpressure disconnects a slow observer with bounded buffering; it does not block
research publication. Heartbeat comments carry no research state or ordered IDs.
Restart-safe progress replay is not promised before D5. An implementation must
advertise its replay window and recovery mode; loss of ephemeral progress produces
an explicit gap. An interrupted run must never be reported as completed merely
because a worker disappeared. D5 acceptance is required before recovery guarantees
or an unattended pilot ship.

### Sessions, structured output and client parity

Experimental sessions hold compact references to run and artifact identities.
Attaching a completed result uses a scoped idempotent mutation with an expected
session revision; conflicting revisions return 409. Concurrent independent runs
can finish in any order; attachment never renumbers citations or concatenates
artifact prose. Session expiry removes the convenience reference, not the retained
research root. Session deletion does not delete research; explicit root deletion
uses the ADR-0071 ownership and retention contract. Keep inherited search/scrape
parallel steps and serialized query/deepen semantics unchanged.

An optional structured projection is generated from the same pinned IR, validated
against the requested schema and audited for claim support. Schema validation alone
is insufficient. If a schema cannot represent the recorded uncertainty or demands
unsupported facts, return a structured-output failure with no successful result;
do not coerce an unknown value into a fabricated answer. Record the requested
schema identity in the manifest and keep protocol/status metadata outside user
schema fields. W6 must freeze supported schema features and limits.

HTTP, CLI and applicable MCP operations must ship together, including create,
status, cancel, artifact/evidence reads and session attachment. CLI progress goes
to stderr; stdout contains one final machine-readable result or an atomically
written pyramid path. Downloads use stored bytes and verify manifest digests; a
CLI must not regenerate the analysis/dossier locally. MCP maps the same typed
results and errors without requiring its consumers to parse our SSE transport.

Creation accepts `webhook`. Terminal notifications carry a stable notification ID,
run ID, outcome and authorized retrieval link, not raw research text. The async
worker invokes delivery on completion, failure and cancellation; duplicate delivery
is allowed and consumers deduplicate. Honor inherited destination validation and
rate-limit contracts. D5 must define retry limits, outbox atomicity and restart
replay before claiming durable delivery. A webhook failure cannot undo publication
or convert a completed result into a failed research run.

### Intended predecessor scope on acceptance

| ADR | Proposed treatment in this fork |
|---|---|
| 0017, 0022 | Retain inherited answer/agent behavior; add experimental asynchronous lifecycle rather than silently changing their inline streams |
| 0024 | Partially supersede for experimental output: download server-audited pyramid bytes instead of CLI-side transformation |
| 0032 | Retain standardized HTTP error envelope; add experimental error codes and typed terminal failure projections |
| 0039, 0042 | Retain API/CLI/MCP parity; extend inventories and fixtures for the new route family |
| 0041 | Distinguish retained artifact reads from semantic research-memory reuse; no cache hit bypasses scope or verification |
| 0040, 0063, 0066 | Retain inherited session operations; add compact research attachments with explicit concurrency semantics |
| 0053 | Retain retryable rate-limit metadata; distinguish admission retry from admitted-run observation |
| 0064, 0065 | Retain one final answer and prompt acquisition progress; experimental text waits for audit/publication instead of streaming during synthesis |

Predecessor metadata records the exact experimental successor scope. Accepted
predecessor bodies remain unchanged; this acceptance does not accept D4/D5.

## Consequences

Clients can distinguish progress, qualified knowledge and transport uncertainty.
The same artifact can be checked across interfaces without regenerating prose.
Costs include more public operations, retained terminal/replay metadata, extra
contract tests and slower time to first answer text. Progress cannot conceal that
latency; measure both time to first progress and time to audited result. Independent
chunk verification and provisional revisions remain future alternatives if those
measurements justify their complexity.

## Confirmation

The [protocol confirmation matrix](../../../experiments/research-client-protocol.md#confirmation-matrix)
is the operational record. Magnus Hedemark owns the decision and consumes evidence;
the W6 API/CLI implementer owns golden traces and CI checks. Run fixture-based
contract tests on every protocol/client change, with zero identity, access,
publication or compatibility violations. Missing evidence blocks rollout. Required
scenarios include conflict/insufficiency, audit/schema failures, duplicate replay,
disconnect, cursor expiry, scope isolation, deletion, cancellation/publication races,
webhooks and all clients. D5 fault-injection evidence is additionally required for
restart claims. No such implementation evidence is supplied by this draft.

Record run URLs, commit, fixture/version and exclusions in the W6 acceptance report.
Latency gates use a frozen W1 baseline and D7 evaluation protocol; do not invent a
performance success from a synthetic trace. A fixture/instrumentation failure is
indeterminate, not compliant. Exceptions require a maintainer-approved scope,
compensating control and expiry; do not relax verification/access invariants to
ship a pilot. Review after protocol/retention changes or a replay/publication
incident; retire checks only with a successor contract and preserved evidence.

## Links

- [Experiment plan](../../../experiments/research-architecture.md)
- [ADR-0068: boundaries](0068-separate-research-execution-knowledge-and-rendering.md)
- [ADR-0069: knowledge and verification](0069-define-versioned-knowledge-and-verification.md)
- [ADR-0070: evaluation](0070-evaluate-research-policy-and-runtime-separately.md)
- [ADR-0071: retained evidence](0071-store-research-evidence-independently-of-sessions.md)
- [Inherited final synthesis](../../0064-one-final-research-synthesis.md)
- [Inherited independent sessions](../../0066-opt-in-to-independent-session-steps.md)
