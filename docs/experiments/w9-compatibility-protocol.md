# W9 incumbent compatibility protocol

Status: **frozen before execution**

## Question and decision

Can the isolated GroktoCrawl-X candidate perform the existing scrape, crawl,
search, answer, agent, webhook, CLI, and MCP journeys without an unexplained
contract regression, while preserving its additional auditable research path?

Passing this comparison permits the migration and rollback rehearsal. It does
not authorize production cutover or establish research-quality improvement.

## Arms and shared boundaries

| Arm | Deployment | Mutable state |
|---|---|---|
| Incumbent | Existing GroktoCrawl deployment on `incumbent.example.internal` | Incumbent Valkey and Qdrant volumes |
| Candidate | Frozen `groktocrawl-x-candidate` deployment on `inference.example.internal` | Candidate-only PostgreSQL, Valkey, pgvector, and rollback Qdrant volumes |

Both arms use LiteLLM's `local` model on `inference.example.internal`. The incumbent reaches it
through its existing TLS route while the candidate uses the home-lab route.
Both arms use the same pinned SlopSearX image digest. The comparison client runs
on `inference.example.internal`, so the candidate remains loopback-only while the incumbent is
accessed through its already-published home-lab ports.

No cache is cleared, no service is restarted, and no incumbent configuration or
state is changed for the comparison. Run order alternates by repetition. Every
attempt, including failures and timeouts, remains in the raw receipt.

## Fixed cases

- **Acquisition/crawl URL:** `https://docs.python.org/3/tutorial/introduction.html`
- **Live search query:** `agentic engineering software factory enterprise`
- **Grounded-answer question:** `What distinguishes an agentic engineering software factory from ordinary CI automation?`
- **Agent task:** the same question, using focused search and a bounded credit limit
- **Crawl bound:** two pages, depth one, sitemap skipped
- **Search/answer source bound:** three results
- **Per-operation deadline:** 300 seconds
- **Repetitions:** three per arm

The fixed URL and query are public and contain no private material. Live search
and model output can vary; compatibility is therefore judged on response shape,
terminal behavior, non-empty grounded output, source/citation structure, and
failure class. Content hashes and counts are retained for diagnosis without
claiming byte-identical model output.

## Webhook verification

Each arm receives a separate ephemeral Webhook.site token. The crawl requests
only the `crawl.completed` event. The runner polls the token's request API,
records the event name, HTTP method, payload digest, and job-ID match, and then
deletes the token. Tokens, callback URLs, request bodies, and headers are not
written to the durable receipt.

## Compatibility classifications

- **Exact:** status code, terminal state, required fields, and documented types match.
- **Functionally equivalent:** both complete the user journey, with differences
  limited to volatile IDs, timing, retrieved sources, or generated prose.
- **Intentional experimental difference:** candidate-only research capabilities
  are additive and do not alter the inherited route.
- **Regression:** the candidate fails or changes an inherited contract that the
  incumbent completes.
- **Incumbent limitation:** both fail in the same documented way, or only the
  incumbent fails an additive candidate feature.
- **Untested:** no successful observation; this cannot be counted as a pass.

## Outcomes and gates

Primary gates:

1. no candidate-only failure in scrape, crawl, search, answer, agent, CLI, MCP,
   HTTP polling, SSE completion, or webhook delivery;
2. required response fields and terminal states match on all inherited routes;
3. HTTP, CLI, and MCP retrieve non-empty content for their fixed journeys;
4. the already-frozen candidate research receipt continues to prove identical
   retained artifacts across HTTP, SSE, CLI, and MCP.

Diagnostics include latency for every attempt, result/source/citation/page
counts, output hashes, webhook observations, and before/after container CPU,
memory, network, and block-I/O snapshots. Report distributions and individual
failures; do not replace them with averages alone.

## Limits

This is a three-repetition operational compatibility comparison on one home-lab
deployment pair. It does not prove Internet-wide acquisition reliability,
production scale, semantic answer superiority, long-duration stability, or
compatibility for endpoints outside the declared inventory. Those claims belong
to later W9 gates or remain explicitly untested.
