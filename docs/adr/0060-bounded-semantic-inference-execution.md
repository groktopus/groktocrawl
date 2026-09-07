# ADR-0060: Bounded Semantic Inference Execution

* Status: accepted
* Date: 2026-09-04

## Context

The semantic service's FastAPI handlers share a process with CPU-bound
SentenceTransformer and CrossEncoder model calls. A synchronous reranker call
can stop the event loop, and moving calls to an unbounded executor would allow
concurrent requests, indexing, and migration backfills to oversubscribe the
loaded model and device.

HTTP cancellation also cannot stop native model work reliably. Releasing a
capacity permit when the request task is canceled would make the service report
capacity that is still occupied by a running native call.

## Decision

All semantic model calls use a process-wide `InferenceManager`. It admits at
most `SEMANTIC_INFERENCE_WORKERS + SEMANTIC_INFERENCE_QUEUE_SIZE` calls, waits
for admission only up to
`SEMANTIC_INFERENCE_ADMISSION_TIMEOUT_SECONDS`, and returns a retryable 503
when admission times out. A manager-owned worker awaits each native call in
the event loop's executor, while caller cancellation only abandons the result;
the capacity permit is released after the native call returns or raises.

Interactive calls have priority over maintenance migration backfills that are
already waiting. The default is one model worker, and operators must measure
model/device memory and throughput before increasing it. Queue wait, native
inference latency, in-flight depth, cancellations, overloads, and failures are
exported as bounded-cardinality metrics separate from HTTP request latency.

The manager is created during application lifespan and drains accepted native
calls before executor shutdown. Embedding, reranking, vector-search query
embeddings, indexing, dual writes, and migration backfill all use the same
admission policy.

## Consequences

Health and unrelated async handlers remain responsive while model inference is
running. Under load, callers receive a bounded and observable overload response
instead of unbounded memory growth or hidden model oversubscription. A canceled
request may leave native work running until the model returns, consuming its
capacity by design. Increasing worker count can improve throughput only when
the model and hardware support safe concurrent execution; the service does not
create model replicas automatically.
