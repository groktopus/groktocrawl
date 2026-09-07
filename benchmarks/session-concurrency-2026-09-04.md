# Session concurrency evidence

The production SessionManager and SessionStore ran against an isolated local Valkey instance. Two independent manager/client instances submitted search steps concurrently into the same session. Search was replaced with deterministic 50 ms async I/O; no external provider or model was called. Each case ran seven times. The script creates unique session IDs, verifies unique step indexes and coherent exported refs/history/artifact, and deletes only those sessions.

| Steps | Mode | Completion p50 / p95 (ms) | Admission wait p50 / p95 (ms) | Rejections |
| ---: | --- | ---: | ---: | ---: |
| 1 | Default serialized | 55.60 / 59.44 | 1.11 / 1.83 | 0 |
| 1 | Independent opt-in | 55.63 / 56.54 | 1.78 / 2.00 | 0 |
| 4 | Default serialized | 363.17 / 366.17 | 146.95 / 307.85 | 0 |
| 4 | Independent opt-in | 60.29 / 62.04 | 5.98 / 6.68 | 0 |

Admission wait is elapsed time from submitting a step to entering the fixture search call. It includes lock/reservation work and scheduling. Default lock backoff contributes to the serialized completion time. These figures demonstrate overlap and coordinator behavior under controlled delays; they are not production research latency or a claim about provider throughput. The two clients share one Python process, so process-failure behavior is covered by separate ownership/expiry tests rather than this timing probe.

Raw summaries: [session-concurrency-results.json](session-concurrency-results.json). Reproduce with:

```sh
SESSION_STORE_TEST_URL=redis://127.0.0.1:16379/0 \
PYTHONPATH=agent-svc:. python benchmarks/session_concurrency.py
```
