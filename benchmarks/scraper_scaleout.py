"""Exercise the production gateway with bounded acquisition twins at 1/2/4 replicas.

This validates DNS/load balancing and configured topology, not real-browser
speedup. Run in an isolated Compose project; never point at a production stack.
"""

from __future__ import annotations

import concurrent.futures
import http.client
import json
import os
import shlex
import statistics
import subprocess
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = [
    "docker",
    "compose",
    "-p",
    "groktocrawl-scaleout-smoke",
    "-f",
    "docker-compose.yml",
    "-f",
    "docker-compose.scaleout.yml",
    "-f",
    "tests/fixtures/compose.scaleout.yml",
]


def compose(*args):
    return subprocess.check_output([*COMPOSE, *args], cwd=ROOT, text=True)


def request(headers=None):
    start = time.monotonic()
    port = os.environ.get("SCRAPER_HOST_PORT", "18091")
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/scrape",
        data=b"{}",
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        body = json.load(response)
    return time.monotonic() - start, body["backend"]


def gateway_runtime(command):
    """Run one admin command against HAProxy's container-local socket."""
    container = compose("ps", "-q", "scraper-gateway").strip()
    return subprocess.check_output(
        [
            "docker",
            "exec",
            "-i",
            container,
            "sh",
            "-c",
            f"printf '%s\\n' {shlex.quote(command)} | nc 127.0.0.1 9999",
        ],
        text=True,
    )


def cancel_client_request():
    """Close a client while its bounded fixture acquisition is still active."""
    port = int(os.environ.get("SCRAPER_HOST_PORT", "18091"))
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.connect()
        connection.putrequest("POST", "/scrape")
        connection.putheader("Content-Type", "application/json")
        connection.putheader("Content-Length", "2")
        connection.putheader("X-Test-Delay", "0.5")
        connection.endheaders()
        connection.send(b"{}")
    finally:
        connection.close()


def graceful_drain_probe():
    """Drain one slot during an active request, then restore it for reuse."""
    command = "set server scrapers/scraper1 state drain"
    restore = "set server scrapers/scraper1 state ready"
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            active = pool.submit(request, {"X-Test-Delay": "0.5"})
            time.sleep(0.05)
            gateway_runtime(command)
            active.result(timeout=10)
        request()
    finally:
        gateway_runtime(restore)


def main():
    os.environ.setdefault("SCRAPER_HOST_PORT", "18091")
    report = {
        "workload": "100ms acquisition twin, four slots per replica",
        "results": [],
    }
    try:
        for count in (1, 2, 4):
            compose("up", "-d", "--scale", f"scraper-svc={count}", "scraper-gateway")
            deadline = time.monotonic() + 60
            seen = set()
            while time.monotonic() < deadline:
                try:
                    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
                        samples = list(pool.map(lambda _: request(), range(16)))
                    seen = {backend for _, backend in samples}
                    if len(seen) == count:
                        break
                except Exception:
                    pass
                time.sleep(1)
            assert len(seen) == count, (
                f"Expected {count} healthy backends, got {len(seen)}"
            )
            single = [request()[0] for _ in range(5)]
            started = time.monotonic()
            with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
                samples = list(pool.map(lambda _: request(), range(64)))
            elapsed = time.monotonic() - started
            latencies = sorted(t for t, _ in samples)
            report["results"].append(
                {
                    "replicas": count,
                    "single_request_p50_seconds": statistics.median(single),
                    "requests": len(samples),
                    "distinct_backends": len({b for _, b in samples}),
                    "p50_seconds": statistics.median(latencies),
                    "p95_seconds": latencies[int(0.95 * (len(latencies) - 1))],
                    "requests_per_second": len(samples) / elapsed,
                }
            )
        cancel_client_request()
        time.sleep(0.1)
        request()
        report["client_cancellation_recovered"] = True

        graceful_drain_probe()
        report["graceful_drain_completed_active_request"] = True
        # Fail one replica abruptly: health checks must withdraw it, and the
        # surviving backends must still serve requests after convergence.
        victim = compose("ps", "-q", "scraper-svc").splitlines()[0]
        subprocess.check_output(["docker", "stop", "--time", "0", victim])
        time.sleep(5)
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            survivors = list(pool.map(lambda _: request(), range(24)))
        assert 1 <= len({b for _, b in survivors}) <= 3
        report["backend_failure_recovered"] = True
        print(json.dumps(report, indent=2))
    finally:
        compose("down", "--remove-orphans", "--timeout", "5")


if __name__ == "__main__":
    main()
