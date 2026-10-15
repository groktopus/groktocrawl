# Consolidate Research Interchange Contracts

- Status: accepted for bounded experimental implementation
- Deciders: Magnus Hedemark
- Date: 2026-09-06
- Scope: bounded experimental research formats; `magnus919/groktocrawl-x` only
- Plan: issue [#90](https://github.com/magnus919/groktocrawl-x/issues/90)
- Supersedes: ADR-0071 canonical IR ceiling for the initial consolidated implementation only; extends ADR-0069 and ADR-0072 as scoped below

- Accepted: 2026-09-06 by Magnus Hedemark: “I accept your proposal.”
- Acceptance record: [issue #92](https://github.com/magnus919/groktocrawl-x/issues/92).
- Acceptance scope: bounded consolidated admission and the experimental research
  journey; preserve old readers/data and the initial 1 MiB format bound.
- Implementation, format-freeze and evaluation gates remain. No provider spending,
  production cutover, vector/runtime selection or recovery adoption is authorized.
- The decision body below preserves the reviewed proposal; its prospective
  acceptance wording is historical, not a reversal of this acceptance metadata.

## Context and Problem Statement

The accepted Knowledge IR direction now has executable fixture validation and
complete retained-history behavior. However, structure, verification, research,
revision and publication use separate nested prototype formats. They repeat large
input objects and distinguish canonical retained bytes from parsed default values.
Changing their names would not create an interoperable contract.

The [field review](../../../experiments/research-ir-contract-review.md) and
[frozen compatibility examples](../../../../tests/contracts/research/README.md) establish
what those readers currently accept. We need one explicit research contract before
building the next research workflow against it.

## Decision Drivers

- Exact evidence and verifier inputs remain inspectable after a session expires.
- Knowledge can be re-rendered without making presentation part of claim identity.
- Digests have explicit domains and no circular dependencies.
- Fixture, model, tool and human reviewer provenance remain distinguishable.
- Old bytes remain readable without fabricated dates, assessments or approvals.
- Bound implementation effort and memory before broadening research behavior.

## Considered Options

| Option | Benefits | Costs |
|---|---|---|
| Rename the current complete envelope | Smallest code change | Freezes fixture nesting and default ambiguity; does not supply the proposed manifest or real reviewer contract |
| Keep every prototype as a permanent public format | Preserves existing implementation | Multiplies public reader/context contracts and leaves downstream research coupled to fixture details |
| Explicit consolidated IR and separate render manifest, alongside old readers | Stable responsibility boundaries and auditable conversion | New validation, compatibility cases and a bounded storage integration are required |

## Decision Outcome

Recommend the third option, specified in the
[consolidated format proposal](../../../experiments/research-consolidated-format.md).
`knowledge-ir/1` contains revision context, source descriptors/references, claims,
evidence, relationships, questions/conflicts, assessment links and independently
input-bound verification records. `render-manifest/1` pins one IR digest and three
output layers, with statement mappings and audits. Retained source/output bytes
remain separately resolved through an authorized store.

Require every declared field, including explicit nulls and empty collections;
reject unknown fields and versions. Canonicalize the submitted validated values
without applying model defaults or coercing strings into numbers. Source text is
never normalized again during admission. Source references identify immutable
bytes; they do not themselves grant access.

Use the existing JCS domain-prefix rule for new formats, with distinct versioned
verifier and render-audit inputs. Check records hash their input projection rather
than their enclosing IR; render audits hash an audit-free manifest projection.
Keep original fixture digest interpretations unchanged under old readers.

For the first implementation retain the experimentally implemented **1 MiB**
canonical revision/manifest and encoded-bundle bounds, plus at most 20 revisions.
Do not claim the capacity experiment establishes larger interchange capacity.
Revisit bounds only with a separately measured workload and an explicit decision.

## Inherited Decision Impact

| Record | On adoption | Exact scope |
|---|---|---|
| ADR-0069 | Extend | Specify consolidated layout, explicit nulls, source references, assessment mapping and versioned reviewer inputs; retain claim/evidence semantics, immutable IDs and separate structural/support/render checks |
| ADR-0071 | Partially supersede for the initial consolidated-format implementation | Use 1 MiB instead of the proposed 5 MiB canonical IR ceiling; retain JCS domains, source lifecycle, authorization, quotas, deletion and storage-adoption gates |
| ADR-0068 | Retain | Execution ownership/budgets stay outside retained IR; no framework choice |
| ADR-0070 | Retain | Structural compatibility is not semantic quality or an authorized comparison |
| ADR-0072 | Extend at artifact boundary | Stable artifact descriptors can carry these versions; HTTP/SSE/CLI/MCP delivery and principal authentication remain separate work |

This proposal changes no accepted ADR body or predecessor status. Acceptance would
record these scoped successor links in this fork only. Mainline remains unaffected.

## Consequences

Research stages gain one explicit interchange contract while presentation remains
independently replaceable. Separate input objects make checked evidence precise
without embedding the entire result recursively. Authoritative bytes and model
projections no longer have an unspecified relationship in the new format.

Additional validation and explicit source resolution are required. Strict bounds
can reject a large valid research result; the controller must surface the limit
without truncating evidence or publishing an unverified substitute. Old formats
remain supported and consume maintenance effort. Reviewer metadata records who or
what reportedly performed a check; storage integrity alone cannot authenticate it.

## Confirmation

The implementing agent maintains change-triggered tests under repository CI;
Magnus owns the architecture decision and evaluates adoption evidence. Before format
freeze, require zero accepted malformed/unknown-version controls, exact golden
canonical bytes/digests, source/ref closure, immutable-history checks and unchanged
legacy pins. Missing evidence is an incomplete gate, not a pass. A failing control
blocks format publication; fixture errors require a documented correction, not a
weakened invariant.

Then exercise one bounded acquisition → construction → verification → rendering
journey using the consolidated contracts. Existing authored fixture judgments may
validate the plumbing but must remain labeled fixture expectations. Independent
semantic evaluation and any provider budget remain separately required before
claiming research quality. New storage integration requires actual PostgreSQL
lifecycle/restore checks, not only pure-model validation.

Review these checks on every affected format change. Retire an old-version check
only after a separately approved reader-retirement/migration decision and inventory
of its retained artifacts. This proposal does not set production performance SLOs,
select pgvector/LangGraph/recovery infrastructure or authorize provider execution.
