# Jev staged-continuation public-document pilot

Status: **frozen exploratory pilot; no TypeSafe calls or searches have been
made. Independent packet review is required before calls.**

Study ID: `jev-continuation-v2-pilot-2026-10-03`
Contract: `continuation-evidence-gap-addressability/2`
Pinned model: requested and returned `jev-1.13.0`
Public source revision: [`7b9bf51de1334d4d7e6bd6256d3085c8c2a8d60d`](https://github.com/magnus919/groktocrawl-x/tree/7b9bf51de1334d4d7e6bd6256d3085c8c2a8d60d)

This bounded pilot evaluates eight fresh obligations from public GroktoCrawl
documentation. They are separate from the exposed LangGraph continuation and
page-change cases. The study is descriptive feasibility work: assistant
references and research-agent proposals are best-effort, are not independent
gold labels, and cannot support claims of general quality or calibrated
thresholds. No runtime integration, production policy, or mainline changes are
in scope.

## Frozen protocol

The frozen packet is
[`continuation-pilot-2026-10-03.case-packet.json`](continuation-pilot-2026-10-03.case-packet.json).
Its SHA-256 is
`6bdff861da0dfb8f20b7d11983034520d7a37bda6e8d0ad7037da40d314d023c`.
The request-builder contract and wording are pinned at commit
`84f5a6498834ccf23eab5f9b6cf930232717908a`.
It contains exact public source blobs for offline span validation, bounded
first-pass excerpts shown to Jev, one assistant-authored research-agent
proposal per case, best-effort reference assessments, and SHA-256 digests for
the exact stage 1, stage 3, and potential stage 4 requests. The full source
blobs and reference fields are evaluator-only. The call path sends only the
stage-specific state and structured question/proposal. The frozen request
builder validates all excerpt offsets and citations before constructing any
request.

The flow is sequential per case:

1. Stage 1 asks whether the supplied first-pass evidence adequately answers
   the exact research question and scoped obligation.
2. A separate research-agent stage supplies the packet's concrete gap
   hypothesis with cited exact source spans. Jev never generates or repairs
   hypotheses. These fixed proposals include plausible misses, false missing
   proposals, one aligned-passage non-contradiction control, and a
   deployment-private-information case. The packet's agent proposals are
   assistant-authored; this pilot does not estimate an independent agent's
   gap-generation coverage.
3. Stage 3 asks one Noul about the exact proposal: either the missing
   information is absent from the supplied material, or the two exact cited
   passages conflict on the same subject/version/context.
4. Stage 4 is called only if stage 3's validated yes-probability exceeds its
   implied no-probability (the fixed binary argmax; an exact tie abstains).
   This is a sequencing guard only, not a search-action threshold, and it
   never dispatches a search. Stage 4 asks whether bounded public sources
   could reasonably resolve that exact proposal. The returned probability
   is recorded, not converted into an actual search.

Use the exact trusted instruction strings in the frozen request-builder
module. Stage 1 uses `type: "noul"` and `_STAGE1_QUESTION`; stage 3 uses
`_MISSING_QUESTION` or `_CONTRADICTION_QUESTION` with that case's exact
validated hypothesis in structured instructions; stage 4 uses
`_ADDRESSABILITY_QUESTION` and the exact stage-3 proposal. The provider answer
is accepted only as `answers[id].noul`, with exact response membership, model
revision, and finite `[0,1]` validation. Do not read or substitute a confidence
field. A value near 0.5 means uncertainty about that proposition.

## Budget, exclusions, and reporting

- Eight stage-1 calls and eight stage-3 calls are planned. Up to eight
  dependent stage-4 calls may follow, for a maximum of **24 total requests**.
- One request is in flight at a time. No retries or replacement calls. A
  transport, provider, parse, or validation failure consumes that attempt and
  remains unevaluated; dependent stages are skipped.
- There are no new searches. The pilot assesses semantic evidence gaps and
  public-addressability only. No query is formulated or dispatched, and no
  caller-facing or notification action is taken.
- Only the pinned public Git revision and source passages in the packet are
  permitted. No private deployment configuration, credentials, authenticated
  content, or non-public operational state may be sent.
- Do not change cases, labels, wording, exclusions, or thresholds after the
  first call. Report exact counts and each case outcome, with provider failures
  and skipped dependent stages visible. Keep assistant judgments labeled as
  best-effort and discuss disagreement as exploratory signal, not accuracy
  against independent gold.
- Preserve request/response digests, returned `answers[id].noul`, model pin,
  latency and usage when available. Never persist, print, or commit provider
  credentials. Do not publish raw provider receipts unless separately cleared.

## Frozen case references

| Case | Best-effort first-pass status | Stage-3 reference | Stage-4 reference if reached | Stratum |
|---|---|---|---|---|
| `pilot-01` | satisfied | false missing proposal | not reached by reference | adequate with false missing proposal |
| `pilot-02` | satisfied | false missing proposal | not reached by reference | adequate with false missing proposal |
| `pilot-03` | missing | specific numeric limit absent | public source plausibly resolvable | missing numeric contract |
| `pilot-04` | missing | cache-miss behavior absent | public source plausibly resolvable | missing cache behavior |
| `pilot-05` | satisfied | false missing proposal | not reached by reference | adequate with false missing proposal |
| `pilot-06` | satisfied | false missing proposal | not reached by reference | adequate with false missing proposal |
| `pilot-07` | satisfied | cited passages align | not reached by reference | aligned passages, non-contradiction control |
| `pilot-08` | insufficient to assess | deployment-specific setting absent from public material | not resolvable from allowed public scope | private-state boundary |

The reference labels were authored before model calls by the research agent
that assembled the packet. They are not blinded independent adjudications.
The pilot will assess four separate things descriptively: stage-1 evidence
sufficiency; whether Jev accepts or rejects each supplied stage-2 hypothesis;
stage-4 public-addressability conditional on stage 3; and pre-call abstention
or failure handling. The packet does not evaluate actual query quality or
evidence gain because it contains no live search arm.
