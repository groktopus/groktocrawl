# Experimental research client protocol: proposed wire examples

Design companion to [ADR-0072](../adr/experimental/groktocrawl-x/0072-expose-verified-research-through-an-experimental-protocol.md),
D6 / issue [#9](https://github.com/magnus919/groktocrawl-x/issues/9).
**These routes, fields and traces are proposed, not implemented or tested API behavior.**
They reuse the fictional [Acme Archive fixture](research-foundation-example.md).
Opaque IDs below are readable fixture labels; production IDs have no embedded authority.

## Proposed surface and compatibility

Prefix: `/experimental/research/v1`. Existing `/v2` clients remain unchanged.
This is an explicitly enabled experimental capability, not a mainline replacement.
The implementation must freeze a complete schema before W6; these examples define
lifecycle invariants and minimum fields, not an OpenAPI document.

| Operation under prefix | Meaning | Proposed CLI / MCP mapping |
|---|---|---|
| `GET /capabilities` | Version, enablement, limits, replay window and recovery mode | `research capabilities` / `research_capabilities` |
| `POST /runs` | Admit one run; accept webhook and idempotency key | `research create` / `research_create` |
| `GET /runs/{run_id}` | Authoritative state and committed result | `research status` / `research_status` |
| `GET /runs/{run_id}/events` | Observe bounded application event stream | `research watch`; MCP uses typed status/progress |
| `POST /runs/{run_id}/cancel` | Request cancellation; return current state | `research cancel` / `research_cancel` |
| `GET /artifact-sets/{artifact_set_id}` | Pinned manifest and audit/reference mappings | `research show` / `research_show` |
| `GET /artifacts/{artifact_id}` | Exact retained bytes for one layer | `research download` / `research_artifact` |
| `GET /research/{research_id}/evidence/{snapshot_id}` | Scoped retained evidence and locator metadata | `research evidence` / `research_evidence` |
| `DELETE /research/{research_id}` | Explicit root deletion under ADR-0071 | `research delete` / `research_delete` |
| `POST /sessions/{session_id}/attachments` | Attach a completed result using expected revision | `research attach` / `research_attach` |

Artifact download is bounded by declared size limits. Evidence reads are authorized
through the research root, not through globally addressable content hashes. Deletion
returns 202 once a tombstone makes normal reads unavailable; physical cleanup is
bounded background work. An idempotent repeated deletion returns the same receipt
while retained. Session attachment does not transfer ownership or broaden access.
Fresh research uses create with a new idempotency key; re-render/export/import
operations remain W3 follow-ups and are not implicitly available through these reads.

## Create and observe

A client generates and persists an idempotency key before sending a request:

```http
POST /experimental/research/v1/runs
Content-Type: application/json
Idempotency-Key: price-check-001
```

```json
{
  "protocol_version": "research/1",
  "objective": "For Acme Archive Pro, what monthly price and audit-log access apply to a US individual on 2026-09-01?",
  "as_of": "2026-09-01",
  "webhook": null
}
```

Successful admission returns 202, a Location header for polling, and:

```json
{
  "protocol_version": "research/1",
  "run_id": "run-price-001",
  "research_id": "research-price-001",
  "state": "accepted",
  "status_url": "/experimental/research/v1/runs/run-price-001",
  "events_url": "/experimental/research/v1/runs/run-price-001/events"
}
```

Idempotency is scoped to authenticated scope, operation and key. The server binds
it to normalized request identity, including schema and webhook; different input
with the same key returns 409. An accepted duplicate returns the same run without
new provider work. Admission and receipt creation must be atomic. Declare receipt
retention and maximum key length in capabilities. After retention, clients must
reconcile an ambiguous old request rather than assume a reused key prevents work.
Before admission, a 429 includes retry metadata and no run is created; after
admission, observation retries do not repeat creation. D5 must establish what
admission guarantees across process loss before claiming crash-safe deduplication.

Nonterminal states are `accepted`, `running` and `cancel_requested`; terminal states
are `completed`, `failed` and `cancelled`. Coverage and stop reason are terminal
fields; do not invent a final coverage score from live counters. A status response
for a nonterminal state has `result: null` and optional bounded progress metadata.

The observer opens GET `/runs/run-price-001/events`. A short illustrative trace:

```text
id: run-price-001:1
event: accepted
data: {"protocol_version":"research/1","run_id":"run-price-001","state":"accepted"}

id: run-price-001:2
event: progress
data: {"protocol_version":"research/1","run_id":"run-price-001","stage":"acquisition","completed_sources":2}

id: run-price-001:3
event: progress
data: {"protocol_version":"research/1","run_id":"run-price-001","stage":"verification","unresolved_questions":1}
```

Stages come from an application vocabulary (`acquisition`, `knowledge`,
`verification`, `rendering`, `publication`), not framework node names. Counters may
be partial and never establish source support. No answer prose has been emitted.
Unknown additive progress fields may be ignored; an unknown terminal event or major
version requires reconciliation through status, never an assumption of success.

## One audited terminal

After all three renderings are audited and published, `id: run-price-001:4`,
`event: done` carries this JSON. GET status returns the same terminal object:

```json
{
  "protocol_version": "research/1",
  "run_id": "run-price-001",
  "state": "completed",
  "execution_outcome": "completed",
  "answer_coverage": "partial",
  "stop_reason": "unresolved_conflict",
  "result": {
    "research_id": "research-price-001",
    "ir_revision_id": "ir-001",
    "artifact_set_id": "set-price-001",
    "summary": "Both captured pages list audit logs as included. [1][2] They disagree on the monthly Pro price: the pricing page lists USD 20, while the help page lists USD 30 for the same account and billing scope. [1][2] These snapshots do not establish which price applies as of September 1, 2026. [1][2]",
    "citations": {"1": "src-pricing", "2": "src-help"},
    "manifest_url": "/experimental/research/v1/artifact-sets/set-price-001",
    "artifacts": {
      "summary": "/experimental/research/v1/artifacts/summary-price-001",
      "analysis": "/experimental/research/v1/artifacts/analysis-price-001",
      "dossier": "/experimental/research/v1/artifacts/dossier-price-001"
    },
    "unresolved_questions": [
      "Which price applies to a US individual monthly subscription as of 2026-09-01?"
    ]
  }
}
```

The manifest additionally supplies required byte digests, snapshot IDs/locators,
verification/audit records and statement-to-claim mappings from ADRs 0069/0071.
Those are not replaced by this compact event's citations. A client verifies each
downloaded layer against its manifest digest, and verifies the summary field equals
the retained summary text under the schema's declared text encoding. (No synthetic
digest is invented in this trace.) `completed` with partial coverage is intentional:
the conflict is visible and audited. Verification means passing recorded checks
against retained evidence, not proof that a disputed real-world price is true.

## Disconnect, replay and reopening

1. Disconnect after event 2. The research run continues; no cancellation is inferred.
2. Reconnect with `Last-Event-ID: run-price-001:2`. If retained, receive events after
   2, possibly with transport duplicates; deduplicate IDs before displaying progress.
3. If the cursor expired, HTTP 410 returns `cursor_expired` and the authorized
   `status_url`. Fetch status and display the committed result if available.
4. If the process lost progress history, reconcile the explicit replay gap the same
   way. A nonterminal status after a crash is not evidence of live execution; D5
   must define interruption detection/reconciliation before unattended operation.
5. Reopening a completed artifact reads its manifest and stored bytes. It performs
   no search, LLM call or freshness refresh. Session expiry does not affect a
   separately retained research root; expiry of the root does.

Events must be re-authorized at read time. After deletion/purge, old terminal text
must not be replayed. A connection already streaming is invalidated at the next
server authorization boundary; bytes already sent cannot be recalled. Specify that
boundary and test the deletion/replay race before W6 acceptance. Admission receipts
and terminal status cannot resurrect deleted artifacts.

## Failure and cancellation examples

HTTP failures retain ADR-0032's `success: false`, string `error`, `error_code`
and optional `details` envelope. Proposed codes include `CURSOR_EXPIRED`,
`RESOURCE_UNAVAILABLE` and `IDEMPOTENCY_CONFLICT`; freeze the complete code/status
mapping before implementation. Admission 429 retains `RATE_LIMITED` and ADR-0053
retry metadata. Terminal run failures are distinct status/event projections,
not an HTTP 200 pretending a rejected admission succeeded.

A required render audit failure has `event: error`, never `done`:

```json
{
  "protocol_version": "research/1",
  "run_id": "run-price-002",
  "state": "failed",
  "execution_outcome": "failed",
  "answer_coverage": "insufficient",
  "stop_reason": "verification_failed",
  "result": null,
  "error": {"code": "render_audit_failed", "retryable": false}
}
```

A cancellation request returns 202 with `cancel_requested` if pending, or 200 with
an already terminal projection. Cancellation is not complete until `cancelled` is
committed. Its terminal event is `cancelled`, with matching state/outcome,
`stop_reason: cancelled`, actual assessed coverage and `result: null`. Repeated
cancellation returns the same state; publication winning the race returns the
completed result instead. Never emit both successful `done` and cancelled terminals.

An insufficient but audited answer instead uses `done`, `state: completed`,
`answer_coverage: insufficient` and qualified text explaining what cannot be
established. Missing evidence and output-schema failure must not be disguised as
this legitimate knowledge outcome. Transport failures after a terminal commit do
not change the outcome; clients use status. Webhook notifications reference the
same immutable terminal and stable notification ID, with authorized retrieval
links only. Delivery status is separate from research execution status.

## Confirmation matrix

This is a future W6 test specification, not an executed golden test suite. The
ADR identifies owners, invocation, exceptions and retirement. Freeze full schemas,
limits, fixtures and traces before implementing adapters. Archive evidence at the
commit that produced it; report skipped paths and recovery-mode limits explicitly.

| Fixture / scenario | Required observable result | Evidence lane |
|---|---|---|
| Supported, conflicting and insufficient evidence | Same pinned artifact and coverage across poll, SSE, CLI, MCP; no pre-audit prose | W6 fixture integration + client golden traces |
| Failing support/render audit; unsatisfiable structured schema | Failed terminal, null result, no successful done or exposed staged artifact | W2/W6 negative fixtures |
| Duplicate create, changed payload, lost admission response | Same run for identical retained key; 409 for conflict; no duplicate acquisition | W6 admission/receipt tests; D5 crash injection |
| Disconnect, duplicate event, stale/foreign cursor | Ordered deduplicated projection or explicit gap; no new run | W6 stream/status tests |
| Slow reader and oversize artifact | Bounded buffers; no truncated success; publication independent of observer | W6 resource-limit tests |
| Cancel versus publish; webhook versus connection failure | One terminal outcome; no durable-delivery claim without D5 evidence | W6 race tests + D5 fault injection |
| Session expiry and concurrent attachments | Research survives session expiry; revision conflict is 409; citations stable | W3/W6 lifecycle fixtures |
| Wrong scope, missing root, expired/deleted evidence | No text leakage; 404/authorized 410; replay, downloads and webhook links respect tombstones | W3/W6 authorization/deletion race tests |
| Stored pyramid download | All three layers match manifest bytes/digests; no client LLM call | W6 CLI/MCP artifact tests |
| Existing clients without experiment opt-in | Inherited wire/CLI/session behavior unchanged | Existing regression suites + W6 compatibility traces |
| Process interruption | Explicit advertised limitation or demonstrated D5 recovery; never fabricated completion | D5 fault-injection report |

Hard invariants allow zero violations. Missing or indeterminate results block
rollout. These fixtures cannot establish semantic truth or production performance;
use D7's independent assessment and frozen baseline for quality/latency claims.
