# W7 lean successor freeze — 2026-09-10

Status: **implementation frozen for independent packet curation; comparison not authorized**

Candidate D is the lean source-bound successor proposed by ADR-0080. Its frozen
implementation identity is:

- repository: `magnus919/groktocrawl-x`;
- commit: `85f7da0d815a8c24e2da4baafaa0e7e0dd13bce7`;
- policy: `lean-evidence-first/1`;
- construction schema: `source-bound-selection/1`;
- answer-unit schema: `source-bound-answer-units/1`;
- construction prompt digest:
  `sha256:24429ea075ab62252e47ea31df6971a4e8fd1ef092a01c582071634914a01b28`;
- selective-review prompt digest:
  `sha256:c4da4e7e4b99d62b74b3481731b7873e544f8803b96eb92e492651c8fe8b7f44`;
- requested model alias: `local`.

The label **Candidate D** avoids confusing this policy candidate with the earlier
Arm C LangGraph runtime study. Candidate D is runtime-neutral; the imperative
controller remains the comparison implementation.

## Frozen behavior and budgets

- Up to 8 retained sources and 120,000 exact UTF-8 source bytes.
- Deterministic passages of at most 4,000 code points, up to 32 passages.
- Exactly one construction call, with a 90-second timeout and 1,536 output-token ceiling.
- Up to 12 returned answer units and 8 passages per unit.
- Zero review calls for simple source statements.
- At most one batched selective-review call, with a 90-second timeout and 1,024 output-token ceiling.
- Review is mandatory for inferences, multi-passage units, disputed evidence, and high-consequence guidance.
- Unknown or mismatched freshness cannot publish as a factual unit.
- Exact source reconstruction, deterministic rendering, review results, call receipts, and resolved model identities must reproduce before development-artifact admission.

No retry can add calls beyond these ceilings. Provider usage missing from a receipt
remains unknown rather than zero.

## Evidence available before freeze

The fixture suite covers supported facts, conflicting/disputed evidence, explicit
uncertainty, incomplete coverage, unknown passage and question references, citation
assignment, stale or unknown freshness, failed selective review, changed retained
source bytes, changed rendered text, and false call destination accounting.

The [fixed-source development probe](../evidence/lean-successor/2026-09-10-fixed-source/README.md)
completed its two declared questions in one provider call. This is functional
evidence only. The synthetic fixture and the earlier W1 replacement packet are
exposed development material and cannot support the next comparative-quality claim.

## Isolation boundary and next gate

An independent curator may now create a fresh private packet without seeing
Candidate D outputs. The packet needs the same case-count, domain, as-of, source,
required-subquestion, balance, digest, and access-history controls used by W1. Raw
cases remain outside the implementation workspace until all candidate outputs are
frozen.

This freeze does not accept ADR-0080, authorize a scored comparison, adopt a
production architecture, or replace mainline. Before execution, Magnus must review
the new packet's human-readable metadata and explicitly authorize the Candidate D
comparison and its provider-call budget.
