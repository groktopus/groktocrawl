"""Render a local candidate by appending browser-capacity panels; never deploy it."""

import argparse
import copy
import json
from pathlib import Path

ROW_TITLE = "Browser capacity and collection"


def extend_dashboard(
    source: dict, *, datasource_uid: str, job: str, instance: str
) -> dict:
    """Preserve source settings/panels; select exactly one existing agent target."""
    if not all((datasource_uid, job, instance)):
        raise ValueError("datasource UID, job and instance must be nonempty")
    result = copy.deepcopy(source)
    panels = result["panels"]
    if any(panel.get("title") == ROW_TITLE for panel in panels):
        raise ValueError(
            "browser capacity row already exists; review rather than duplicate"
        )

    def walk(items):
        for item in items:
            yield item
            yield from walk(item.get("panels", []))

    existing = list(walk(panels))
    next_id = max((p.get("id", 0) for p in existing), default=0) + 1
    y = max((p["gridPos"]["y"] + p["gridPos"]["h"] for p in existing), default=0)
    selector = f"job={json.dumps(job)},instance={json.dumps(instance)}"
    datasource = {"type": "prometheus", "uid": datasource_uid}

    def metric(name):
        return f"groktocrawl_browser_{name}{{{selector}}}"

    def increase(name):
        return f"increase({metric(name)}[$__rate_interval])"

    panels.append(
        {
            "id": next_id,
            "type": "row",
            "title": ROW_TITLE,
            "collapsed": False,
            "panels": [],
            "gridPos": {"x": 0, "y": y, "w": 24, "h": 1},
        }
    )
    y += 1
    specifications = [
        (
            "Browser tasks, limit and launch boundary",
            "short",
            "Tasks include threads, not just processes. Launch boundary = finite cgroup task limit minus "
            "configured minimum task headroom. Unknown/unlimited limits produce gaps, never zero or infinity.",
            [
                (metric("cgroup_tasks"), "tasks"),
                (metric("cgroup_task_limit"), "task limit"),
                (
                    f"{metric('cgroup_task_limit')} - {metric('minimum_task_headroom')}",
                    "launch boundary",
                ),
            ],
        ),
        (
            "Browser task headroom",
            "short",
            "Remaining tasks = finite limit minus usage. Required launch headroom is a configured guard, "
            "not an SLO or alert threshold. Missing finite limits leave remaining headroom absent.",
            [
                (
                    f"{metric('cgroup_task_limit')} - {metric('cgroup_tasks')}",
                    "remaining task headroom",
                ),
                (metric("minimum_task_headroom"), "required launch headroom"),
            ],
        ),
        (
            "Browser session ownership",
            "short",
            "Owner phases include creating, active, closing and quarantined. Reserved capacity is separate "
            "from active sessions; closing/quarantined ownership can prevent new admission.",
            [
                (metric("owners"), "owners {{phase}}"),
                (metric("active_sessions"), "active sessions"),
                (metric("reserved_sessions"), "reserved sessions"),
                (metric("session_limit"), "session limit"),
            ],
        ),
        (
            "Browser admission and collection state",
            "short",
            "1 = available/success; 0 = unavailable/failure. Admission is a snapshot, not a promise "
            "of request success. Agent scrape health and browser relay/cgroup collection are distinct. "
            "No data is unknown, not healthy. No interpolated gaps.",
            [
                (metric("admission_available"), "admission available"),
                (
                    metric("metrics_collection_success"),
                    "browser relay collection success",
                ),
                (metric("cgroup_collection_success"), "cgroup task collection success"),
                (
                    metric("cgroup_limit_events_collection_success"),
                    "cgroup limit event collection success",
                ),
                (f"up{{{selector}}}", "agent scrape success"),
            ],
        ),
        (
            "Browser collection age",
            "s",
            "Seconds since the relay's last successful browser collection. No successful collection "
            "yet is a gap, not an enormous age from timestamp zero. Read alongside up and collection "
            "success; an absent agent scrape cannot provide a current age. No freshness cutoff is inferred.",
            [
                (
                    f"time() - ({metric('metrics_last_success_timestamp_seconds')} > 0)",
                    "last success age",
                )
            ],
        ),
        (
            "Browser admission rejection increments",
            "short",
            "Counter increments by bounded reason over $__rate_interval, not a rejection percentage. "
            "Prometheus extrapolation may produce fractional counts; gaps are not zero.",
            [(increase("admission_rejections_total"), "{{reason}}")],
        ),
        (
            "Browser creation and cleanup failure increments",
            "short",
            "Creation failures split timeout/error/cancelled. Cleanup-incomplete counts events, "
            "not current owners. Inspect quarantined/closing owners for current state.",
            [
                (increase("creation_failures_total"), "creation {{reason}}"),
                (increase("cleanup_incomplete_total"), "cleanup incomplete"),
            ],
        ),
        (
            "Browser cgroup limit-hit increments",
            "short",
            "Increase of raw cgroup pids.events max counter over $__rate_interval. Limit hits are "
            "not admission rejections. Unreadable counters are absent. Resets need lifecycle context.",
            [(increase("cgroup_limit_events_total"), "cgroup limit hits")],
        ),
        (
            "Browser restart and counter-reset context",
            "short",
            "Observed changes of service start timestamp and drops of raw cgroup counter over "
            "$__rate_interval. Browser-process restart need not recreate the cgroup. Changes between "
            "scrapes or across gaps may be missed; zero does not prove no restart.",
            [
                (
                    f"changes({metric('service_start_time_seconds')}[$__rate_interval])",
                    "observed service restarts",
                ),
                (
                    f"resets({metric('cgroup_limit_events_total')}[$__rate_interval])",
                    "observed cgroup counter resets",
                ),
            ],
        ),
    ]
    for index, (title, unit, description, queries) in enumerate(specifications):
        panels.append(
            {
                "id": next_id + index + 1,
                "type": "timeseries",
                "title": title,
                "description": description,
                "datasource": datasource,
                "gridPos": {
                    "x": 12 * (index % 2),
                    "y": y + 8 * (index // 2),
                    "w": 12,
                    "h": 8,
                },
                "fieldConfig": {
                    "defaults": {
                        "unit": unit,
                        "custom": {
                            "spanNulls": False,
                            "showPoints": "never",
                            "drawStyle": "line",
                        },
                    },
                    "overrides": [],
                },
                "options": {
                    "legend": {"displayMode": "list", "placement": "bottom"},
                    "tooltip": {"mode": "multi"},
                },
                "targets": [
                    {
                        "refId": chr(65 + i),
                        "expr": expr,
                        "legendFormat": legend,
                        "datasource": datasource,
                        "range": True,
                        "editorMode": "code",
                    }
                    for i, (expr, legend) in enumerate(queries)
                ],
            }
        )
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--datasource-uid", required=True)
    parser.add_argument("--job", required=True)
    parser.add_argument("--instance", required=True)
    args = parser.parse_args()
    if args.source.resolve() == args.output.resolve():
        parser.error("source and candidate output must differ")
    result = extend_dashboard(
        json.loads(args.source.read_text()),
        datasource_uid=args.datasource_uid,
        job=args.job,
        instance=args.instance,
    )
    with args.output.open("x") as output:
        output.write(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
