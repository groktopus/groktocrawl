"""Measure actual session coordination with real Valkey and delayed search I/O.

SESSION_STORE_TEST_URL=redis://127.0.0.1:16379/0 \
PYTHONPATH=agent-svc:. python benchmarks/session_concurrency.py
Only uniquely created benchmark sessions are written and deleted.
"""

import asyncio
import json
import math
import os
import statistics
import time
from unittest.mock import patch

from agent.session import SessionManager


def summarize(values):
    ordered = sorted(values)
    return {
        "p50": round(statistics.median(ordered), 3),
        "p95": round(ordered[math.ceil(len(ordered) * 0.95) - 1], 3),
    }


async def sample(url, parallel, count):
    managers = [SessionManager(redis_url=url) for _ in range(2)]
    session_id = await managers[0].create_session()
    starts, admitted, latencies = {}, {}, []

    class Search:
        def __init__(self, *args, **kwargs):
            pass

        async def search(self, query, **kwargs):
            admitted[query] = time.perf_counter()
            await asyncio.sleep(0.05)
            return (
                [
                    {
                        "url": f"https://fixture.invalid/{query}",
                        "title": query,
                        "description": "controlled evidence",
                    }
                ],
                None,
            )

        async def close(self):
            pass

    async def step(index):
        query = str(index)
        starts[query] = time.perf_counter()
        result = await managers[index % 2].step(
            session_id,
            "search",
            {"query": query},
            llm_model="fixture",
            parallel=parallel,
            idempotency_key=f"benchmark-{index}",
        )
        latencies.append((time.perf_counter() - starts[query]) * 1000)
        return result

    try:
        with patch("agent.session.SearXNGClient", Search):
            start = time.perf_counter()
            results = await asyncio.gather(*(step(i) for i in range(count)))
            elapsed = (time.perf_counter() - start) * 1000
        exported = await managers[0].export_session(session_id)
        assert len({r["step_index"] for r in results}) == count
        assert len(exported["steps"]) == len(exported["refs"]) == count
        assert all(ref in exported["artifact"] for ref in exported["refs"])
        return {
            "completion_ms": elapsed,
            "step_ms": latencies,
            "admission_wait_ms": [(admitted[q] - starts[q]) * 1000 for q in starts],
            "steps": count,
            "rejected": 0,
        }
    finally:
        await managers[0].delete_session(session_id)
        for manager in managers:
            manager.store.redis.close()


async def main():
    url = os.environ["SESSION_STORE_TEST_URL"]
    report = {
        "fixture_search_ms": 50,
        "independent_clients": 2,
        "runs": 7,
        "results": [],
    }
    for count in (1, 4):
        for parallel in (False, True):
            samples = [await sample(url, parallel, count) for _ in range(7)]
            report["results"].append(
                {
                    "parallel": parallel,
                    "steps": count,
                    "completion_ms": summarize([s["completion_ms"] for s in samples]),
                    "step_ms": summarize([v for s in samples for v in s["step_ms"]]),
                    "admission_wait_ms": summarize(
                        [v for s in samples for v in s["admission_wait_ms"]]
                    ),
                    "rejected": sum(s["rejected"] for s in samples),
                }
            )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
