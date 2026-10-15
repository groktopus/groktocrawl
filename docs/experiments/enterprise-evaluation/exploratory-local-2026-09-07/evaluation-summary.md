# Exploratory local self-check — 2026-09-07

Status: **exploratory only; no comparison or adoption decision**.

The evaluator ran the 30 exposed candidate cases from `corpus.json` inside the existing GroktoCrawl agent container. The container used the internal LiteLLM gateway at `http://inference.example.internal:4000/v1` with the `local` alias for both answering and grading. The gateway key stayed inside the existing deployment.

## What happened

- 30 cases were attempted.
- 24 produced a valid answer and grade.
- 6 failed before grading: malformed JSON or invalid citations/subquestion labels.
- Of the 24 graded cases: **13 ready to use**, **9 need a fix**, **1 do not use**, and **1 cannot tell**.
- The judge marked `critical_finding: true` on 19 records; that flag is a review signal, not proof that each answer was unsafe.
- Answer latency for graded cases was p50 1701 ms and p95 2841 ms; judge latency was p50 2546 ms and p95 3198 ms.

The failed cases are retained in `results.jsonl` and count as failed task coverage. They are not silently dropped from the denominator.

## What this tells us

This run found real harness and answer-quality problems worth fixing before any controlled comparison: six malformed/invalid outputs, one plainly unsafe answer, and repeated omissions around source scope, mirror independence, and recovery after timeouts. It also showed that the internal route and `local` alias can serve the evaluator.

It does **not** show that groktocrawl-x is better than mainline. The cases were exposed to the implementation process, the corpus is synthetic, and the same model answered and graded. No held-out score, A/B comparison, runtime adoption, or replacement claim is authorized.

## Reproducibility

- Corpus SHA-256: `sha256:d3d1821b54783fa831247ea66f362c4a0abacb733c1532ccdb4267be8a7076af`
- Answer model: `local`
- Judge model: `local`
- Calls dispatched: 53 (the six failed cases consumed some answer or judge calls before the run stopped retrying them).
- Raw runner manifest: [`runner-manifest.json`](runner-manifest.json)
- Raw per-case outcomes: [`results.jsonl`](results.jsonl)

The next work item is to fix the output-contract and citation failures, then design a genuinely sealed held-out packet before using any judge result as a quality gate.
