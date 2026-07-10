# Contributing to GroktoCrawl

Thanks for your interest! GroktoCrawl is MIT-licensed and contributions of all kinds are welcome.

## Code of Conduct

Be excellent to each other. This project is small but aims to be a welcoming space for contributors of all experience levels.

## How to Contribute

### Reporting Bugs

Open a GitHub issue with:
- A clear description of the bug
- Steps to reproduce
- The output of `docker compose logs` for the affected service
- Your `.env` file (redact API keys)

### Suggesting Features

Open a GitHub issue with:
- What you want to accomplish
- Why it doesn't fit as a post-MVP improvement
- A sketch of the API or behavior change (optional but helpful)

### Pull Requests

1. Fork the repo
2. Create a branch: `git checkout -b feat/your-feature` or `fix/your-bug`
3. Make your changes
4. Run the relevant checks and tests (see below)
5. Commit with a clear message
6. Open a PR

### Running Tests

```bash
# From the repo root (fixture services provide an LLM and test sites):
cp .env.sample .env
docker compose --profile fixture up --build -d
docker compose exec -T agent-svc python3 -m pytest /app/tests/integration/ /app/tests/service/

# Fast checks that do not require the stack:
python3 scripts/check-cli-coverage.py
python3 scripts/check-docs-surface.py
```

All tests must pass before a PR is merged.

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
- `semantic-svc/` — vector indexing and near-duplicate detection (Qdrant)
- `portal-svc/` — web UI for human users
- `llm-svc/` — LLM fixture for local testing (replaceable with any OpenAI-compatible backend)
- `test-site/` — fixture website for integration tests
- `tests/` — unit, service, and integration tests

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
