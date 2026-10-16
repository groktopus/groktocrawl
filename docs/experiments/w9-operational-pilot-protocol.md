# W9 bounded operational pilot protocol

Status: **operational pilot complete; three checkpoints passed, bounded architecture ratified by Magnus on September 30**

## Question and decision

Can the isolated candidate remain healthy and complete representative inherited
and experimental research work over a real observation window without losing
artifact authority, vector consistency, client compatibility, or rollback
readiness?

Passing permits the final W9 adoption decision. It does not make the candidate a
mainline replacement or prove production scale.

## Window and minimum exposure

- Current window began at `2026-09-23T00:09:25.966168523Z`, after the
  experimental candidate was pinned to LiteLLM's `free` model alias.
- Checkpoint 0 passed at `2026-09-23T00:12:34.352827Z` and checkpoint 1
  passed at `2026-09-26T19:38:03.623260Z`; each completed all 12 declared
  operations on the frozen candidate. Checkpoint 2 passed its single permitted retry at `2026-09-30T19:40:57.866103Z`.
  The completed window has 36 successful operations and 3/3 checkpoints.
  [Final evidence and recommendation](w9-replacement-readiness-decision.md) retain
  the first attempt’s model-health timeout and the owner’s completed bounded ratification.
- Required exposure: at least seven elapsed days **and** at least 30 successful
  representative requests. Both conditions are mandatory.
- Checkpoints: start, no earlier than 72 elapsed hours, and no earlier than 168
  elapsed hours. A delayed checkpoint extends the pilot; it never shortens it.

Each checkpoint runs the frozen candidate compatibility suite: scrape, bounded
crawl, search, synchronous and streaming answer, synchronous and streaming
agent, webhook, CLI scrape/search, and MCP scrape/search. It also runs the
cross-client experimental research verification. This yields at least 12
declared user operations per checkpoint and at least 36 over three checkpoints.
Health probes and job-status polling do not count toward the request minimum.

## Frozen boundaries

- Candidate: `groktocrawl-x-candidate` on the candidate host, with a
  key-protected API and allowlisted MCP service published on configured trusted
  interfaces.
- Search: the pinned SlopSearX image already matched to the incumbent.
- Inference: the configured OpenAI-compatible provider and pinned model alias.
- Artifact authority: PostgreSQL schema 14.
- Semantic serving: pgvector, with Qdrant retained as the tested rollback copy.
- Fixtures and operation bounds: the W9 compatibility protocol.

No cache is deliberately cleared between checkpoints. No failed attempt is
discarded. Configuration or code changes restart the seven-day window unless
they only correct observation tooling and leave the deployed candidate revision
and runtime configuration unchanged.

The original window failed after a host restart: the private environment and
PostgreSQL secret bind sources had been placed under volatile temporary storage,
so Compose could not recreate the stopped containers. The outage remains in the
evidence record. Moving the same private values to persistent per-user storage
changed the operational configuration boundary, so the pilot restarted. The
candidate source and runtime image revisions did not change.

The corrected loopback-only window then passed checkpoint 0. It was deliberately
ended on 2026-09-19 when the owner chose to route normal client use through the
candidate. Publishing the API and MCP ports on trusted host interfaces changed
the operational configuration boundary, so the clock and successful-operation
count restarted again.

A subsequent real-client audit found three runtime defects: streaming search
failed in the CLI, the candidate omitted Parse, and web similarity timed out
before embeddings completed. Repairing those defects and the staged Parse
metadata loss discovered during verification changed the deployed runtime. The
preceding real-use window remains retained evidence but cannot count toward the
final stability gate. The repaired window began at
`2026-09-19T18:12:36.512533Z` and passed checkpoint 0 at
`2026-09-19T18:15:21.395820Z` with all 12 declared operations successful.
Live verification then showed that the public staged Parse workflow could not
reserve an upload identifier without an internal storage write. Restoring the
authenticated reservation route and automatic CLI reservation changed the
runtime once more. The preceding repaired window remains retained evidence but
cannot count toward the final stability gate. The final window began at
`2026-09-19T19:00:53.139536Z` and passed checkpoint 0 at
`2026-09-19T19:03:37.101501Z` with all 12 declared operations successful.
An independent agent-use audit then showed that model-backed operations failed
for most of its observation window while retrieval and document workflows
remained available. Candidate logs traced the common failure to a retired model
alias left in deployment configuration. Correcting the alias restored rich
search, grounded answer, focused agent research, and structured extraction in
serial smoke tests. Because inference identity is a frozen boundary, the failed
window remains evidence but cannot count toward the final gate. The corrected
window began at `2026-09-19T21:46:56.859244Z` and passed checkpoint 0 at
`2026-09-19T21:50:36.553057Z` with all 12 declared operations successful.
An independent retest then confirmed 27 passed cases, two partial projections of
the same local-vector relevance concern, and no failures. The bounded follow-up
rejected both query-time cross-encoder reranking and a cleaned representation;
raw cosine retrieval passed its labeled screen. Finally, making the proven
14-CPU semantic allocation durable in the public Compose configuration changed
the deployment boundary and restarted the window. The current window began at
`2026-09-20T05:16:52.26916171Z`. Its [checkpoint 0](evidence/replacement-rehearsal/2026-09-20-current-runtime-checkpoint-0/README.md)
passed all 12 declared operations at `2026-09-20T05:23:26.651758Z`.

The candidate agent image changed on `2026-09-21T17:45:02.472507659Z`, ending
that window before checkpoint 1. Candidate SlopSearX was then recreated with
Semantic Scholar key wiring on `2026-09-22T00:08:23.276489269Z`; a targeted
keyed search returned five results with engine status `ok`. Both changes are
outside the former frozen boundary. The earlier 12 operations remain evidence
but count as zero toward the next window. Do not run the previously scheduled
September 23 or September 27 gates. The next September 22 window passed
[checkpoint 0](evidence/replacement-rehearsal/2026-09-22-current-main-checkpoint-0/README.md)
but ended before checkpoint 1 when a separate model comparison recreated the
candidate agent with an older image and model alias `free`. Its 12 operations
remain historical evidence only. The candidate was restored to the frozen
current-main revision and `general` alias at
`2026-09-22T19:16:26.093854409Z`. A first checkpoint attempt received HTTP
502 as the shared model gateway restarted; its failure is retained. The
unchanged candidate passed the protocol's single retry with all 12 declared
operations at `2026-09-22T19:20:21.394803Z`. The
[current checkpoint packet](evidence/replacement-rehearsal/2026-09-22-free-model-restart-checkpoint-0/README.md)
contains both attempt outcomes.

The owner then selected LiteLLM's `free` alias for the experimental candidate
only. The previous `general` window ended before checkpoint 1; its 12 operations
are retained history and have zero credit in the new window. The agent image
and runtime revision stayed at `46a528228b1365189cdd38d0bcdb12109a8dc763`.
A first checkpoint attempt used a newer source checkout than the deployed
revision and failed its source-revision guard after compatibility operations;
this was a test setup error and earned no credit. The clean matching checkout
passed [checkpoint 0](evidence/replacement-rehearsal/2026-09-23-free-model-checkpoint-0/README.md)
with all 12 declared operations, including text and structured-output model
readiness. Mainline and Hermes were not changed.

## Observations

At every checkpoint retain:

- the sanitized compatibility and cross-client verification receipts;
- the exact deployed revision and provider/model identities;
- terminal outcomes, failure classes, and per-operation latency distributions;
- artifact-set, vector-provider, and client-path reconciliation results;
- point-in-time service health and bounded resource observations.

The roadmap heartbeat checks due gates and candidate health. It stays quiet
while healthy and records or reports only a material state change, failure,
completed gate, or required user action.

## Pass and stop rules

Pass only after both time and request thresholds when:

1. every declared request has a terminal receipt, with no unexplained
   candidate-only contract failure;
2. experimental research artifacts remain identical across HTTP, SSE, CLI, and
   MCP retrieval paths;
3. PostgreSQL remains authoritative, pgvector remains the serving provider, and
   Qdrant remains healthy and count-consistent as the rollback copy;
4. no retained deletion reappears and no backup/restore or reconciliation alarm
   remains unresolved;
5. observed latency or resource behavior shows no repeated material regression
   that would prevent ordinary home-lab operation.

Stop and preserve evidence on unexplained data loss, authority ambiguity,
repeated failed completion, rollback-copy divergence, or a required candidate
runtime change. A single transient dependency failure is recorded and retried
once; it is not silently converted into a pass.

## Limits

This pilot uses one candidate host, a small fixed fixture set, and three
scheduled suites. It does not measure multi-user saturation, regional failure,
Internet-wide acquisition, physical disaster recovery, or production support
load. Resource snapshots on this host are diagnostic rather than capacity proof.
