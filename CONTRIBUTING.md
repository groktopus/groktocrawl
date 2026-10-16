# Contributing to GroktoCrawl

Thanks for your interest! GroktoCrawl is MIT-licensed and contributions of all kinds are welcome.

## Code of Conduct

Be excellent to each other. This project is small but aims to be a welcoming space for contributors of all experience levels.

## Contribution intake and triage

Issue filing on this repository is **open** — anyone can create an issue
directly, with no invitation or blank-issue gate. The issue templates exist to
keep reports structured, not to restrict who can file. Pick the template that
matches what you want to do and use the route below.

| What you want to do | Route |
|---------------------|-------|
| Report a bug | Open a [bug report](.github/ISSUE_TEMPLATE/bug_report.md) |
| Propose a feature | Open a [feature request](.github/ISSUE_TEMPLATE/feature_request.md) |
| Signal implementation interest | Comment "I'd like to work on this" on an existing issue, or open a feature request stating what you intend to build and reference the [roadmap](docs/roadmap.md) |

**Triage is best-effort, with no SLA.** A small maintainer team runs this
project and responds as time allows. We do **not** promise a response or
resolution time. Issues are scoped, labelled, and prioritized as the backlog
allows; the [roadmap](docs/roadmap.md) shows the current **Now / Next / Later**
priorities and how they relate to the open backlog.

What makes triage and prioritization fastest:

- A concrete, reproducible bug — steps, `docker compose logs` output, and a
  redacted `.env` (no real API keys)
- A feature proposal framed as a user problem and use case, not just a desired
  flag or endpoint
- A clear statement of what you're willing to build, test, or investigate
  yourself

## How to Contribute

### Reporting Bugs

Open a [bug report](.github/ISSUE_TEMPLATE/bug_report.md) with:
- A clear description of the bug
- Steps to reproduce
- The output of `docker compose logs` for the affected service
- Your `.env` file (redact API keys)

### Suggesting Features

Open a [feature request](.github/ISSUE_TEMPLATE/feature_request.md) with:
- What you want to accomplish
- Why it doesn't fit as a post-MVP improvement
- A sketch of the API or behavior change (optional but helpful)

### Signaling Implementation Interest

If an open issue or a [roadmap](docs/roadmap.md) item matches something you want to
build, say so on the issue thread before starting large work: "I'd like to work
on this." For brand-new ideas, open a feature request and state your intent to
implement. Coordination up front avoids two people building the same thing and
lets maintainers flag scope or design concerns early.

### Pull Requests

1. Fork the repo
2. Create a branch: `git checkout -b feat/your-feature` or `fix/your-bug`
3. Make your changes
4. Run the relevant checks and tests (see below)
5. Commit with a clear message
6. Open a PR

### Merging to `main`

The `main` branch is protected by repository rulesets (see
[ADR-0046](docs/adr/0046-enforce-qa-checks-and-review-policy-on-main.md)):

- Merges require the **Code Quality Gate** and **Runtime Gate** checks to pass
  (branches must be up to date before merging).
- Non-automation changes require **at least one approving review**; stale
  approvals are dismissed on push and open review conversations block merge.
- `dependabot[bot]` is exempt from the review requirement only — it must
  still pass the required checks. The sole maintainer can merge their own
  PRs without an approving review (review bypass only — required checks
  still bind). Release-please PRs require a human approving review
  (`github-actions[bot]` cannot be a ruleset bypass actor; see ADR-0046).
- The policy is enforced on admins; the audited emergency exception path is
  documented in
  [Emergency Branch Protection Bypass](docs/runbooks/emergency-branch-protection-bypass.md).

### Running Tests

```bash
# Fast Docker-free unit and service tests (Python 3.12 with uv):
uv sync --locked --no-dev --group fast-tests
PYTHONPATH=agent-svc:scraper-svc:llm-svc:parse-svc:portal-svc:browser-svc:semantic-svc:. \
  uv run --no-sync pytest tests/unit/ tests/service/ --no-cov

# From the repo root (fixture services provide an LLM and test sites):
cp .env.sample .env
docker compose --profile fixture up --build -d
docker compose exec -T agent-svc python3 -m pytest /app/tests/integration/ /app/tests/service/

# Fast checks that do not require the stack:
python3 scripts/check-cli-coverage.py
python3 scripts/check-docs-surface.py
```

The fast test suite is Docker-free and covers only `tests/unit/` and `tests/service/`.
The Docker command above remains the integration-test path.

All tests must pass before a PR is merged.

### Durable research acceptance

`tests/service/test_live_research_recovery.py` exercises operation reuse, ambiguous-dispatch refusal, persistent failures and lease/cancel fencing with isolated Valkey. `tests/integration/test_live_research_http_adapters.py` additionally requires an isolated PostgreSQL schema16 database and runs actual HTTP search/capture/model digital twins. The runtime workflow provisions the durable profile and runs this test mandatorily. These twins require no provider credentials. Component tests alone do not establish objective-dependent retained research; changes to the durable adapter must preserve this full HTTP-to-retained-byte test and its recovery checks. Never point acceptance tests at a production database.

## Development Setup

You need Docker and Docker Compose. No other dependencies are required — everything runs in containers.

```bash
git clone https://github.com/your-username/groktocrawl
cd groktocrawl
cp .env.sample .env
docker compose --profile fixture up --build -d

# Verify health
curl http://localhost:8080/health
```

The `--profile fixture` flag starts test helper services (`llm-svc` for a built-in LLM, `test-site` for integration tests). For production you'd omit it and configure a real LLM in `.env`.

## Coding Conventions

- **Python 3.12+** with type hints
- **FastAPI** for all HTTP services
- **Async/await** throughout; background jobs run in the API process through `TaskTracker` and `asyncio.create_task()`.
- **MIT license** — all contributions are under this license
- Keep dependencies minimal. Each service's `pyproject.toml` should list only what it needs.
- **Webhook support required for all async endpoints** — any new endpoint that returns a job ID must accept a `webhook` field in its request and fire it on completion/failure via `deliver_webhook()` in `agent/webhook.py`. This ensures all async jobs are observable.

## Project Layout

- `agent-svc/` — the main API service (FastAPI + research worker)
- `scraper-svc/` — URL-to-markdown conversion service (three-tier fetch: llms.txt → content-negotiation → Playwright)
- `browser-svc/` — headless Playwright browser sessions
- `semantic-svc/` — vector indexing and near-duplicate detection (Qdrant or PostgreSQL/pgvector)
- `portal-svc/` — web UI for human users
- `llm-svc/` — LLM fixture for local testing (replaceable with any OpenAI-compatible backend)
- `test-site/` — fixture website for integration tests
- `tests/` — unit, service, and integration tests

### Vector storage and migrations

Qdrant remains the default vector store. Research-memory artifacts and their
expiry metadata remain in Valkey; `RESEARCH_MEMORY_VECTOR_MODE` selects the
vector projection (`qdrant`, `shadow_pgvector`, or `pgvector`). The semantic
page index uses `VECTOR_STORE_MODE`; `pgvector_only` serves from PostgreSQL
without Qdrant readiness checks or writes. These are separate stores and
backfills, even though they may share the PostgreSQL service.

PostgreSQL modes require the `research-postgres` service and its pgvector
extension. In Compose, the `durable-research` profile enables PostgreSQL for
research-memory migration and keeps Qdrant available; `pgvector-only` enables
PostgreSQL plus `semantic-svc` without activating Qdrant. Do not enable the
`indexing` or `durable-research` profile when you intend to run the semantic
service without Qdrant.

Before changing a deployed mode, take and verify backups of the active Valkey,
Qdrant, and PostgreSQL data. Initialize the destination schema, run the
idempotent backfill, and require exact active-ID and metadata parity plus
vector parity within the documented tolerance before switching reads. Keep
the source data intact through the rollback window. The
[research-memory migration runbook](docs/runbooks/research-memory-pgvector.md)
contains the copy, verification, and reverse-copy commands; in particular,
semantic restore preserves unmatched Qdrant points unless the operator
explicitly requests `--prune-stale` after verifying a backup. Storage changes
must add or update tests for migration idempotency, missing-backend behavior,
and rollback, and record architectural changes in the [ADR index](docs/adr/README.md).

## Error Handling Conventions

All API endpoints return errors in a consistent format:

```json
{
  "success": false,
  "error": "Human-readable description",
  "error_code": "NOT_FOUND",
  "details": null
}
```

### Error Codes

| HTTP | Error Code | When |
|------|-----------|------|
| 400/422 | `INVALID_REQUEST` | Validation errors, missing fields |
| 401/403 | `AUTH_ERROR` | Authentication or authorization failure |
| 404 | `NOT_FOUND` | Resource (job, monitor, session) not found |
| 429 | `RATE_LIMITED` | Rate limit exceeded |
| 502 | `SCRAPE_FAILED` | Scraper service failure |
| 502 | `BROWSER_ERROR` | Browser service failure |
| 502 | `UPSTREAM_ERROR` | Generic upstream service failure |
| 500 | `INTERNAL_ERROR` | Unhandled exceptions (traceback logged) |

### Raising Errors

Use the exception hierarchy from `agent-svc/agent/exceptions.py` (or `scraper-svc/scraper/exceptions.py`):

```python
from agent.exceptions import NotFoundError, InvalidRequestError, ScrapeError

# Resource not found
raise NotFoundError(detail="Job not found", details={"job_id": "abc"})

# Invalid input
raise InvalidRequestError(detail="URL is required")

# Upstream failure
raise ScrapeError(detail="Failed to scrape URL")
```

### Rules

- Do NOT return 200 with `success: false` — raise an appropriate exception instead
- Do NOT catch broad `Exception` and return a degraded 200 — let exceptions propagate to the handler
- Stack traces are automatically logged by the exception handler — do not log + re-raise
- For fire-and-forget background tasks, silent `except Exception: pass` is acceptable (the error is logged where the task was spawned)
- FastAPI exception handlers in `app.py` convert all exceptions to the standard error response shape automatically

Significant architectural decisions are documented as ADRs in `docs/adr/`. Each ADR follows the MADR template and covers context, decision drivers, considered options, and consequences.

**Convention:**

- **File name:** `NNNN-title-with-dashes.md` (sequential numbers, imperative verb phrase)
- **Statuses:** `proposed`, `accepted`, `rejected`, `deprecated`, `superseded by ADR-NNNN`
- **Immutability:** ADRs are never edited after acceptance. To change a decision, write a new ADR and update the old one's status.
- **Linking:** Reference related ADRs via relative links in the Links section.

**When to write an ADR:**

- Adding a new integration or service
- Changing an existing architectural pattern
- Choosing between significant alternatives with lasting impact
- Any decision a future contributor would want to understand *why* it was made

**Workflow:**

1. Create the ADR as `docs/adr/NNNN-title-with-dashes.md` (next available number)
2. Get it reviewed as part of the PR
3. On acceptance, update the ADR status and the table in `docs/adr/README.md`

See `docs/adr/README.md` for the full index of existing ADRs.

## Documentation updates

Keep the README as onboarding material and update the relevant guide in `docs/guides/` for public behavior changes. The validated inventory in `docs/reference/public-surface.md` must change with any public route, top-level CLI command, compose service, or `.env.sample` setting. `scripts/check-docs-surface.py` is the local and CI guardrail; do not add broad exemptions for public behavior.

## Commit Guidelines

This project uses **Conventional Commits**:

```
<type>: <short description>

<longer explanation if needed>
```

Types: `feat`, `fix`, `refactor`, `docs`, `test`, `ci`, `chore`, `perf`, `style`

Branch names should match the commit type: `feat/add-widget`, `fix/login-timeout`.

### Sign-Off (DCO)

Every commit must include a `Signed-off-by` trailer, certifying that you have the right to contribute the code under the MIT License:

```bash
git commit -s -m "feat: add widget"
```

This is a [Developer Certificate of Origin](https://developercertificate.org/) requirement. It is legally simpler than a CLA.

## PR Template

A pull request template is available at `.github/PULL_REQUEST_TEMPLATE.md`. Fill it out completely when opening a PR.

## License

By contributing, you agree that your contributions will be licensed under the MIT License.


## Experimental research changes

Research-substrate changes in `agent-svc/agent/experimental/` must preserve the existing public interfaces and behavior by default. Read [ADR-0069](docs/adr/0069-adopt-bounded-research-substrate.md) before changing execution, evidence, knowledge, or artifact persistence. Document feature gates and schema versions, include explicit migration and rollback behavior when state changes, and keep experimental capabilities clearly labeled until their separate promotion checks are complete. The [GroktoCrawl X ADR archive](docs/adr/experimental/groktocrawl-x/README.md) preserves historical decisions; use the mainline ADR index for current decisions.
