# W9 checkpoint 1 with LiteLLM `free`

The frozen candidate remained at runtime revision
`46a528228b1365189cdd38d0bcdb12109a8dc763` with LiteLLM model alias `free`.
Checkpoint 1 ran from a clean checkout matching that revision after its
72-hour gate. It passed at `2026-09-26T19:38:03.623260Z` with all 12 declared
operations: the 11 inherited compatibility journeys and one cross-client
research journey. Text and structured-output model probes both passed. The
research run completed and exposed matching retained artifacts across clients.

The [checkpoint receipt](checkpoint.json) hashes the compatibility, research,
and resource receipts. The published resource and research receipts use
normalized service labels, health status, and CPU and memory percentages. Their
source digests preserve provenance while omitting container IDs and names,
image references, port mappings, host limits, and I/O counters. No candidate
configuration or runtime change occurred.

The current frozen window has **24/30** successful operations and **2/3**
checkpoints. Checkpoint 2 and the final decision cannot count before
`2026-09-30T00:09:25.966168Z`. At least 30 successful operations and all three
checkpoints remain required. Any further runtime or material candidate
configuration change restarts the clock.
