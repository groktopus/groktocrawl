"""Measure bounded BrowserPool concurrency with a deterministic intercepted page.

This benchmark exercises the production ``BrowserPool`` and Playwright seam,
but it does not make origin requests or run the full scraper/research stack.
Each synthetic session fetches three pages concurrently.  Pool instances are
independent and each is capped at one browser process, so the 1/2/4 pool cases
stay within the four-process resource cap used by this probe.  Each pool also
has a four-lifecycle admission semaphore, matching the scraper's default
``SCRAPER_MAX_BROWSER_CONCURRENCY`` bound.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scraper-svc"))

URL = "https://browser-pool-concurrency-fixture.example/article"
HTML = (
    "<html><body><article><h1>Deterministic fixture</h1>"
    + "Evidence paragraph. " * 100
    + "</article><script>document.title='fixture';</script></body></html>"
)
POOL_COUNTS = (1, 2, 4)
SESSION_COUNTS = (1, 8)
PAGES_PER_SESSION = 3
MAX_CONTEXTS_PER_SESSION = 3
MAX_CONTEXTS_PER_POOL = 4


def _percentile(samples: list[float], fraction: float) -> float:
    ordered = sorted(samples)
    if not ordered:
        return 0.0
    rank = fraction * (len(ordered) - 1)
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (rank - low)


def _summary(samples: list[float]) -> dict[str, Any]:
    return {
        "p50_seconds": round(_percentile(samples, 0.5), 6),
        "p95_seconds": round(_percentile(samples, 0.95), 6),
        "samples": [round(value, 6) for value in samples],
    }


def _descendant_stats(root_pid: int) -> tuple[int, int, float]:
    """Return (descendant count, RSS in MiB, summed ps CPU percent) best effort."""
    ps = shutil.which("ps")
    if not ps:
        return 0, 0.0, 0.0
    try:
        output = subprocess.check_output(
            [ps, "-eo", "pid=,ppid=,rss=,pcpu="],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        rows: dict[int, tuple[int, int, float]] = {}
        for line in output.splitlines():
            parts = line.split()
            if len(parts) != 4:
                continue
            try:
                pid, ppid = int(parts[0]), int(parts[1])
                rows[pid] = (ppid, int(parts[2]), float(parts[3]))
            except ValueError:
                continue
        descendants: set[int] = set()
        changed = True
        while changed:
            changed = False
            for pid, (ppid, _rss, _cpu) in rows.items():
                if (ppid == root_pid or ppid in descendants) and pid not in descendants:
                    descendants.add(pid)
                    changed = True
        rss_kib = sum(rows[pid][1] for pid in descendants)
        cpu = sum(rows[pid][2] for pid in descendants)
        return len(descendants), rss_kib / 1024, cpu
    except (OSError, subprocess.SubprocessError):
        return 0, 0.0, 0.0


class _ResourceMonitor:
    def __init__(self, interval: float = 0.05) -> None:
        self.interval = interval
        self.samples: list[tuple[int, float, float]] = []
        self._task: asyncio.Task[None] | None = None

    async def _run(self) -> None:
        while True:
            self.samples.append(_descendant_stats(os.getpid()))
            await asyncio.sleep(self.interval)

    async def __aenter__(self) -> _ResourceMonitor:
        self._task = asyncio.create_task(
            self._run(), name="browser-pool-resource-monitor"
        )
        return self

    async def __aexit__(self, *_exc: object) -> None:
        assert self._task is not None
        self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)

    def summary(self) -> dict[str, Any]:
        if not self.samples:
            return {
                "peak_descendant_pids": 0,
                "peak_descendant_rss_mb": 0.0,
                "peak_descendant_cpu_percent": 0.0,
            }
        return {
            "peak_descendant_pids": max(sample[0] for sample in self.samples),
            "peak_descendant_rss_mb": round(
                max(sample[1] for sample in self.samples), 2
            ),
            "peak_descendant_cpu_percent": round(
                max(sample[2] for sample in self.samples), 2
            ),
        }


async def _fetch_page(
    pool: Any, admission: asyncio.Semaphore, page_number: int
) -> dict[str, Any]:
    started = time.monotonic()
    async with admission:
        admission_acquired = time.monotonic()
        lease = await pool.acquire(f"{URL}/{page_number}")
        pool_acquired = time.monotonic()
        content = ""
        try:

            async def fulfill(route: Any) -> None:
                await route.fulfill(body=HTML, content_type="text/html")

            await lease.context.route("**/*", fulfill)
            page = await lease.context.new_page()
            try:
                await page.goto(f"{URL}/{page_number}", wait_until="domcontentloaded")
                content = await page.locator("article").inner_text()
            finally:
                await page.close()
        finally:
            await lease.release()
    finished = time.monotonic()
    return {
        "elapsed_seconds": finished - started,
        "admission_wait_seconds": admission_acquired - started,
        "pool_acquire_wait_seconds": pool_acquired - admission_acquired,
        "acquire_seconds": pool_acquired - started,
        "content_hash": hashlib.sha256(content.encode()).hexdigest(),
    }


async def _session(
    pool: Any, admission: asyncio.Semaphore, session_number: int
) -> dict[str, Any]:
    started = time.monotonic()
    semaphore = asyncio.Semaphore(MAX_CONTEXTS_PER_SESSION)

    async def fetch(page_number: int) -> dict[str, Any]:
        async with semaphore:
            return await _fetch_page(pool, admission, page_number)

    pages = await asyncio.gather(
        *(fetch(page_number) for page_number in range(PAGES_PER_SESSION))
    )
    return {
        "session": session_number,
        "elapsed_seconds": time.monotonic() - started,
        "pages": pages,
    }


async def _measure(pool_count: int, session_count: int) -> dict[str, Any]:
    from scraper.browser_pool import BrowserPool

    pools = [
        BrowserPool(enabled=True, max_processes=1, idle_ttl=60, max_age=900)
        for _ in range(pool_count)
    ]
    admissions = [asyncio.Semaphore(MAX_CONTEXTS_PER_POOL) for _ in pools]
    started = time.monotonic()
    sessions: list[dict[str, Any]] = []
    monitor = _ResourceMonitor()
    try:
        async with monitor:
            sessions = await asyncio.gather(
                *(
                    _session(
                        pools[index % pool_count],
                        admissions[index % pool_count],
                        index,
                    )
                    for index in range(session_count)
                )
            )
    finally:
        await asyncio.gather(*(pool.close() for pool in pools), return_exceptions=False)

    page_results = [page for session in sessions for page in session["pages"]]
    session_times = [session["elapsed_seconds"] for session in sessions]
    acquire_times = [page["acquire_seconds"] for page in page_results]
    admission_times = [page["admission_wait_seconds"] for page in page_results]
    pool_acquire_times = [page["pool_acquire_wait_seconds"] for page in page_results]
    elapsed = time.monotonic() - started
    hashes = {page["content_hash"] for page in page_results}
    return {
        "pool_count": pool_count,
        "session_count": session_count,
        "pages_per_session": PAGES_PER_SESSION,
        "max_contexts_per_session": MAX_CONTEXTS_PER_SESSION,
        "max_contexts_per_pool": MAX_CONTEXTS_PER_POOL,
        "total_pages": len(page_results),
        "elapsed_seconds": round(elapsed, 6),
        "throughput_pages_per_second": round(len(page_results) / elapsed, 6),
        "session_latency": _summary(session_times),
        "context_acquire_wait": _summary(acquire_times),
        "admission_wait": _summary(admission_times),
        "pool_acquire_wait": _summary(pool_acquire_times),
        "identical_content": len(hashes) == 1,
        "per_pool_distribution": {
            str(index): sum(
                1 for session in sessions if session["session"] % pool_count == index
            )
            for index in range(pool_count)
        },
        "pool_process_counts_after_cleanup": [pool.process_count for pool in pools],
        "resources": monitor.summary(),
    }


async def measure() -> dict[str, Any]:
    results = []
    for pool_count in POOL_COUNTS:
        for session_count in SESSION_COUNTS:
            results.append(await _measure(pool_count, session_count))
    return {
        "fixture": "real Playwright BrowserPool with intercepted deterministic page",
        "url": URL,
        "pool_counts": list(POOL_COUNTS),
        "session_counts": list(SESSION_COUNTS),
        "results": results,
        "limitations": [
            "No origin network requests, Docker replicas, scraper HTTP server, research loop, or LLM models are involved.",
            "Process RSS/PID/CPU values are best-effort descendant snapshots from ps and include the benchmark process tree.",
            "The cases demonstrate bounded local pool behavior and are not an additive production capacity claim.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = json.dumps(asyncio.run(measure()), indent=2) + "\n"
    if args.output:
        args.output.write_text(result)
    print(result)


if __name__ == "__main__":
    main()
