# Grounded Q&A Endpoint — Synchronous Single-Turn with SSE Streaming

* Status: accepted
* Deciders: magnus, jasper
* Date: 2026-06-06

Technical Story: GroktoCrawl needs a lightweight grounded Q&A endpoint that fills the gap between raw search results (`/v2/search`) and the deep autonomous research agent (`/v2/agent`). This is the most common primitive for AI agent tool use — ask a question, get a cited answer in one round-trip.

- Research-substrate scope: The existing `/v2/answer` contract remains unchanged. Its opt-in research-substrate scope follows [ADR-0069](0069-adopt-bounded-research-substrate.md); the former experiment decision records are preserved in the [provenance archive](experimental/groktocrawl-x/README.md).

## Context and Problem Statement

GroktoCrawl has two modes for answering questions:

| Mode | Endpoint | Latency | Use Case |
|---|---|---|---|
| **Raw search** | `POST /v2/search` | <1s | Caller wants URLs, will synthesize themselves |
| **Deep research** | `POST /v2/agent` | 5-15s+ | Caller wants thorough multi-source investigation |

A caller who wants to know "What is the current Fed interest rate?" should not need to:
1. Call `/v2/search`, get 10 URLs
2. Scrape each URL
3. Feed context to an LLM themselves
4. Parse citations from the LLM output

Nor should they need to wait 5-15s for the full agent research loop when the question needs one search + one scrape + one LLM call.

This missing middle is what Exa's `/answer` endpoint provides — it is the single most common primitive for AI agent tool use.

## Decision Drivers

* Must be a **single-turn** request-response — one call, one answer. No job ID, no polling.
* Must return in **1-3s** (vs 5-15s for the agent)
* Must be **citation-grounded** — every factual claim must trace to a source URL
* Must support both **synchronous** (simple consumption) and **streaming** (real-time display) modes
* Must **reuse existing infrastructure** — SearXNG for search, scraper-svc for content extraction, the configured LLM for synthesis. No new dependencies.
* Must not duplicate the agent's research loop — this is a simpler, faster path, not a competing agent

## Considered Options

### A. Synchronous single-turn endpoint with SSE streaming *(chosen)*

A single `POST /v2/answer` endpoint that returns synchronously. Internally runs search → scrape top N results → LLM synthesis → deterministic citation normalization and validation. Optional `stream: true` flag switches to Server-Sent Events for real-time token delivery.

**Pipeline:** `request → SearXNG search → scrape excerpts → freeze numbered source map → one grounded LLM call → incremental citation normalization and validation → response`

**Positive:**
- Fits 1-3s latency target (single LLM call, no iterative search/scrape loop)
- Sync mode: simple curl/agent consumption — no webhook, no polling
- SSE streaming: real-time display in chat UIs
- Fully reuses existing infrastructure (SearXNG client, scraper client, LLM client)
- No new dependencies

**Negative:**
- LLM response time dominates latency for complex queries
- Citation syntax still depends on the LLM following the `[N]` prompt, but invalid output is rejected deterministically instead of being converted into guessed metadata
- No retry mechanism — entire request must be retried on failure

### B. Async job-based endpoint with webhook (existing pattern)

Follow the pattern established by ADR-012: return a job ID, process in background, fire webhook on completion, let caller poll `GET /v2/answer/:id`.

**Positive:**
- Consistent with all other block endpoints (agent, crawl, extract, generate-llmstxt)
- Webhook delivery enables event-driven workflows
- No HTTP timeout concerns — caller can poll at their leisure

**Negative:**
- Adds latency overhead (job creation → async dispatch → polling)
- Requires a new `GET /v2/answer/:id` endpoint and job store entries
- Poor fit for the use case — Q&A should feel like a query, not a job submission
- Webhook infrastructure is overengineered for a 1-3s operation

### C. Extend the agent to auto-detect simple vs deep questions

A single agent endpoint that classifies questions and chooses a fast path (search → scrape → answer) or deep path (multi-step research) accordingly.

**Positive:**
- Single entry point for all question-answering

**Negative:**
- Internal complexity — the agent would need to classify questions, switch between two execution paths, and report which path was taken
- Hard to debug — caller doesn't know whether they got the fast path or deep path
- Defeats the purpose of having a predictable, fast endpoint

### D. SSE-only endpoint (no sync mode)

A streaming-only endpoint with no synchronous fallback.

**Positive:**
- Simpler implementation — one code path

**Negative:**
- Forces every consumer to handle SSE parsing, even for simple use cases
- Incompatible with curl/httpx in simple request mode
- Wastes tokens on the `data: [DONE]` framing for synchronous consumers

## Decision Outcome

Chosen option: **A. Synchronous single-turn endpoint with optional SSE streaming.**

The endpoint is explicitly **not** an async job-based endpoint (contrary to ADR-012's pattern for agent/crawl/extract). The rationale: Q&A is a query, not a job. Sub-second search, scrape in <1s, LLM in <3s — the total is fast enough that async infrastructure (job IDs, polling, webhooks) adds unnecessary complexity.

SSE streaming is an optional overlay on the synchronous path, not a separate processing mode. When `stream: false` (default), the caller receives a complete structured JSON response. When `stream: true`, the same pipeline delivers tokens in real-time with a final `done` event containing the full answer and citation metadata.

### Key Implementation Details

- **Search results:** 1-20 sources (default 5), controlled by `num_sources` parameter
- **Scraping:** Only excerpts are used (first 8000 chars per source) — not full-page content
- **LLM prompt:** Dedicated `ANSWER_SYSTEM_PROMPT` — concise, citation-focused, distinct from the full research agent prompt
- **Citation format:** Numeric `[N]` markers are validated against the frozen source ordering. Grouped markers normalize to individual references, compact links must match the corresponding URL, and metadata is derived only from validated markers.
- **Streaming citation contract (issue #708):** The source map is copied before synthesis and remains fixed for the entire response. Tokens are normalized as they arrive, while ambiguous marker prefixes are held until complete. At provider completion, concatenated token text must exactly equal the provider's final text. Unknown/zero/noncanonical indices, URL mismatches, incomplete numeric markers, missing final completion, or token/final mismatch terminate with `CITATION_INVALID`; no successful `done` event or memory admission follows. Structured JSON is passed through unchanged. No second synthesis, corrective generation, or retry is performed, in keeping with [ADR-0064](0064-one-final-research-synthesis.md).
- **Latency tracking:** `latency_ms` field returned in both sync and streaming modes
- **Auth header fix:** The LLM client's unconditional `Authorization: Bearer` header was changed to conditional (only sent when `api_key` is non-empty). This was necessary because the LLM fixture (and some LLM backends) reject empty Bearer tokens. Discovered during integration testing.

### Concurrent Scraping

Scraping is the dominant cost factor (~90% of wall time). Each URL goes through a three-tier fallback pipeline (llms.txt → Accept: text/markdown → Playwright), and some sites (Britannica, Study.com) exhaust all three tiers taking 30-45s to fail. In the original implementation, URLs were scraped sequentially via `for url in urls[:5]`, producing 1-3 minutes of dead air before any SSE event fired.

**Improvement (PR #135):** `_scrape_urls()` now uses bounded concurrent execution:

- `asyncio.Semaphore(2)` — limits concurrent scrapes to 2, based on scraper-svc capacity probe results (~1.2x speedup)
- `asyncio.wait_for(timeout=20)` — per-URL timeout that cuts off three-tier exhaust at 20s instead of 30-45s
- Early termination — when `min_sources` are successfully scraped, remaining in-flight tasks are cancelled
- Priority ordering — adapter-matched URLs (GitHub, YouTube) run before general web URLs for faster slot turnover

The scraper-svc was confirmed partially concurrent via internal benchmark: 3 fresh Wikipedia articles took 11.35s sequentially vs 9.48s concurrently (~1.2x). True linear scaling is blocked by a shared Playwright browser instance.

### sources_pending SSE Event

The original SSE contract emitted the `sources` event only after all scraping completed — the caller saw dead air for the entire scrape duration. PR #135 added a `sources_pending` event that fires immediately after the search phase, before any scraping begins:

```
event: sources_pending
data: {"sources": [{"url": "...", "title": "..."}, ...]}

event: sources
data: {"sources": [{"url": "...", "title": "...", "content": "..."}, ...]}

event: token
data: {"content": "..."}
```

The `sources_pending` event is **additive** (backwards-compatible per SSE spec). Existing consumers that only handle `sources`, `token`, and `done` ignore the unknown `sources_pending` event type. New consumers can show source URLs in a loading/pending state immediately, eliminating the dead-air UX gap.

### LLMClient.generate_stream()

A new async generator method on `LLMClient` that supports SSE-style token-by-token streaming from any OpenAI-compatible backend. Yields structured events (`token`, `done`, `error`) rather than raw SSE bytes, making it compatible with any streaming consumer. Uses `httpx.AsyncClient.stream()` for efficient backpressure-aware consumption.

### Positive Consequences

* Single-turn Q&A in 1-3s with grounded citations — fills the primary gap in the API surface
* Sync mode enables simple consumption (curl, agent tool calls, CLI)
* SSE streaming enables real-time display (chat UIs, dashboards)
* No new dependencies — reuses `SearXNGClient`, `ScraperClient`, `LLMClient`
* No webhook infrastructure needed for a synchronous endpoint

### Negative Consequences

* Breaks from ADR-012's webhook pattern — every **async** endpoint gets webhooks, but this endpoint is deliberately synchronous. Consumers expecting the async pattern will need to adjust.
* The LLM may still use an unsupported citation style (such as footnotes or parenthetical URLs). The deterministic validator then returns a bounded `CITATION_INVALID` error rather than a successful answer with incomplete or guessed citation metadata. The caller can retry the request itself.
* Search + scrape + LLM latency is additive. Scraping is the dominant cost (~90% of wall time) — each URL traverses up to three scraper tiers (llms.txt → Accept: text/markdown → Playwright). Concurrent scraping (semaphore=2, 20s per-URL timeout) provides ~20% speedup over sequential. Real-world latency for a 3-source answer is 5-25s, exceeding the original 1-3s target.
* **Dead-air UX gap** — the `sources` SSE event fires only after all scraping completes. For multi-source queries this creates 1-3 minutes of dead air. **Mitigation (PR #135):** the `sources_pending` SSE event fires immediately after search results are available, showing URLs in a loading state before scraping begins. Eliminates perceived dead air even when total latency remains unchanged.
* No webhook/retry — callers must handle failures themselves.

## Quality Attributes (arc42 §1.2)

Three quality goals drive the answer endpoint design, distinct from the research agent's quality goals:

| Quality Goal | Scenario | Measure | Priority |
|---|---|---|---|
| **Latency** | A caller submits a factual question ("What is the current Fed rate?"). The endpoint returns a cited answer. | P95 response time <5s for single-source questions; <25s for multi-source synthesis. Dead air mitigated by `sources_pending` SSE event. | High |
| **Citation Accuracy** | Every factual claim in the answer maps to a source URL the LLM actually used. No hallucinated sources or claims unsupported by context. | Precision of cited sources against provided context >95% | High |
| **Graceful Degradation** | Search returns no results, or scraping fails on all sources. The endpoint returns a clear "no information found" message rather than hallucinating. | Zero hallucinated answers when context is empty | Critical |

**Tradeoff:** Citation syntax is validated deterministically against the frozen source map, but this does not independently prove that a cited source supports each claim. Structured JSON output remains opaque to the Markdown citation grammar. Invalid or mismatched markers terminate with `CITATION_INVALID`; the service does not invent references, retry synthesis, or revise an answer. This preserves [ADR-0064](0064-one-final-research-synthesis.md)'s one-final-synthesis contract.

## Constraints (arc42 §2)

| Constraint | Source | Impact |
|---|---|---|
| Reuse existing SearXNG instance | Infrastructure decision (ADR-0013) | Answer latency includes SearXNG search time. If SearXNG is degraded (<50% engines responding), answer latency exceeds targets. |
| Reuse existing scraper-svc | Infrastructure decision | Answer quality depends on scraper extracting usable markdown. Block pages, JS-rendered content, and paywalled sources produce empty context → graceful degradation path. |
| LLM backend is user-configured | Product decision | Citation format compliance varies by model. Models fine-tuned for instruction-following (DeepSeek, GPT-4o, Claude) reliably produce `[N]` citations. Smaller/quantized models may not. |
| No new dependencies | Project policy | The endpoint must not add packages or services. Search, scrape, and LLM calls use existing clients. |
| Synchronous HTTP timeout | Platform constraint | Long-running answer requests (>30s) may hit reverse proxy or load balancer timeouts. The 1-3s target is designed to stay well within typical 30s-60s gateway timeouts. |

## Risks and Mitigations (arc42 §9)

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| LLM produces citations in wrong format, uses an unknown index, or supplies a mismatched compact URL | Medium | Request ends with a bounded citation validation error; no successful `done` event or memory admission | Validate incrementally against the fixed source map, emit `CITATION_INVALID` on invalid syntax, and do not retry or fabricate references. |
| Latency exceeds 5s due to slow LLM backend | Medium | Caller timeout or poor UX | The endpoint does not hard-fail on slow LLM responses. Streaming mode mitigates perceived latency. Recommendation: configure a fast LLM model for the answer endpoint vs a deep reasoning model for the agent. |
| Empty context after search + scrape | Low-Medium | No sources to ground answer | Graceful: returns "No relevant web pages found" with empty sources/citations and a `latency_ms` value. |
| Citation parsing mistakes Markdown examples, code, or ordinary links for source references | Low | A literal example could be normalized or rejected | The incremental parser preserves fenced/indented/inline code, escaped markers, images, and ordinary Markdown labels; sync and SSE paths share the same parser and final-text parity check. |
| SSE connection drops mid-stream | Low | Partial answer delivered | The SSE path does not support resumption. Callers must handle incomplete streams by retrying with `stream: false` (sync mode). The latency field in the `done` event indicates whether the full pipeline completed. |

## Glossary (arc42 §10)

| Term | Definition |
|---|---|
| **Grounded Q&A** | Question answering where every claim in the answer is supported by a specific source URL provided in the context, not by the LLM's parametric knowledge. |
| **Citation index** | A numeric marker `[N]` in the answer text that maps to the N-th source in the `sources` array. |
| **SSE (Server-Sent Events)** | HTTP protocol where the server pushes a stream of events as `data:` lines. Used here for real-time token delivery. |
| **Search → Scrape → LLM → Cite** | The four-stage answer pipeline: search the web, scrape top results, synthesize via LLM, extract citation markers. |
| **Graceful degradation** | The endpoint returns a clear "no information" response when search or scraping produces no usable content, rather than hallucinating or erroring. |

## Links

* Issue #61 — Feature request: POST /v2/answer
* PR #117 — Implementation
* PR #135 — Concurrent scraping and sources_pending SSE event (concurrency addendum to this ADR)
* ADR-0012 — Webhook delivery for async endpoints (documenting the pattern this endpoint deliberately diverges from)
* Exa Answer API — https://exa.ai/docs/reference/answer.md (inspiration for the design)

---

## Architecture Diagrams

### C4 Level 2 — Container View (Updated)

The agent-svc container now has two distinct processing modes for question-answering alongside the existing endpoints:

```mermaid
flowchart TB
    subgraph external["External Actors"]
        C[Caller / Agent]
    end

    subgraph agent["agent-svc — GroktoCrawl API"]
        direction TB
        API["FastAPI Router\nPOST endpoints"]

        subgraph processing["Processing Modes"]
            AGENT["Research Agent\nsearch→scrape→think→answer\nmulti-step, 5-15s"]
            ANSWER["Answer Pipeline\nsearch→scrape→LLM→cite\nsingle-turn, 1-3s"]
            EXTRACT["Extract Pipeline\nscrape→LLM→JSON\nstructured data"]
        end
    end

    subgraph infrastructure["Infrastructure Services"]
        SX[SearXNG\nWeb Search]
        SC[scraper-svc\nURL→Markdown]
        LLM[LLM Backend\nOpenAI-compatible]
    end

    C -->|"POST /v2/agent"| AGENT
    C -->|"POST /v2/answer"| ANSWER
    C -->|"POST /v2/extract"| EXTRACT
    C -->|"POST /v2/scrape, /v2/search, etc."| API

    AGENT -->|search| SX
    AGENT -->|scrape| SC
    AGENT -->|synthesize| LLM

    ANSWER -->|search| SX
    ANSWER -->|scrape| SC
    ANSWER -->|synthesize + cite| LLM

    EXTRACT -->|scrape| SC
    EXTRACT -->|extract| LLM
```

### C4 Level 3 — Component View: Answer Pipeline

```mermaid
flowchart LR
    subgraph answer["Answer Pipeline (inside agent-svc)"]
        ROUTE["answer() route handler\napi.py"]
        SEARCH["SearXNGClient.search()\nsearxng_client.py"]
        SCRAPE["ScraperClient.scrape()\nscraper_client.py"]
        CONTEXT["Context Builder\nnumber sources + excerpts"]
        LLM_CLIENT["LLMClient.generate()\nLLMClient.generate_stream()\nllm.py"]
        CITE["Incremental citation normalizer\nfixed source map + validation"]

        ROUTE --> SEARCH
        SEARCH --> SCRAPE
        SCRAPE --> CONTEXT
        CONTEXT --> LLM_CLIENT
        LLM_CLIENT --> CITE
        CITE --> ROUTE
    end

    subgraph models["Data Models (models.py)"]
        REQ["AnswerRequest\nquery, num_sources,\nmodel, stream"]
        RES["AnswerResponse\nanswer, sources,\ncitations, latency_ms"]
        SRC["Source\nurl, title, relevance"]
        CIT["Citation\nindex, url"]
    end

    ROUTE -->|"validates"| REQ
    ROUTE -->|"returns"| RES
    RES -->|"sources[]"| SRC
    RES -->|"citations[]"| CIT

    subgraph streaming["SSE Streaming Path"]
        SSE["StreamingResponse\nServer-Sent Events"]
        EVT_PEND["event: sources_pending\\nsource URLs + titles\\nimmediate, before scrape"]
        EVT_SRC["event: sources\\nscraped sources with content"]
        EVT_TOK["event: token\\nper-token content"]
        EVT_DONE["event: done\\nfull answer + citations"]

        SSE --> EVT_PEND
        SSE --> EVT_SRC
        SSE --> EVT_TOK
        SSE --> EVT_DONE
    end

    ROUTE -->|"stream: true"| SSE
```
