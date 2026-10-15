# W8 source-diversity outcome — 2026-09-11

Status: **adopt bounded quality and publisher independence as the experimental default**

An independent Hermes run produced 16 synthetic held-out cases: four each for
duplicated syndication, shared ownership across domains, primary/secondary
conflicts and independent consensus. Ten cases hide a required fact below the
first three results, ten make three or four search engines look broader than the
publishers actually are, two hide a credible conflict below rank three, and eight
contain a low-quality dissent trap. Fictional facts and `.example` domains keep
the comparison stable and free of current web claims.

Each policy selected at most three sources from the same candidates.

| Measure | Rank only | Quality + independence | Quality-gated opposition |
|---|---:|---:|---:|
| Full answer support | 6/16 | 14/16 | 14/16 |
| Mean required-fact coverage | 79.2% | 95.8% | 95.8% |
| Material conflicts found | 2/4 | 4/4 | 4/4 |
| False-balance cases | 0 | 0 | 0 |
| Mean independent publishers | 1.88 | 3.00 | 3.00 |
| Canonical duplicates selected | 4 | 0 | 0 |
| Cases containing a primary source | 6/16 | 16/16 | 16/16 |
| Mean source quality (1–3) | 2.46 | 2.79 | 2.79 |
| Median / maximum selection time | 0 / 2 µs | 6 / 15 µs | 6 / 8 µs |
| Provider calls / monetary cost | 0 / 0 | 0 / 0 | 0 / 0 |

Engine agreement was not evidence independence. Rank-only selection contained all
four engines on average, but every case had more selected engines than publishers.
Syndicated copies and same-owner domains created the appearance of corroboration.

The quality-and-independence policy first collapses canonical copies, declines
low-quality padding, and then favors high-quality primary sources, new publishers,
new domains and new facts within the three-source limit. It found every material
conflict in this packet without using stance as a selection target. The explicit
opposition policy therefore added no benefit. Its quality gate did avoid all eight
false-balance traps, but deliberate opposition should remain conditional rather
than consume a default source slot.

## Decision and limits

Carry quality-and-publisher independence into the experimental research policy.
Keep relevance rank as an input, not the definition of corroboration. Do not add
an unconditional opposing-source search. Trigger opposition only when a material
claim, credible conflict signal or user request justifies it, and never admit
low-quality dissent merely to create two sides.

This is controlled selection evidence, not a live-web adoption result. Real use
needs optional backward-compatible provenance from SlopSearX: contributing engine
identities, canonical document identity, publisher or owner identity when known,
source type and timestamps. SlopSearX issues #307 and #309 are the appropriate
general-purpose homes for those fields; GroktoCrawl-X must continue to own the
research-specific selection rule.

## Private evidence

The validated packet is frozen at
`sha256:042b0b2eaa5515db241fc90275fd99cbdf57852f372ab0872b13dd7c7ea7a537`.
The result ledger is frozen at
`sha256:28fb79e6485efe2e2eca03bb78d111dfbf530ef6f9c92fc9c1f6d356e24b20de`.
An earlier valid but non-discriminating packet is retained separately and excluded
from the decision. No production configuration changes from this experiment.
