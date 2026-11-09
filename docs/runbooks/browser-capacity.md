# Browser capacity and collection

**Owner:** GroktoCrawl maintainers. **Status:** offline instrumentation/dashboard
candidate; production deployment and live query/render verification are pending.
This is a USE-style capacity investigation view, not an SLO or paging policy.
It helps distinguish task exhaustion, occupied session ownership, failed cleanup,
and missing telemetry before choosing a separately authorized intervention.

## Collection contract

Browser-service metrics are relayed through the agent's existing `/metrics`
endpoint. No additional public browser port or Prometheus scrape is required.
Metrics use the `groktocrawl_browser_` prefix. The relay's
`metrics_collection_success` and `metrics_last_success_timestamp_seconds` describe
collection, not browser readiness. Failed relay collection does not re-emit stale
browser samples. Prometheus history remains available, but current missing values
must not be filled with zero. Inspect agent `up` independently.

- `cgroup_tasks` counts tasks (including threads). `cgroup_task_limit` exists only
  for a known finite limit. Absent does not distinguish unlimited from unreadable;
  inspect `cgroup_collection_success` and the actual cgroup configuration.
- Launch boundary = task limit minus `minimum_task_headroom`; remaining headroom
  = task limit minus tasks. The configured launch guard is not an alert threshold.
- `owners{phase}` tracks creating, active, closing, and quarantined ownership.
  `reserved_sessions`, `active_sessions`, and `session_limit` show different
  capacity boundaries. Cleanup can retain ownership after the active phase ends.
- `admission_available` is a sampled 1/0 decision, not a guarantee that a later
  request will launch or that the end-to-end user journey is healthy.
- `admission_rejections_total{reason}` uses shutting_down, cleanup_incomplete,
  creation_in_progress, session_capacity_reached, and process_capacity_low.
- `creation_failures_total{reason}` uses timeout, error, and cancelled.
  `cleanup_incomplete_total` counts failed-cleanup events, not current owners.
- `cgroup_limit_events_total` is the raw kernel `pids.events` max counter, not
  browser rejection count. Its availability has its own
  `cgroup_limit_events_collection_success` signal.
- `service_start_time_seconds` changes on browser process restart. A process
  restart need not recreate its cgroup; a cgroup counter reset has a different
  lifetime. Scrape gaps can hide both restart and reset transitions.

Counters are displayed as `increase(...[$__rate_interval])`, not totals since
startup or request error ratios. Extrapolation may produce fractional increments.
The row also shows `changes(service_start_time_seconds[...])` and
`resets(cgroup_limit_events_total[...])` for context. These are observed changes,
not a complete event log. No data/insufficient samples must remain distinguishable
from zero increments. Collection age is `time() - (last_success_timestamp > 0)`;
zero/uninitialized timestamps leave a gap. No freshness cutoff is invented.

## Prepare an additive dashboard candidate

Use the existing dashboard's authoritative provisioned source, not a UI export
that might disagree with its owner. Deployment-specific source paths, target
labels, datasource UIDs and generated JSON stay in the private deployment overlay.
The repository deliberately contains a portable renderer rather than a second
owner for a production dashboard UID.

```sh
python scripts/extend_browser_capacity_dashboard.py \
  "$PRIVATE_SOURCE" "$PRIVATE_CANDIDATE" \
  --datasource-uid "$PROMETHEUS_UID" --job "$AGENT_JOB" --instance "$AGENT_INSTANCE"
```

The renderer only reads/writes local files. It preserves UID, settings, variables,
time/refresh defaults and every existing panel. It appends one row and nine panels,
uses exact target labels, refuses duplicate rows and existing output paths, and
adds no alerts, thresholds or provisioning/scrape changes. The source and output
must differ. Inspect the diff and retained datasource's scrape interval so Grafana's
`$__rate_interval` covers enough samples. Do not alter cadence just for this view.

Before applying, establish the source repository/revision, provider watched path,
actual mount/synchronization mechanism, org/folder, datasource UID, polling cycle,
edit/delete behavior, and one owner per UID. A host staging file may not be the
watched file when Grafana uses a named volume. A generated candidate is not a
reconciled dashboard. Do not import it as a second dashboard with the same UID.
Any merge, deployment, provider synchronization or reload requires separate
approval. Preserve a private copy/checksum of the actual current provider file;
rollback is restoration through that same owner, not editing Grafana's database.

## Verification gates

Offline structural checks:

```sh
uv run --no-sync pytest tests/unit/test_browser_capacity_dashboard.py -n 0 --no-cov
```

These verify source preservation, unique panel/target IDs, exact target scope,
null behavior, reset context and absence of invented thresholds. They do not
validate PromQL against a running backend or prove Grafana rendering.

After separately approved instrumentation and dashboard deployment:

1. Confirm the existing agent target is up and actual job/instance labels match.
   Query every new metric family, checking values, label bounds and latest sample
   timestamps. Record deployment time; do not imply historical reconstruction.
2. Run every rendered expression in the actual Prometheus datasource over a
   recent range containing enough scrapes. Confirm response warnings/errors,
   cardinality, units and null behavior; distinguish no events from absent data.
3. Compare tasks, finite limit and launch boundary with read-only cgroup evidence.
   Confirm the configured headroom rather than assuming a universal constant.
   Compare session phases with read-only service state.
4. Verify success/failed collection, missing/unlimited limit, counter resets and
   no-traffic behavior using isolated fixtures or an approved test environment.
   Do not induce production exhaustion, shutdown, cleanup failure or restarts
   merely to make a panel move. Mark unavailable scenarios unverified.
5. Confirm provider reconciliation on more than one configured polling cycle:
   same UID and pre-existing panels, correct appended row, no duplicate-ownership
   warnings, correct datasource and no provisioning drift. Test readability,
   legends and gaps in Grafana; preserve existing refresh/time settings.
6. Record queries, time ranges, source revision/checksum, Grafana version and
   results. Keep live query/render, access and restart/persistence checks pending
   until actually exercised. Healthy metrics alone do not establish user recovery.

## Investigation, not automatic remediation

First distinguish stale/absent collection from genuine pressure. Then correlate
headroom and cgroup hits with admission rejections, owner phases and cleanup
failures. Read bounded service logs without exposing URLs, tokens or user content.
Do not raise limits, restart services or forcibly discard quarantined ownership
based solely on these graphs. Agree on the specific mitigation, rollback and
user-facing verification with the service owner before any production mutation.
