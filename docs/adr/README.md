# Architecture Decision Records

This directory contains Architecture Decision Records (ADRs) for GroktoCrawl.

## What is an ADR?

An Architecture Decision Record captures an important architectural decision made along with its context and consequences. ADRs are immutable — existing records are never edited. If a decision changes, a new ADR is created and the old one is marked as superseded.

## Convention

- **Location:** `docs/adr/`
- **Naming:** `NNNN-title-with-dashes.md` (sequential numbers, imperative verb phrase)
- **Template:** [MADR](https://github.com/architecture-decision-record/architecture-decision-record/tree/main/locales/en/templates/decision-record-template-of-the-madr-project) — structured with Status, Deciders, Date, Context, Decision Drivers, Considered Options, Outcome, and Links
- **Immutability:** ADRs are immutable. To change a decision, write a new ADR and update the old one's status to `superseded by ADR-NNNN`.
- **Linking:** ADRs reference each other via relative links (`[ADR-0001](0001-adapter-registry-pre-pipeline-hook.md)`)
- **Statuses:** `proposed`, `accepted`, `rejected`, `deprecated`, `superseded by ADR-NNNN`

## Index

**Status legend:** accepted ADRs describe decisions used by the current implementation. Proposed ADRs are design work, not promises of current behavior. Superseded ADRs are historical context only; use their successor when documenting current behavior.

**Current accepted decisions:** ADR-0001–0012, 0014–0022, 0026, 0029–0035, 0038, 0039, 0043, and 0044.

**Proposed work:** ADR-0023–0025, 0027, 0028, 0036, 0037, and 0040–0042.

| ADR | Title | Status |
|-----|-------|--------|
| 0001 | [Adapter Registry Pre-Pipeline Hook](0001-adapter-registry-pre-pipeline-hook.md) | accepted |
| 0002 | [Regex Dispatch with Priority](0002-regex-dispatch-with-priority.md) | accepted |
| 0003 | [Per-Adapter Fallback Chain](0003-per-adapter-fallback-chain.md) | accepted |
| 0004 | [Two-Phase Result (Markdown + Metadata)](0004-two-phase-result-markdown-and-metadata.md) | accepted |
| 0005 | [In-Repo Adapters with Entry-Point Path Reserved](0005-in-repo-adapters-with-entry-point-path-reserved.md) | accepted |
| 0006 | [Auto-Registration via @adapter Decorator](0006-auto-registration-via-adapter-decorator.md) | accepted |
| 0007 | [Adapter Timeout and Circuit Breaker](0007-adapter-timeout-and-circuit-breaker.md) | accepted |
| 0008 | [Three-Layer Testing Strategy](0008-three-layer-testing-strategy.md) | accepted |
| 0009 | [Zero CLI Surface Changes](0009-zero-cli-surface-changes.md) | accepted |
| 0010 | [Five-Tier Scraper Pipeline with LLM Recovery](0010-five-tier-scraper-with-llm-recovery.md) | accepted |
| 0011 | [Stealth Playwright Configuration](0011-stealth-playwright-configuration.md) | accepted |
| 0012 | [Webhook Delivery for Async Endpoints](0012-webhook-delivery-for-async-endpoints.md) | accepted |
| 0013 | [Search Architecture with Vertical Categories](0013-search-architecture-with-vertical-categories.md) | superseded by ADR-0043 |
| 0014 | [Binary Content Detection and Download](0014-binary-content-detection-and-download.md) | accepted |
| 0015 | [Barrier Classification Phase 1](0015-barrier-classification.md) | accepted |
| 0016 | [Extraction Quality Gates](0016-extraction-quality-gates.md) | accepted |
| 0017 | [Grounded Q&A Endpoint](0017-grounded-qa-endpoint.md) | accepted |
| 0018 | [Observability Infrastructure](0018-observability-infrastructure.md) | accepted |
| 0019 | [Intelligent Scrape Cache](0019-intelligent-scrape-cache.md) | accepted |
| 0020 | [Proxy Support with Guardrails](0020-proxy-support-with-guardrails.md) | accepted |
| 0021 | [Web Portal](0021-web-portal.md) | accepted |
| 0022 | [Agent SSE Streaming](0022-agent-sse-streaming.md) | accepted |
| 0023 | [Search Type Spectrum — Fast and Rich](0023-search-type-spectrum-fast-and-rich.md) | proposed |
| 0024 | [Artifact Pyramid CLI Output](0024-artifact-pyramid-cli-output.md) | proposed |
| 0025 | [Semantic Search Pipeline — Embedding-Based Retrieval](0025-semantic-search-pipeline.md) | proposed |
| 0026 | [Phase 2 Semantic Search — Persistent Vector Index](0026-phase2-vector-index.md) | accepted |
| 0027 | [Smarter Index Retention — Domain TTLs, Frequency Weighting, Access Boosting](0027-smarter-index-retention.md) | proposed |
| 0028 | [Embedding Model Migration Path for Index Rebuilds](0028-embedding-model-migration-path.md) | proposed |
| 0029 | [Service-Level Metrics for semantic-svc](0029-service-level-metrics-for-semantic-svc.md) | accepted |
| 0030 | [Batch Vector Ingestion via Qdrant gRPC](0030-batch-vector-ingestion.md) | accepted |
| 0031 | [Centralized Settings Object](0031-settings-object.md) | accepted |
| 0032 | [Standardized Error Response Model](0032-standardized-error-response-model.md) | accepted |
| 0033 | [Search Volume Controls](0033-search-volume-controls.md) | accepted |
| 0034 | [Lifespan-Based Model Loading and Startup Readiness](0034-lifespan-model-loading.md) | accepted |
| 0035 | [Graceful Shutdown for Fire-and-Forget Tasks](0035-graceful-shutdown.md) | accepted |
| 0036 | [Split Scraper fetch.py into Focused Modules](0036-split-scraper-fetch-modules.md) | proposed |
| 0037 | [Split Semantic Service app.py into Focused Modules](0037-split-semantic-svc-app-modules.md) | proposed |
| 0038 | [Crawl Engine — BFS Orchestrator with Shared Link Extraction](0038-crawl-engine.md) | accepted |
| 0039 | [API-CLI Surface Must Ship Together](0039-api-cli-surface-ship-together.md) | accepted |
| 0040 | [Session Protocol — Agent-Native Research Sessions](0040-session-protocol.md) | proposed |
| 0041 | [Research Memory — Cross-Session Semantic Cache](0041-research-memory.md) | proposed |
| 0042 | [MCP Server Architecture](0042-mcp-server-architecture.md) | proposed |
| 0043 | [Migration from SearXNG to SlopSearX](0043-migration-to-slopsearx.md) | accepted |
| 0044 | [Autonomous CAPTCHA Recovery](0044-autonomous-captcha-recovery.md) | accepted |

See [CONTRIBUTING.md](../../CONTRIBUTING.md) for the full ADR workflow: when to write an ADR, how to number it, and how to get it reviewed in a PR.
