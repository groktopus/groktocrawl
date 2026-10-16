# Method and limits

The study used 12 challenge cases, 24 W8 anchor cases, five policies, and three
repetitions. It preserved every trial, query, candidate disposition, acquired
source digest, source-to-claim link, proposal decision, stop reason, failure, and
adjudication selected by the frozen protocol.

## Input identities

- Primary summary SHA-256: `7bc5592931f33eb09f2c4a90bcddaaad1e662a3499b7441f27615b8da3f9f575`
- Public accounting SHA-256: `f7566190dc420ab87de12f26519a19ce8e25fcf53eb5b45e4d29dcc2cd4ae235`
- Adjudication analysis SHA-256: `9b2a9ae28d0cedc090e52d512722642a84c81dcf827a78080de3fb35114dd1ad`

## Limits

Repeated runs reuse the same questions, so they measure run variability rather
than independent population samples. Live search results are time-sensitive.
Model judgments were independently sampled and checked, but this remains an
agent-adjudicated experiment rather than human-labeled ground truth. The result
applies to the frozen models, prompts, cases, search environment, and bounds.

## SOURCES

- [Research brief](../../../../adaptive-policy/w10-research-brief.md)
- [Frozen protocol](../../../../adaptive-policy/w10-frozen-protocol.md)
- [Analysis plan](../../../../adaptive-policy/w10-analysis-plan.md)
- [Research log](../../../../adaptive-policy/w10-research-log.md)
