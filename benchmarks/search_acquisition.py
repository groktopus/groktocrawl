"""Reproducible delayed-I/O probe; no network, providers, or credentials.

Run with PYTHONPATH=agent-svc:. python benchmarks/search_acquisition.py.
This isolates acquisition/reuse, not provider or model latency.
"""

import asyncio
import json
import statistics
import time

from agent.research.acquisition import acquire_source_artifacts


class DelayedScraper:
    def __init__(self):
        self.calls = 0
        self.active = 0
        self.peak = 0

    async def scrape(self, url, **_kwargs):
        self.calls += 1
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            await asyncio.sleep(0.02)
            return {"success": True, "data": {"markdown": f"evidence {url}"}}
        finally:
            self.active -= 1


async def probe(count, shared):
    scraper = DelayedScraper()
    results = [{"url": f"https://example.test/{i}"} for i in range(count)]
    start = time.perf_counter()
    if shared:
        artifacts = []
        for _stage in range(3):
            acquired = await acquire_source_artifacts(
                results, scraper, existing=artifacts
            )
            artifacts = acquired.artifacts
        assert len(artifacts) == count
    else:
        # Previous hybrid ranking (sequential), rich (five), contents (two).
        for slots in (1, 5, 2):
            semaphore = asyncio.Semaphore(slots)

            async def fetch(result, slots=semaphore):
                async with slots:
                    return await scraper.scrape(result["url"])

            await asyncio.gather(*(fetch(result) for result in results))
    return (time.perf_counter() - start) * 1000, scraper.calls, scraper.peak


async def main():
    report = []
    for count in (1, 5, 10):
        for shared in (False, True):
            samples = [await probe(count, shared) for _ in range(10)]
            timings = sorted(sample[0] for sample in samples)
            report.append(
                {
                    "sources": count,
                    "shared": shared,
                    "p50_ms": round(statistics.median(timings), 2),
                    "p95_ms": round(timings[-1], 2),
                    "fetches": samples[0][1],
                    "peak_fetches": max(s[2] for s in samples),
                }
            )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
