# ADR-0037: Split Semantic Service app.py into Focused Modules

**Status:** proposed

**Deciders:** @magnus919

**Date:** 2026-06-16

## Context

`semantic-svc/app.py` is 1248 lines — the second-largest file in GroktoCrawl after the (now-split) fetch.py. It mixes:

- **Model lifecycle** (lines 95-165): lifespan handler, `_get_embed_model()`, `_get_rerank_model()`, `_get_active_model()`
- **Qdrant management** (lines 346-500): `_ensure_qdrant()`, `_evict_if_needed()`, `_run_backfill()`, hash utilities
- **Retention logic** (lines 231-317): domain category classification, retention scoring
- **Pydantic models** (lines 622-714): 16 request/response schemas inline in the route file
- **Middleware** (lines 768-793): metrics middleware
- **Endpoints**: embed, rerank, index (single + batch), vector search, index stats/model, migration (start/status/cutover), metrics

A file this large has the same costs as fetch.py: reviewer must understand 1200 lines for context on any single endpoint change, merge conflicts on unrelated sections, and high cognitive load for new contributors.

## Decision Drivers

1. **Reviewability:** a change to `/search/vector` should not require understanding `/index/migrate`
2. **Separation of concerns:** model lifecycle is infrastructure; retention is business logic; routes are API surface
3. **Minimal interface changes:** FastAPI's `APIRouter` supports clean extraction without changing URL paths or response schemas
4. **Existing ADR precedent:** ADR-0036 established the modular-split pattern for fetch.py

## Considered Options

### Option A: Extract routes only

Move endpoint handlers into router modules, keep models and infrastructure in app.py.

**Pros:** minimal diff, clear API delineation
**Cons:** doesn't split the largest section (models), leaves app.py as a catch-all

### Option B: Extract routes + models + retention (chosen)

1. Extract Pydantic models into `models.py` (~200 lines)
2. Extract index routes into `router_index.py` — `/index`, `/index/batch`, `/index/{url_hash}`, `/index/stats`, `/index/model`, `_build_index_payload()`, `_track_access()` (~350 lines)
3. Extract search routes into `router_search.py` — `/search/vector` (~120 lines)
4. Extract migration routes into `router_migration.py` — `/index/migrate/*`, `_run_backfill()` (~200 lines)
5. Extract retention logic into `retention.py` — `_compute_domain_category()`, `_compute_retention_score()`, `_evict_if_needed()` (~120 lines)
6. Keep in `app.py`: FastAPI app creation, `lifespan()`, `TaskTracker`, middleware, model helpers (`_get_embed_model`, `_get_rerank_model`, `_ensure_qdrant`), `/health`, `/embed`, `/rerank`, `/metrics` endpoints, router mounting (~350 lines)

**Pros:** each module <400 lines, clean domain separation, models reusable by tests
**Cons:** larger diff, router prefix wiring must be validated

### Option C: Extract models only

Move Pydantic schemas to `models.py`, leave everything else in `app.py`.

**Pros:** minimum structural risk, models become independently importable
**Cons:** doesn't address the core issue — routes and infrastructure still mixed

## Decision

**Option B** — Extract routes + models + retention into focused modules.

## Model Extraction Detail

All Pydantic models currently at lines 622-714 of app.py move to `semantic-svc/models.py`:

- `EmbedRequest`, `EmbedResponse`
- `RerankRequest`, `RerankResult`, `RerankResponse`
- `IndexRequest`, `IndexResponse`
- `IndexBatchRequest`, `IndexBatchResponse`
- `VectorSearchRequest`, `VectorSearchResult`, `VectorSearchResponse`
- `IndexStatsResponse`, `ModelInfoResponse`
- `MigrationStartRequest`, `MigrationStatusResponse`

Each router module imports only the models it needs.

## Module Boundaries

```
semantic-svc/
  app.py              (~350 lines) — FastAPI app, lifespan, TaskTracker, middleware,
                                     /health, /embed, /rerank, /metrics, router mounting
  models.py           (~200 lines) — all Pydantic request/response schemas
  router_index.py     (~350 lines) — /index, /index/batch, /index/{url_hash},
                                     /index/stats, /index/model, payload builder
  router_search.py    (~120 lines) — /search/vector, access tracking
  router_migration.py (~200 lines) — /index/migrate/*, backfill runner
  retention.py        (~120 lines) — domain categories, retention scoring, eviction
  metrics.py          (existing)    — unchanged
```

## Consequences

### Positive

- Each module <400 lines, reviewable in isolation
- Pydantic models are independently importable by tests and client code
- Adding a new index endpoint only touches `router_index.py`
- Retention policy changes only touch `retention.py`
- Follows the same architectural pattern as ADR-0036

### Negative

- Router prefix (`app.include_router(router_index, prefix="/index")`) requires URL path adjustments in route handlers
- All callers that import models from `app` must update to `models`
- Six import-path updates across test files

### Neutral

- All URL paths, response schemas, and HTTP status codes remain identical
- Metrics middleware is unaffected (registered on the app, not on individual routers)

## Links

- Issue #244: split semantic-svc/app.py into focused modules
- ADR-0036: Split Scraper fetch.py into Focused Modules (establishes the modular-split pattern)
- ADR-0029: Service-Level Metrics for semantic-svc (documented the existing metrics structure)
