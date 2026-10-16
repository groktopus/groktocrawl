# W9 current-main checkpoint 0

- Candidate and runtime revision: `46a528228b1365189cdd38d0bcdb12109a8dc763`
- New observation window: `2026-09-22T16:47:25.742584Z`
- Checkpoint completed: `2026-09-22T16:49:57.651610Z`
- Result: **passed, 12 of 12 declared operations**; current window is 12/30 operations and 1/3 checkpoints.
- Next time gate: `2026-09-25T16:47:25.742584Z`; seven-day gate: `2026-09-29T16:47:25.742584Z`.

The replacement candidate was rebuilt from a clean current-main checkout. Its
temporary Jev agent image override was removed while retaining the pinned
SlopSearX image and private engine configuration. All eleven services were
healthy before the window started. Public health reported model alias `general`
and the exact revision above. The semantic service served pgvector with its
durable 14-CPU limit; Qdrant remained ready for rollback. The verifier's
source receipt hashes the effective two-file Compose configuration without
publishing the private override or environment values.

The guarded runner completed the inherited scrape, crawl, search, answer,
agent, webhook, CLI, and MCP journeys, plus the experimental cross-client
research verification. The two model-readiness probes succeeded. The resource
snapshot is a point-in-time observation, not a capacity claim.

Two attempts before the passing packet reached the CLI step and stopped because
the isolated runner environment supplied `httpx` but not `requests`, which the
repository CLI imports. Host Python had both, masking the setup difference in
an initial single-case diagnostic. The failed packets remain in private
operational storage; [the sanitized failure summary](setup-failures.json)
retains their timing and cause. The runner now checks both dependencies before
product traffic, and the corrected isolated environment completed the full
checkpoint. No failed product operation was reclassified as a pass.

The published packet contains only generic service identities, loopback/public
fixture URLs, hashes, timings, and bounded resource statistics. A targeted
private-data scan and Gitleaks found no credentials, private hostnames,
private addresses, or personal filesystem paths.
