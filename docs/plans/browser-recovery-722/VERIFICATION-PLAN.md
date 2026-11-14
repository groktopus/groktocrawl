# Verification plan

Baseline: live unhealthy 503 with zero active sessions/one quarantine; synthetic
single-close exception returns False; new recovery import test fails before
implementation. Deadline regression must fail against original caller code.

Component: lifecycle timeout/exception redaction; one recycle task; grace not
reset; shutdown cancel/no reschedule; host/disabled guard; subprocess exit 75.
Integration: actual dedicated container with init/restart, injected diagnostic
quarantine outside production; verify replacement starts and old PID descendants
cannot survive. No source traffic or persistent data involved.
Regression: lifecycle bounds, browser process/capacity/control tests, scraper
fallback; lint/types plus repository required CI runtime/quality gates.
Production: merged revision labels, healthy endpoints, controlled known-safe
page create/navigate/content/delete, zero active/reserved after cleanup. Observe
quarantine/restart counters. Preserve MCP service from independent PR 721.
