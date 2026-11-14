# Delivery evidence

Scope: issue #722; full path; owner-authorized issue/ADR/implementation/PR/merge/
production deployment. Skills: neckbeard, systematic-debugging, adr-authoring,
verification-methodology. Current stage: review; not yet merged or deployed.

Discovery: live service had zero active sessions, one quarantined reservation,
152 cleanup-incomplete rejections. Source and timestamped caller/server logs
proved timeout ordering and permanent quarantine. Exact original cleanup stage
was not logged; historical memory/PID counters do not establish cause. Existing
quarantine tests intentionally retain uncertain owners. No claim of a proven
memory leak or spontaneous Chromium crash.

Design: ADR-0080; independent QA conditionally approved single-flight guarded
container exit with shutdown fencing and explicit interrupted-session semantics.
No security/network/resource-limit changes. Rollback: previous browser/scraper
images; disable recycle flag; preserve unrelated services.

Checks, November 14:
- New recovery test failed collection against pre-change code (module absent).
- New behavioral deadline regression failed against original caller: 30s is less
  than 45s+10s; candidate restored after bounded comparison.
- 161 focused lifecycle/recovery/browser/control/capacity tests pass on Python 3.13.
  Two initial sandbox-only DNS/UDS failures passed in authorized network context.
- Targeted Ruff and git diff --check pass.
- Isolated production-host Docker probe used existing browser base, new recovery
  module, no network, init, 128MiB/32 PID limits and on-failure:1. Scheduled short
  grace, exited 75, restarted exactly once, exited 75 again. Original observed init,
  Python and child sleep host PIDs no longer existed. Production containers were
  not altered. This verifies process-boundary recovery, not original Chromium
  teardown causality. Standard 30-second default remains unit-characterized.

Remaining gates: exact-head independent review, published platform review,
required CI, authorized merge, pinned targeted deploy and production lifecycle
smoke. No production completion claim until these pass.

Reusable lesson: caller deadlines must include inner operation budgets; an
unhealthy marker is not a restart strategy. Documented in ADR/runbook.
