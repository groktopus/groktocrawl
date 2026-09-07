"""Measure isolated Valkey storage writes; SESSION_STORE_TEST_URL is required."""

import json
import os
import statistics
import time

from agent.session_store import SessionStore

store = SessionStore(redis_url=os.environ["SESSION_STORE_TEST_URL"])
report = []
try:
    for count in (1, 20, 100):
        session = store.create(ttl=120)
        timings = []
        refs = {
            f"ref_{i}": {"url": f"https://example.test/{i}", "markdown": "é🙂" * 100}
            for i in range(count)
        }
        for _ in range(20):
            start = time.perf_counter()
            store.add_refs(session, refs)
            timings.append((time.perf_counter() - start) * 1000)
        report.append(
            {
                "refs": count,
                "client_round_trips": 2,
                "p50_ms": round(statistics.median(timings), 3),
                "p95_ms": round(sorted(timings)[18], 3),
            }
        )
        store.delete(session)
    for chars in (0, 100_000, 1_000_000):
        session = store.create(ttl=120)
        store.append_artifact(session, "é" * chars)
        timings = []
        for _ in range(20):
            start = time.perf_counter()
            store.append_artifact(session, "追加🙂")
            timings.append((time.perf_counter() - start) * 1000)
        report.append(
            {
                "initial_artifact_chars": chars,
                "new_content_bytes": len("追加🙂".encode()),
                "p50_ms": round(statistics.median(timings), 3),
                "p95_ms": round(sorted(timings)[18], 3),
            }
        )
        store.delete(session)
    print(json.dumps(report, indent=2))
finally:
    store.redis.close()
