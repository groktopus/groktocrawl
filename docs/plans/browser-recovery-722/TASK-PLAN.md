# Task plan

- Delivery owner: diagnose production, file 722, preserve evidence and uncertainty.
- Implementation owner: align budgets, add safe lifecycle diagnostics and guarded
  single-flight recycler; update Compose, ADR and runbook.
- Independent QA: review design/test plan and exact final diff, verify shutdown
  fencing, resource ownership and diagnostics do not expose secrets.
- Delivery owner: run focused regressions and broad gates, address one published
  platform review, merge normally only when required checks pass.
- Release owner: coordinate with PR 721, pin merged browser/scraper images,
  preserve Compose preimage/rollback, verify container recovery in isolation and
  normal lifecycle in production. Report gaps, never infer success from restart.
