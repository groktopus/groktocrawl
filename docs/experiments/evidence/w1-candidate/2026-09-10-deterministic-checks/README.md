# Candidate B deterministic-check probe — 2026-09-10

Status: **complete fixed-source candidate-core result; not a quality comparison or full live-acquisition result**.

This bounded probe exercised policy `real-research-pilot/3` through the internal LiteLLM `local` alias. It used one synthetic policy note so model construction and review could be isolated from changing public search results. The source contains no private or production data.

The candidate completed both model calls, application-owned structural and conflict-consistency checks, model-owned assessment/semantic/freshness checks, all three report layers, and the deterministic whole-render audit. It produced a complete canonical manifest. This is the first complete terminal manifest from the evidence-first candidate.

## Result

- Model calls dispatched: 2; both returned valid envelopes.
- Reported tokens: 4,274 total (3,645 input and 629 output).
- Model-reviewed checks: 6 across two claims; all returned eligible outcomes.
- Application-owned checks: structural and conflict coverage; both passed exact contract validation.
- Rendered layers: summary, analysis and dossier.
- Publication audit: passed exact byte and descriptor reproduction.
- Terminal result: complete manifest, with no retained database publication.

The machine-readable [usage ledger](usage.json), [checked knowledge](knowledge.json), [manifest](manifest.json), and the three reports are retained beside this note.

## Interpretation and limits

This proves the candidate core can complete without asking a model to judge mechanically decidable record structure. It also proves that the publication gate still rejects semantically ineligible claims: the preceding live-source probe completed both model calls but was denied at claim eligibility.

It does not establish answer quality, search or scraper reliability, production readiness, or improvement over mainline. The fixed source is synthetic and exposed, and the same local model constructed and reviewed the claims. The independently curated comparison packet, human adjudication and explicit scored-comparison authorization remain separate W1 gates.
