# W9 checkpoint 0 with LiteLLM `free`

The owner pinned `free` for the experimental candidate only. The prior
`general` observation window ended before its 72-hour gate; its 12 successful
operations remain historical evidence with zero credit toward this window.
The candidate image and runtime revision remained
`46a528228b1365189cdd38d0bcdb12109a8dc763`. Mainline and Hermes were
not changed.

The candidate agent restarted at `2026-09-23T00:09:25.966168523Z` and became
healthy. Its `/health` runtime reported `free` and the expected revision.
An initial guarded attempt used a source checkout newer than the deployed
image, so the cross-client verifier stopped at its revision guard. The
[sanitized setup receipt](setup-attempt.json) retains that zero-credit attempt.
The matching clean checkout then passed the guarded checkpoint at
`2026-09-23T00:12:34.352827Z`: all 11 inherited compatibility operations and
the experimental cross-client research operation, **12/12**. The text and
structured-output model probes both passed on `free`. The
[checkpoint receipt](checkpoint.json) hashes the compatibility, research,
and resource receipts.

The published resource and research receipts use normalized service labels,
health status, and CPU and memory percentages. Their source digests preserve
provenance; container IDs and names, image references, port mappings, host
limits, and I/O counters are omitted from the public packet.

The current frozen window has **12/30** successful operations and **1/3**
checkpoints. Checkpoint 1 cannot count before `2026-09-26T00:09:25.966168Z`;
checkpoint 2 and the final decision cannot count before
`2026-09-30T00:09:25.966168Z`. A further candidate runtime or configuration
change restarts those gates.
