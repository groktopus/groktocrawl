# Vector-store failure contract fixture — 2026-09-08

Status: **bounded fixture evidence only**.

This packet exercises six failure boundaries against a deterministic in-memory
model using the same six-record, three-dimensional corpus as the isolated
PostgreSQL/Qdrant comparison:

- write timeout before commit acknowledgement;
- partial write requiring reconciliation;
- malformed search response rejection;
- interrupted restore before activation;
- interrupted migration before cutover; and
- deletion acknowledgement timeout.

The manifest pins the corpus digest, embedding-model label, dimension, distance,
seed and scenario list. Every injected failure is retained in `result.json`.
The gates require that a failed operation is never reported as successful, an
active version remains available after interrupted restore or migration, and a
malformed result is rejected before exposure.

This packet does **not** establish provider behavior, scale, footprint,
latency, migration compatibility, or production recovery. Docker is not
available in the authoring checkout, so the next storage step is to replay the
same scenarios against isolated Qdrant and pgvector services in hosted CI.
The packet makes no production change and does not authorize Qdrant removal.

The raw result is [`result.json`](result.json). It was generated with:

```sh
python3 scripts/run_vector_store_fault_evaluation.py \
  --output docs/experiments/evidence/storage-vector-evaluation/2026-09-08-failure-injection/result.json
```
