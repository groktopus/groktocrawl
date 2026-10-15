# Browser process-capacity health

The experimental browser service retains `status` and `active_sessions` on
`GET /health` and adds sanitized `process_capacity` diagnostics. It reads two
small cgroup controller values; it never launches a browser or enumerates process
commands on a health poll.

With fewer than 16 available tasks under a finite container PID limit, health
returns HTTP 503, `status: degraded`, and `reason: process_capacity_low`.
The reserve is a conservative pressure signal for a Playwright driver and
Chromium session, not a guarantee that session creation will succeed. Existing
HTTP-based Docker health checks now detect this failure without parsing JSON.
The agent dependency probe uses `/health` as well, so aggregate API health
reports the browser as degraded rather than treating its root 404 as healthy.

Ordinary finite budgets and unlimited container budgets return HTTP 200. Missing,
unreadable, or invalid controller data return HTTP 200 with capacity explicitly
`unknown` and `reason: process_budget_unavailable`; the service does not infer
resource readiness from absent evidence. Both cgroup v2 and conventional v1 PID
controller mounts are supported. The scope is the visible container cgroup;
hidden ancestor limits and host-wide process, memory, or file-descriptor limits
are not measured. No private paths, process command lines, credentials, or
endpoint values appear in the diagnostic response.

A low budget warrants inspecting task accumulation and session cleanup before
recreating the affected browser service. An init reaper remains necessary for
orphan cleanup. This diagnostic does not establish long-term memory stability.
