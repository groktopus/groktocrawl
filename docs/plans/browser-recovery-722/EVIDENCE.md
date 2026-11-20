# Delivery evidence

Scope: issue #722; full path; owner-authorized issue/ADR/implementation/PR/merge/
production deployment. Skills: neckbeard, systematic-debugging, adr-authoring,
verification-methodology. Historical stage at original recording: PR readiness. Updated source status and deployment residuals follow below.

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

Original pre-merge gates (merge status superseded below): exact-head independent review, published platform review,
required CI, authorized merge, pinned targeted deploy and production lifecycle
smoke. No production completion claim until these pass.

Reusable lesson: caller deadlines must include inner operation budgets; an
unhealthy marker is not a restart strategy. Documented in ADR/runbook.

Independent review approved code head3b1ed019 with29 tests rerun and no blockers.
Live production topology verified: Docker marker, private PID namespace, init
PID1, single uvicorn PID7 with parent1, unless-stopped policy. Mypy passes for
recovery/lifecycle modules. ADR acceptance update is documentation-only; code
review remains applicable. Protected renderer topology is not opted in.

## Source-status reconciliation (November 16, 2026)

[#723](https://github.com/groktopus/groktocrawl/pull/723) merged at `0137e256383cca3a5202aa203b7ea47552859fb2`; embedded lifecycle hardening [#726](https://github.com/groktopus/groktocrawl/pull/726) merged at `28e5b58e28dcf30e6b50ffb79d0f4a46b49701af`. Browser HTTP-error rejection [#732](https://github.com/groktopus/groktocrawl/pull/732) and full-page repair [#733](https://github.com/groktopus/groktocrawl/pull/733) merged at `19aace2085879efd2e86fd97047a4ec0334230db` and `91e7a0ce17d14d7d234337c1d3e50b6c9db80976`. Source main at this November 16 snapshot was the latter; release v1.0.1 and its historically verified runtime `729d34d360d819b29a633389e0067e34b2dfe7ce` are separate identities.

Existing delivery owners retain exact image/topology, normal lifecycle, isolated container restart/descendant cleanup, rollback and rollout receipts. Representative NIST title/date, cold/warm cache, no-HTML limitation and browser failure downstream-exclusion evidence remain outstanding here. Merge or the historical isolated process-boundary probe closes none of these user-level checks. Current running images/configuration and private receipts were not inspected; no deployment or production-fault probe occurred in this reconciliation. MAG-48 assigns bounded engineering work; this update neither duplicates repairs nor declares delivery complete. See the [current roadmap](../../roadmap.md).
