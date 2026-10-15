# Delegate Bounded Retrieval Execution to SlopSearX

- Status: accepted
- Deciders: Magnus Hedemark
- Date: 2026-09-11
- Scope: W11 experiment in `magnus919/groktocrawl-x`
- Plan: issue [#318](https://github.com/magnus919/groktocrawl-x/issues/318)
- Supersedes: none

## Context and Problem Statement

GroktoCrawl currently coordinates repeated flat searches itself. W8 found that
adaptive query formulation can recover missing evidence but can also admit less
precise sources. W10 is testing which bounded policy, if any, controls that
tradeoff.

SlopSearX 0.5 adds opt-in MCP contracts for caller-directed research
continuation, bounded staged search, immutable result snapshots, entity
projection, downstream retrieval receipts, research manifests, saved-search
change reports, and dependency dossiers. These contracts overlap with search
execution and provenance work that GroktoCrawl would otherwise implement.

The overlap creates an ownership question. If both systems decide what to
research, whether evidence is sufficient, and when to stop, the combined
system has two competing planners. If GroktoCrawl ignores SlopSearX's durable
workflow contracts, it may duplicate search accounting, recovery, snapshot,
and provenance machinery.

## Decision Drivers

- Keep the user's question, constraints, and research success standard under
  one owner.
- Preserve GroktoCrawl's responsibility for acquired evidence, verification,
  synthesis, and publication.
- Reuse bounded search execution, immutable snapshots, and provenance where
  they demonstrably improve the system.
- Prevent an operational stop reason, result ranking, URL novelty, entity
  grouping, or receipt from becoming a truth or sufficiency judgment.
- Preserve ordinary SearXNG-compatible HTTP behavior and SlopSearX's value to
  clients other than GroktoCrawl.
- Keep every new workflow opt-in, budgeted, observable, recoverable, and
  reversible.
- Avoid making MCP transport or one SlopSearX release part of GroktoCrawl's
  public research artifact format.

## Considered Options

| Option | Benefits | Costs and risks |
|---|---|---|
| Keep all coordination in GroktoCrawl over flat HTTP search | Simple current boundary; no MCP dependency | Duplicates attempt accounting, snapshots, recovery, and handoff provenance |
| Let SlopSearX own the complete research loop | Centralizes planning and retrieval | Creates a second authority for user intent, evidence sufficiency, and stopping; search snippets could be mistaken for verified evidence |
| Keep research authority in GroktoCrawl and delegate bounded retrieval execution to SlopSearX | One research owner with reusable search execution and provenance | Adds an MCP integration and requires explicit mapping, failure handling, and operational evidence |
| Adopt only selected SlopSearX contracts | Limits coupling and permits evidence-led rollout | May retain some duplicated machinery and requires several narrow integration paths |

## Decision Outcome

Evaluate the third option under the W11 protocol. GroktoCrawl remains the
research owner. SlopSearX may own bounded retrieval execution and its durable
operational record.

The boundary is:

| Responsibility | Owner |
|---|---|
| User question, constraints, required claims, and success standard | GroktoCrawl |
| Evidence-gap assessment, query rationale, continuation, and research completion | GroktoCrawl |
| Engine policy, bounded search dispatch, attempt reservations, retries, and immutable result snapshots | SlopSearX |
| Result-to-retriever handoff identity and search provenance | SlopSearX |
| DNS resolution, redirect safety, page retrieval, extraction, hashing, and passage capture | GroktoCrawl |
| Retrieval outcome observation | GroktoCrawl, returned as an attributed receipt |
| Claim verification, contradiction handling, synthesis, citation, and publication | GroktoCrawl |

SlopSearX's `plan_executed` state means the submitted plan ran. It does not
mean the question is answered. Newly seen URLs are leads, entity groups are
identifier projections, scores are ranking weights, and receipts are caller
observations. None grants publication eligibility.

GroktoCrawl consumes versioned MCP contracts behind an internal adapter. Its
retained research model stores the relevant contract name, version, snapshot
identity, result identity, and receipt/manifest references without exposing
SlopSearX's transient job representation as GroktoCrawl's public artifact
schema.

The ordinary SlopSearX HTTP API remains unchanged. W11 enables specialist MCP
grants only in isolated experimental deployments. Saved-search monitoring and
dependency dossiers remain separate capability slices and cannot establish
general research quality.

This proposal does not select the full integration for adoption. W11 may
recommend a narrow subset, reject the integration, or require further study.
It does not authorize a mainline change or production cutover.

### Final W11 decision

W11 selects **narrow adoption**. Ordinary HTTP search remains the default.
The complete recorded-continuation workflow is rejected at the evaluated
SlopSearX 0.5 boundary because result-budget exhaustion made the job terminal
before GroktoCrawl could declare research complete. All 36 recorded trials
retained `caller_completed: false`, despite a later acknowledged update.

Snapshots, retrieval identities, receipts, manifests, recovery, saved-search
events, and dependency dossiers remain eligible as opt-in internal contracts.
Entity projection may organize evidence but may not suppress acquisition based
on the current cases. Staged or broad research orchestration is not adopted.

The matched transports returned identical result sets in every pair, and the
post-run HTTP compatibility gate passed. The incomplete model-grading
diagnostic is not used as quality evidence. Reconsidering the full workflow
requires a terminal-state contract that keeps execution exhaustion distinct
from caller-declared completion and a new frozen comparison.

## Consequences

The design has one place where evidence sufficiency and publication decisions
are made. Search reservations, immutable snapshots, and result provenance can
be reused without making SlopSearX a page retriever or a verification engine.
An attributed receipt can close the discovery-to-capture chain while keeping
the downstream observation distinct from source truth.

The system gains another protocol boundary and more deployment policy. MCP
availability, grants, snapshot expiry, tenant isolation, and version changes
become integration concerns. GroktoCrawl must retain its own post-resolution
SSRF protection because SlopSearX deliberately performs no DNS resolution.
Cross-service recovery can still fail between capture and receipt submission,
so idempotency and reconciliation are required.

A narrow-adoption result may leave flat HTTP search as the common path while
using receipts, manifests, staged search, or durable continuation only for
specific research modes. That is an acceptable outcome if the evidence does
not support the complete workflow.

## Confirmation

The [W11 protocol](../../../experiments/slopsearx-substrate/w11-protocol.md) was the
fitness-function plan. W11 produced the following disposition:

- freeze exact GroktoCrawl and SlopSearX revisions, images, policies, prompts,
  cases, budgets, and analysis;
- pass SearXNG-compatible HTTP checks before and after the experiment;
- compare the W10-selected control with recorded continuation under matched
  cumulative budgets;
- prove discovery-to-capture provenance, tenant isolation, grant enforcement,
  idempotent receipts, expiration handling, and interruption recovery;
- report source precision, weighted claim closure, cost, latency, storage, and
  operator burden separately;
- preserve the complete retrieval evidence package; the post-gate grading
  diagnostic was abandoned and accounted for rather than presented as a
  complete adjudication;
- record the narrow-adoption outcome in this reviewed pre-acceptance revision.

Any compatibility, authorization, provenance, or recovery failure blocks full
adoption regardless of average research quality.

## Links

- [W11 research brief](../../../experiments/slopsearx-substrate/w11-research-brief.md)
- [W11 protocol](../../../experiments/slopsearx-substrate/w11-protocol.md)
- [W11 research log](../../../experiments/slopsearx-substrate/w11-research-log.md)
- [W11 findings](../../../experiments/slopsearx-substrate/w11-findings.md)
- [Narrow provenance adapter issue #327](https://github.com/magnus919/groktocrawl-x/issues/327)
- [SlopSearX caller-completion issue #387](https://github.com/magnus919/SlopSearX/issues/387)
- [ADR-0043](../../0043-migration-to-slopsearx.md)
- [ADR-0068](0068-separate-research-execution-knowledge-and-rendering.md)
- [ADR-0069](0069-define-versioned-knowledge-and-verification.md)
- [ADR-0070](0070-evaluate-research-policy-and-runtime-separately.md)
- [ADR-0074](0074-define-research-recovery-before-selecting-infrastructure.md)
- [SlopSearX retrieval handoff](https://github.com/magnus919/SlopSearX/blob/edeba9ef9311adf19ce3f1b6060257ef1b54c2f9/docs/RETRIEVAL_HANDOFF.md)
