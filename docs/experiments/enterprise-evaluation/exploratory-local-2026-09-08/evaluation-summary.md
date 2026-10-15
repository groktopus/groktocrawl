# Corrected exploratory local self-check — 2026-09-08

Status: **exploratory only; no comparison or adoption decision**.

This is a follow-up to the [2026-09-07 run](../exploratory-local-2026-09-07/evaluation-summary.md) after fixing evaluator accounting. It ran the 30 exposed candidate cases inside the existing GroktoCrawl agent container, through the internal LiteLLM gateway at `http://inference.example.internal:4000/v1`, using the `local` alias for both answering and grading. The gateway key stayed inside the existing deployment.

## What happened

- 30 cases were attempted.
- 23 produced a valid answer and grade.
- 7 failed and remain in the denominator.
- Failure stages: 2 answer request, 4 answer validation, 1 judge request, 0 judge validation.
- Of the 23 graded cases: **9 ready to use**, **12 need a fix**, **1 do not use**, and **1 cannot tell**.
- Answer latency for graded cases was p50 1903 ms and p95 3808 ms; judge latency was p50 2730 ms and p95 5390 ms.

The corrected `calls_dispatched` value is **54**. It counts each attempted answer or judge request, including requests that fail before a valid response is returned. The evaluator did not retry a scored case.

## What this tells us

The accounting fix works: failures now say where they occurred, and the call count reflects attempted requests. The model still produced malformed JSON and invalid citations, so the output contract remains a real quality and reliability issue to solve before any controlled comparison.

This run does **not** show that groktocrawl-x is better than mainline. The cases were exposed, the corpus is synthetic, and the same model answered and graded. No held-out score, A/B comparison, runtime adoption, or replacement claim is authorized.

## Reproducibility

- Corpus SHA-256: `sha256:d3d1821b54783fa831247ea66f362c4a0abacb733c1532ccdb4267be8a7076af`
- Answer model: `local`
- Judge model: `local`
- Raw runner manifest: [`runner-manifest.json`](runner-manifest.json)
- Raw per-case outcomes: [`results.jsonl`](results.jsonl)

The next W1 step remains a sealed held-out packet and a reviewed baseline protocol.
