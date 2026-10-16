# W9 checkpoint 0 after a separate model test

The previous observation window began on September 22 at 16:47 UTC and passed
checkpoint 0. A separate test then recreated the candidate agent at
`2026-09-22T18:53:27.962311981Z` with an older agent image and model alias
`free`. This changed two frozen boundaries, ending that window before its
72-hour checkpoint. Its 12 successful operations remain historical evidence,
with **zero credit** toward this window.

The candidate agent was restored to image and runtime revision
`46a528228b1365189cdd38d0bcdb12109a8dc763` and model alias `general` at
`2026-09-22T19:16:26.093854409Z`. All eleven candidate services were healthy,
the semantic service retained its 14-CPU quota, pgvector was ready, and Qdrant
remained available for rollback. No Hermes configuration was changed.

The first guarded checkpoint attempt reached the grounded-answer operation and
received HTTP 502 while the shared model gateway was restarting. The
[failed compatibility receipt](failed-attempt-compatibility.json) and
[attempt summary](attempt-summary.json) retain this failure; it earns no pilot
credit. Following the protocol's one-retry rule for a transient dependency
failure, the unchanged candidate passed the entire guarded checkpoint at
`2026-09-22T19:20:21.394803Z`: all eleven inherited journeys and the
cross-client experimental research journey, **12/12 declared operations**.

The passing [checkpoint receipt](checkpoint.json) hashes the
[compatibility](compatibility.json), [research](research.json), and
[resource](resources.json) receipts. The research verifier observed runtime
revision `46a528228b1365189cdd38d0bcdb12109a8dc763`, model `general`,
completed research, matching retained artifacts across clients, PostgreSQL
schema 14, pgvector serving, and Qdrant rollback readiness.

The current window begins at `2026-09-22T19:16:26.093854409Z` and has 12/30
successful operations and 1/3 checkpoints. Checkpoint 1 cannot count before
`2026-09-25T19:16:26.093854409Z`; checkpoint 2 and the final decision cannot
count before `2026-09-29T19:16:26.093854409Z`. Any further candidate runtime
or configuration change resets those gates.
