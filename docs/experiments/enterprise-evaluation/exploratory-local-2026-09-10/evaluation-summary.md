# Exploratory local reliability refresh — 2026-09-10

Status: **exploratory only; output reliability improved; no comparison or adoption decision**.

This reruns the same 30 exposed candidate cases used by the [corrected 2026-09-08 self-check](../exploratory-local-2026-09-08/evaluation-summary.md). It ran inside the existing production GroktoCrawl agent container on `incumbent.example.internal`, through the internal LiteLLM gateway on `inference.example.internal`, using the `local` alias for both answering and grading. The gateway key remained inside the existing deployment.

## What happened

- 30 cases were attempted.
- 29 produced a valid answer and grade; 1 failed and remains in the denominator.
- The sole failure was an answer-validation failure for invalid citation IDs.
- Of the 29 graded cases: **12 ready to use**, **14 need a fix**, and **3 cannot tell**.
- Answer latency for graded cases was p50 1,746 ms and p95 2,878 ms.
- Judge latency for graded cases was p50 2,630 ms and p95 3,380 ms.
- The runner dispatched 59 calls without retrying a scored case.
- Reported usage across valid answer and judge calls was 30,051 tokens: 11,918 answer tokens and 18,133 judge tokens.

## What changed from the prior run

The valid completion rate rose from 23/30 (76.7%) to 29/30 (96.7%). Failed cases fell from seven to one, and request-level failures fell from three to zero. Median answer latency also fell from 1,903 ms to 1,746 ms; median judge latency fell from 2,730 ms to 2,630 ms. These are observations from two runs on the same small exposed corpus, not estimates of production reliability or a controlled performance comparison.

The remaining failure shows that source-bound citation validation still matters even when the local route is otherwise healthy. The runner rejected the invalid answer rather than converting it into a grade.

## What this does and does not support

This run supports using the local route for the next bounded evaluation step: it completed every model request and retained the one invalid answer as a failure. It does not establish answer quality. The cases and expectations were exposed, the corpus is synthetic, and the same model answered and graded each case. The grade labels are diagnostic only; 25 of the 29 grades also carried the judge's `critical_finding` flag, which makes self-grading unsuitable as an adoption gate.

No held-out score, A/B comparison, runtime adoption, production migration, or mainline replacement claim is authorized by this evidence. Candidate B identity and a fresh independently curated comparison packet still need to be frozen before the scored comparison.

## Reproducibility

- Corpus SHA-256: `sha256:d3d1821b54783fa831247ea66f362c4a0abacb733c1532ccdb4267be8a7076af`
- Answer model: `local`
- Judge model: `local`
- Completed: `2026-09-10T08:53:35Z`
- Raw runner manifest: [`runner-manifest.json`](runner-manifest.json)
- Raw per-case outcomes: [`results.jsonl`](results.jsonl)
