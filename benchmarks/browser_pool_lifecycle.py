"""Measure production BrowserPool launch and context-reuse costs.

The page is fulfilled by Playwright routing, so this benchmark makes no origin
network requests. The cold mode creates and closes a real BrowserPool for each
visit; the pooled mode keeps one BrowserPool and takes a fresh context lease
for each visit. This measures lifecycle reuse at the production pool seam,
not scrape or origin latency.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scraper-svc"))

URL = "https://browser-pool-fixture.example/article"
HTML = (
    "<html><body><article><h1>Fixture</h1>"
    + "Evidence paragraph. " * 100
    + "</article></body></html>"
)


def summary(samples: list[float]) -> dict[str, object]:
    ordered = sorted(samples)

    def percentile(fraction: float) -> float:
        rank = fraction * (len(ordered) - 1)
        low = int(rank)
        high = min(low + 1, len(ordered) - 1)
        return round(ordered[low] + (ordered[high] - ordered[low]) * (rank - low), 6)

    return {
        "p50": percentile(0.5),
        "p95": percentile(0.95),
        "samples": [round(value, 6) for value in samples],
    }


async def visit(pool) -> tuple[str, float, int]:
    started = time.monotonic()
    lease = await pool.acquire(URL)
    try:
        await lease.context.route(
            "**/*", lambda route: route.fulfill(body=HTML, content_type="text/html")
        )
        page = await lease.context.new_page()
        try:
            await page.goto(URL, wait_until="domcontentloaded")
            content = await page.locator("article").inner_text()
        finally:
            await page.close()
    finally:
        await lease.release()
    return (
        hashlib.sha256(content.encode()).hexdigest(),
        time.monotonic() - started,
        pool.process_count,
    )


async def measure(runs: int) -> dict[str, object]:
    from scraper.browser_pool import BrowserPool

    hashes: set[str] = set()
    cold_samples: list[float] = []
    cold_process_counts: list[int] = []
    for _ in range(runs):
        pool = BrowserPool(enabled=True, max_processes=2, idle_ttl=60, max_age=900)
        started = time.monotonic()
        try:
            content_hash, _, process_count = await visit(pool)
            hashes.add(content_hash)
            cold_samples.append(time.monotonic() - started)
            cold_process_counts.append(process_count)
        finally:
            await pool.close()

    pooled_samples: list[float] = []
    pooled_process_counts: list[int] = []
    pool = BrowserPool(enabled=True, max_processes=2, idle_ttl=60, max_age=900)
    try:
        for _ in range(runs):
            content_hash, elapsed, process_count = await visit(pool)
            hashes.add(content_hash)
            pooled_samples.append(elapsed)
            pooled_process_counts.append(process_count)
    finally:
        await pool.close()

    return {
        "runs": runs,
        "fixture": "intercepted-static-page",
        "url": URL,
        "cold_pool_total_seconds": summary(cold_samples),
        "pooled_lease_seconds": summary(pooled_samples),
        "pooled_initial_lease_total_seconds": pooled_samples[0],
        "cold_process_counts_after_lease": cold_process_counts,
        "pooled_process_counts_after_lease": pooled_process_counts,
        "identical_content": len(hashes) == 1,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.runs < 2:
        parser.error("--runs must be at least 2")
    result = json.dumps(asyncio.run(measure(args.runs)), indent=2) + "\n"
    if args.output:
        args.output.write_text(result)
    print(result)


if __name__ == "__main__":
    main()
