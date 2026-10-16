# Browser process-capacity diagnostics — October 1

The browser health follow-up covers the observed PID-budget exhaustion that had
returned healthy status during the bounded resource study. It is separate from
long-term memory stability or research-quality qualification.

## Behavior

Health reads bounded PID-controller values without starting Playwright. A finite
budget with fewer than sixteen available task slots returns HTTP 503 and the
sanitized reason `process_capacity_low`. Healthy status and active-session fields
remain compatible; unavailable controller information is explicitly unknown.
The API dependency probe now calls the browser `/health` endpoint so the same
failure degrades aggregate API health. Visible container budget does not capture
hidden ancestor limits or host-wide resource exhaustion, and the reserve is a
pressure signal rather than guaranteed browser readiness.

## Verification

- 110 focused browser and dependency-health tests passed in strict asyncio mode.
- Focused changed-line coverage measured 30/30 executable lines, including the
  browser route and API dependency probe.
- Controlled fixtures cover ordinary/unlimited, low/exhausted/zero budgets,
  malformed/oversized/missing information, v1 fallback, and absence of browser
  launches during health polling.
- The first final-head CI attempt could not collect the new browser tests because
  its API-container test environment lacked the Playwright Python package.
  The corrected workflow installs that test-only package rather than skipping
  the regressions; the failed attempt remains in public history.

## Merged deployment and CI

[PR #404](https://github.com/magnus919/groktocrawl-x/pull/404) merged at
`68ece0cfb50684a5637185048d0db15049bb9d56` after all required checks passed.
[Final Runtime CI](https://github.com/magnus919/groktocrawl-x/actions/runs/36814300425)
passed 2,243 integration tests (218 skipped, 17 deselected), MCP transport tests,
storage/restore/capacity probes and the required runtime gate. CI itself measured
30/30 changed executable lines, confirming the corrected coverage binding from
[PR #402](https://github.com/magnus919/groktocrawl-x/pull/402).
The [initial collection failure](https://github.com/magnus919/groktocrawl-x/actions/runs/36813534577)
remains historical setup evidence rather than a passing test run.

The [sanitized live receipt](live-deployment.json) records the guarded update of
only the experimental browser and agent images plus the agent revision label.
The deployer required a clean checkout at the merged revision, no active/pending
jobs, no admission work and zero browser sessions. Rendered configuration was
compared before mutation, prior images/configuration retained for rollback, and
the deployed browser diagnostic and agent health source hashes matched that
checkout. A browser create/executeScript/delete cycle passed; final health was
`ok`, active sessions were zero, and the visible task budget was 7/256 (249 slots
available). All eleven services were healthy, init remained enabled, the model
alias remained `free`, and the semantic CPU quota remained fourteen.

Mainline deployment and Hermes configuration were unchanged. The current agent
and browser use the reviewed revision above; other service images were retained.
The completed W9 pilot used its earlier frozen revision and remains historical
bounded evidence. This repair does not create a new seven-day qualification or
an unconditional mainline replacement claim.
