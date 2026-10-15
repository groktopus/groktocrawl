#!/usr/bin/env python3
"""Bounded candidate memory study. Run locally on the deployment host.

Only normalized resource data and operation counts are persisted. API credentials,
URLs, compose configuration, container identities and response content stay in memory.
"""

import argparse
import json
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

SAMPLE = r"""
import json, pathlib, urllib.request
p=pathlib.Path('/sys/fs/cgroup')
def read(name): return (p/name).read_text().strip()
stat=dict(line.split() for line in read('memory.stat').splitlines())
events=dict(line.split() for line in read('memory.events').splitlines())
rss=0
zombies=0
for f in pathlib.Path('/proc').glob('[0-9]*/status'):
 try:
  for line in f.read_text().splitlines():
   if line.startswith('VmRSS:'): rss+=int(line.split()[1])*1024
   if line.startswith('State:') and line.split()[1]=='Z': zombies+=1
 except (OSError, ValueError): pass
health=json.load(urllib.request.urlopen('http://127.0.0.1:PORT/health',timeout=5))
print(json.dumps(dict(used=int(read('memory.current')),limit=read('memory.max'),rss=rss,
 anon=int(stat.get('anon',0)),file=int(stat.get('file',0)),
 oom=int(events.get('oom',0)),oom_kill=int(events.get('oom_kill',0)),
 pids_current=int(read('pids.current')),pids_limit=read('pids.max'),zombies=zombies,
 sessions=health.get('active_sessions'),healthy=health.get('status')=='ok')))
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compose-file", action="append", required=True)
    parser.add_argument("--env-file", required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--scrape-url", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    compose = ["docker", "compose", "--env-file", args.env_file]
    for file in args.compose_file:
        compose += ["-f", file]

    def command(*parts):
        return subprocess.check_output(
            [*compose, *parts], text=True, stderr=subprocess.DEVNULL, timeout=30
        ).strip()

    key = command("exec", "-T", "candidate-agent", "printenv", "API_KEY")

    def api(path, body=None, method=None):
        request = urllib.request.Request(
            args.base_url + path,
            data=None if body is None else json.dumps(body).encode(),
            headers={
                "Authorization": "Bearer " + key,
                "Content-Type": "application/json",
            },
            method=method,
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            result = json.load(response)
        if result.get("success") is False:
            raise RuntimeError("operation unsuccessful")
        return result

    services = {
        "browser": ("candidate-browser", "8012"),
        "scraper": ("candidate-scraper", "8001"),
    }
    identities = {
        name: command("ps", "-q", service) for name, (service, _) in services.items()
    }

    def inspect(identity):
        # Never persist inspect output or identifiers.
        return json.loads(
            subprocess.check_output(
                ["docker", "inspect", identity], text=True, timeout=10
            )
        )[0]

    starts = {
        name: inspect(identity)["State"]["StartedAt"]
        for name, identity in identities.items()
    }
    restarts = {
        name: inspect(identity)["RestartCount"] for name, identity in identities.items()
    }
    health = api("/health")
    baseline_runtime = health.get("runtime")
    if not baseline_runtime or baseline_runtime.get("revision") in (None, "unknown"):
        raise RuntimeError("known runtime revision required")
    receipts = []
    baseline_oom = {}
    start = time.monotonic()

    def snapshot(phase, block):
        if time.monotonic() - start > 1200:
            raise RuntimeError("twenty-minute study limit reached")
        if api("/health").get("runtime") != baseline_runtime:
            raise RuntimeError("runtime changed")
        row = {
            "elapsed_seconds": round(time.monotonic() - start, 2),
            "phase": phase,
            "block": block,
            "services": {},
        }
        for name, (service, port) in services.items():
            identity = command("ps", "-q", service)
            info = inspect(identity)
            if (
                identity != identities[name]
                or info["State"]["StartedAt"] != starts[name]
                or info["RestartCount"] != restarts[name]
            ):
                raise RuntimeError("container changed or restarted")
            value = json.loads(
                command(
                    "exec", "-T", service, "python", "-c", SAMPLE.replace("PORT", port)
                )
            )
            if value["limit"] == "max":
                raise RuntimeError("finite memory limit required")
            pressure = 100 * value["used"] / int(value["limit"])
            oom = (value["oom"], value["oom_kill"])
            baseline_oom.setdefault(name, oom)
            row["services"][name] = {
                field + "_mib": round(value[field] / 1048576, 3)
                for field in ("used", "rss", "anon", "file")
            }
            row["services"][name].update(
                pressure_percent=round(pressure, 3),
                active_sessions=value["sessions"],
                healthy=value["healthy"],
                pids_current=value["pids_current"],
                pids_limit=None
                if value["pids_limit"] == "max"
                else int(value["pids_limit"]),
                zombie_processes=value["zombies"],
                oom_delta=oom[0] - baseline_oom[name][0],
                oom_kill_delta=oom[1] - baseline_oom[name][1],
            )
            pid_pressure = value["pids_limit"] != "max" and value[
                "pids_current"
            ] >= 0.8 * int(value["pids_limit"])
            if (
                pressure >= 80
                or pid_pressure
                or oom != baseline_oom[name]
                or not value["healthy"]
            ):
                receipts.append(row)
                raise RuntimeError("resource stop limit reached")
        receipts.append(row)

    outcome = {
        "protocol": "memory-soak-v1",
        "blocks_completed": 0,
        "browser_cycles": 0,
        "scrapes": 0,
        "status": "running",
        "samples": receipts,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        snapshot("baseline", 0)
        for block in range(1, 7):
            for _ in range(10):
                session = api("/v2/browser", {"ttl": 300})["id"]
                try:
                    api(
                        "/v2/browser/" + session + "/execute",
                        {
                            "action": "executeScript",
                            "script": "() => { document.body.innerHTML='<main><h1>Memory fixture</h1>'+('<p>Equivalent research content.</p>'.repeat(1000))+'</main>'; return true; }",
                            "timeout": 10000,
                        },
                    )
                    api(
                        "/v2/browser/" + session + "/execute",
                        {"action": "getContent", "timeout": 10000},
                    )
                    outcome["browser_cycles"] += 1
                finally:
                    api("/v2/browser/" + session, method="DELETE")
                api(
                    "/v2/scrape",
                    {"url": args.scrape_url, "formats": ["markdown"], "timeout": 30000},
                )
                outcome["scrapes"] += 1
                snapshot("work", block)
            outcome["blocks_completed"] = block
            time.sleep(15)
            snapshot("settled", block)
        for block in range(1, 7):
            time.sleep(30)
            snapshot("idle", block)
        outcome["status"] = "completed"
    except Exception as exc:
        outcome["failure_type"] = type(exc).__name__
        if isinstance(exc, urllib.error.HTTPError):
            outcome["http_status"] = exc.code
        # Do not expose exception messages that may contain private URLs.
        outcome["status"] = "stopped"
    finally:
        args.output.write_text(json.dumps(outcome, indent=2) + "\n")
    return 0 if outcome["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
