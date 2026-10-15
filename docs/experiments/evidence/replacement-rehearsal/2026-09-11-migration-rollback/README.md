# W9 migration and rollback result

Status: **passed after two harness corrections**

The isolated candidate completed the declared PostgreSQL backup/restore and
pgvector-to-Qdrant rollback rehearsal. PostgreSQL remains authoritative for
retained research, pgvector is again serving semantic requests, and Qdrant
remains healthy as the rollback copy for the operational window.

## What passed

- The candidate retained 9 active research artifact sets and 27 artifacts.
  Their identifier and content digests matched after logical backup and restore
  into a new scratch database. Artifact content digests cover the claims and
  evidence inside those artifacts without retaining their bodies here.
- Schema version 14, model identity `v_bge-m3`, retention state, vector rows,
  deletion state, and their declared digests matched in the restored database.
- The synthetic corpus exercised create, update, search, and delete through the
  semantic API. The deleted row was absent from both active stores; PostgreSQL
  retained one vector tombstone.
- A semantic-service restart preserved the pinned query and counts. Readiness
  returned in 12.093 seconds.
- Qdrant rollback preserved the same six served IDs and pinned ranking. It
  reached readiness in 12.066 seconds against the 120-second target.
- Returning to pgvector preserved the same query and counts and reached
  readiness in 11.126 seconds.
- The final provider counts agree at six active vectors. No store or volume was
  removed.

One of the six active vectors predates the synthetic corpus and represents the
public Python documentation fixture used by the compatibility comparison. Five
synthetic rows remain active after the declared deletion.

## Failed attempts retained

The first attempt stopped at manifest collection because the harness selected a
hash function unavailable in the candidate PostgreSQL image. It had already
written the synthetic corpus. The query was changed to the SHA-256 function used
by the repository migrations.

The second attempt completed through backup/restore but timed out recognizing
Qdrant readiness because Qdrant-mode health reports `qdrant: ready` rather than a
`vector_store` field. Direct inspection showed the service healthy in Qdrant
mode. The harness was corrected, the candidate was returned to pgvector, and the
entire idempotent sequence was rerun.

Neither failure exposed an application data mismatch. Both receipts are retained
beside the passing receipt, and every cleanup path returned the candidate to
pgvector.

## Decision

The evidence supports accepting ADR-0079 for this experimental fork and selecting
Qdrant removal from the experimental steady-state stack after the W9 operational
rollback window passes. Qdrant stays deployed and unchanged during that window.
Removal remains a separate reversible change and does not affect mainline or the
incumbent deployment.

## Limits

This was a logical restore inside the same isolated PostgreSQL instance, not a
physical-host disaster or point-in-time recovery. The vector corpus was bounded,
and this single rehearsal does not establish production capacity. The seven-day,
30-request operational window remains the next gate.
