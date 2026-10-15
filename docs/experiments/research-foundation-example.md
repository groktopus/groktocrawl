# Worked example: a completed investigation with an unresolved price conflict

This is a **synthetic design fixture** for proposed ADRs
[0068](../adr/experimental/groktocrawl-x/0068-separate-research-execution-knowledge-and-rendering.md),
[0069](../adr/experimental/groktocrawl-x/0069-define-versioned-knowledge-and-verification.md), and
[0070](../adr/experimental/groktocrawl-x/0070-evaluate-research-policy-and-runtime-separately.md).
All organizations, URLs and source statements below are fictional. No live site,
LLM or verifier was used. Expected verification outcomes are hand-authored design
expectations, not measured model performance. No runtime implementation is claimed.

## Request and frozen sources

Question: **As of September 1, 2026, what is the monthly USD price of Acme Archive
Pro, and does it include audit logs?**

The fixed scope is a US individual account paying monthly. The two snapshots are
captured at `2026-09-01T12:00:00Z`. Neither source supplies a publication date or
an effective date, so those fields are null. Retrieval on the requested date does
not establish when a price became effective or which source is authoritative.

The exact normalized snapshot bodies (UTF-8, LF line endings, final newline) are:

`src-pricing`, URL `https://acme.example.test/pricing`, normalization `fixture-text/1`:

```text
Acme Archive Pro
US individual accounts, billed monthly in USD.
Price: $20 per month.
Audit logs: included.
```

`src-help`, URL `https://acme.example.test/help/pro`, normalization `fixture-text/1`:

```text
Acme Archive Pro
US individual accounts, billed monthly in USD.
Price: $30 per month.
Audit logs: included.
```

Both pages have the same fictional publisher. They provide two document observations,
not two independent organizations corroborating a fact. Their apparent disagreement
could reflect stale content, but that cause is unknown and must not be invented.
D3 will choose where these immutable bodies live; this example resolves them from
the document itself. The hashes and locators below pin their content exactly.

| Snapshot | SHA-256 of UTF-8 body | Price locator | Audit-log locator |
|---|---|---|---|
| src-pricing | `2d3c9554cb8529f15042a46e17a058b98e21fb3ab8aa2c7b73dffc550447c399` | [64, 85) | [86, 107) |
| src-help | `190973fe2f0268cd2d74d0f9e9acef4ea1307fa6e4a846cc6a32766631fa7719` | [64, 85) | [86, 107) |

## Evidence and claims

The following tables instantiate the semantic entities. IDs are scoped to research
`research-price-001`; they are stable across renderings and are not citation numbers.
The IR schema is `knowledge-ir/1`, revision `ir-001`, parent null, policy
`fixture-evidence-policy/1`, created at `2026-09-01T12:00:01Z`.

| Evidence ID | Snapshot | Exact quote | Purpose |
|---|---|---|---|
| e-price-20 | src-pricing | `Price: $20 per month.` | Price assertion on the pricing page |
| e-price-30 | src-help | `Price: $30 per month.` | Different price assertion on the help page |
| e-logs-pricing | src-pricing | `Audit logs: included.` | Pricing-page feature statement |
| e-logs-help | src-help | `Audit logs: included.` | Help-page feature statement |

| Claim ID | Kind | Text and scope | Evidence assessment |
|---|---|---|---|
| c-pricing-says-20 | source_statement | The captured pricing page lists Pro at USD 20 per month for US individual accounts. | supported |
| c-help-says-30 | source_statement | The captured help page lists Pro at USD 30 per month for the same account/billing scope. | supported |
| c-pricing-logs | source_statement | The captured pricing page lists audit logs as included. | supported |
| c-help-logs | source_statement | The captured help page lists audit logs as included. | supported |
| c-both-logs | inference | Both captured pages list audit logs as included. | supported |
| c-current-20 | inference | Pro's actual monthly price on the requested date is USD 20 for the scoped account. | contested |
| c-price-unresolved | inference | These snapshots do not establish a single current price. | supported |

`supports` edges are evidence → claim:
`e-price-20 → c-pricing-says-20`, `e-price-30 → c-help-says-30`,
`e-logs-pricing → c-pricing-logs`, `e-logs-help → c-help-logs`.
For `c-current-20`, the first price passage supports the amount while the second
contradicts it; unknown effective dates prevent a current-price conclusion.
Record conflict `conflict-price-001` with scope US/individual/monthly/USD, evidence
IDs `e-price-20` and `e-price-30`, and resolution `unresolved`.

Derivation `d-both-logs` connects `c-both-logs` to premises `c-pricing-logs` and
`c-help-logs`, using the rule “both captured documents state the same feature.”
It does not imply independent corroboration or actual entitlement for every account.
Derivation `d-price-unresolved` connects `c-price-unresolved` to source-statement
premises `c-pricing-says-20` and `c-help-says-30`, conflict `conflict-price-001`, and
recorded unknown effective dates. Its rule is “incompatible same-scope amounts
without an applicable authority/effective-date resolution do not determine one
current amount.” Neither derivation references itself or a later output artifact.

A renderer must not introduce “the help page is outdated,” “the lower price wins,”
or “audit logs are guaranteed for your account.” Those claims exceed this evidence.

## Verification record example

This is one expected verification record; a complete IR includes structural, support,
conflict and render-audit records for all entities. W2 must implement and validate
the full envelope described by ADR-0069. For this fixture only, the checked-input
digest is SHA-256 of the embedded `checked_input` serialized as UTF-8 JSON with
sorted keys, no whitespace and no final newline (`fixture-verifier-input/1`). This
does not select D3's whole-IR serialization format.

```json
{
  "verification_id": "v-current-price-001",
  "subject_id": "c-current-20",
  "check_type": "semantic_support",
  "verdict": "indeterminate",
  "verifier": {
    "kind": "fixture_expectation",
    "identity": "hand-authored-design-example",
    "version": "1"
  },
  "policy_version": "fixture-evidence-policy/1",
  "input_revision_id": "ir-001",
  "checked_at": "2026-09-01T12:00:01Z",
  "evidence_ids": [
    "e-price-20",
    "e-price-30"
  ],
  "reason": "Captured same-scope sources disagree, and effective dates are unknown.",
  "checked_input": {
    "claim_id": "c-current-20",
    "claim_text": "Pro's actual monthly price on the requested date is USD 20 for the scoped account.",
    "scope": {
      "region": "US",
      "account": "individual",
      "billing": "monthly",
      "currency": "USD",
      "as_of": "2026-09-01"
    },
    "policy_version": "fixture-evidence-policy/1",
    "evidence_ids": [
      "e-price-20",
      "e-price-30"
    ],
    "snapshot_digests": {
      "src-pricing": "2d3c9554cb8529f15042a46e17a058b98e21fb3ab8aa2c7b73dffc550447c399",
      "src-help": "190973fe2f0268cd2d74d0f9e9acef4ea1307fa6e4a846cc6a32766631fa7719"
    }
  },
  "input_digest_algorithm": "fixture-verifier-input/1",
  "checked_input_digest": "566f09ebd5148136b89744bef42bba60bd54ec441c7f0dc644914fbe88488733"
}
```

The embedded input makes the fixture digest reproducible. `fixture_expectation`
must never be presented as an executed model check or human review. This record
is an expected design result, not a claim that a verifier ran.

Expected checks:

| Check | Expected outcome | What it establishes |
|---|---|---|
| Snapshot digests, exact spans and quote equality | pass | Byte identity and quote placement only |
| Support for each source-statement claim | pass under the hand-authored rubric | The captured document says the scoped text |
| Support for c-both-logs | pass | Both documents list the feature; not a universal entitlement guarantee |
| Current-price verification | indeterminate | No single price can be asserted from this evidence |
| Conflict preservation | pass if both amounts remain visible | The answer did not hide disagreement |
| Render audit | pass only for the qualified output below | Output stays within the eligible claims and qualifiers |

## Operational result and artifacts

The run performs the two acquisitions and assesses the conflict. In this bounded
fixture there are no additional search candidates; stop with a partial answer and
an explicit unresolved question rather than repeatedly generating the same query.
A future live run may deepen under a reserved budget, using the same decision rules.

```json
{
  "run_id": "run-price-001",
  "execution_outcome": "completed",
  "answer_coverage": "partial",
  "stop_reason": "unresolved_conflict",
  "ir_revision_id": "ir-001",
  "unresolved_questions": [
    "Which price applies to a US individual monthly subscription as of 2026-09-01?"
  ]
}
```

This compact projection omits the controller's full budget/operation ledger. It is
not a checkpoint or a proof of restart safety. The question has two required parts:
price and audit logs. The artifact earns credit for the supported, source-attributed
feature finding and appropriate uncertainty, while its unresolved price remains
visible in the fixed coverage denominator.

### Summary renderer

> Both captured pages list audit logs as included. [1][2] They disagree on the
> monthly Pro price: the pricing page lists USD 20, while the help page lists
> USD 30 for the same account and billing scope. [1][2] These snapshots do not
> establish which price applies as of September 1, 2026. [1][2]

Citation mapping is fixed for this artifact revision:
`[1] → src-pricing`, `[2] → src-help`. URLs resolve to those snapshots' descriptors,
not a silently refreshed page. Artifact locators and source URLs serve different
purposes; a client may resolve the archived evidence to audit a citation.

### Analysis renderer

| Required question | Finding | Claim references |
|---|---|---|
| Monthly price | Unresolved same-scope conflict: pricing page USD 20, help page USD 30; effective dates unknown | c-pricing-says-20, c-help-says-30, c-price-unresolved |
| Audit logs | Both captured pages list inclusion | c-both-logs |

### Dossier renderer

The dossier contains both immutable snapshots/descriptors, all evidence locators,
all seven claims, supporting/contradicting/derivation edges, the unresolved conflict,
verification outcomes, acquisition and policy versions, and the stop reason. It
retains `c-current-20` as contested even though the summary never asserts it as fact.

All three renderers pin `ir-001`. The render manifest maps each material statement
to its claim IDs and each citation to a snapshot:

| Summary statement | Claim IDs | Citations |
|---|---|---|
| Both pages list audit logs | c-both-logs | [1], [2] |
| Pricing page USD 20; help page USD 30 for the same scope | c-pricing-says-20, c-help-says-30 | [1], [2] |
| No single current price established | c-price-unresolved | [1], [2] |

The contested `c-current-20` is present in the dossier for inspection, not asserted
as an unqualified fact. These mappings are also the expected render-audit inputs.

## Failure variants and expected gates

| Mutation | Expected result |
|---|---|
| Keep the same snapshot ID but replace USD 20 with USD 25 | Digest mismatch; reject the mutated snapshot |
| Cite src-help for “the pricing page lists USD 20” | Support/citation failure; block that assertion |
| Remove the USD 30 evidence from the published dossier | Conflict/coverage regression; reject the artifact |
| Add a mirror of the USD 20 page | Record shared lineage; do not resolve conflict by document vote count |
| Render “Pro costs USD 20” without qualification | Required support is indeterminate; block or repair within budget |
| Infer the help page is outdated | Missing evidence for the cause; block or acquire dated authority |
| Change the as-of date to October 1 without new evidence | Require freshness evaluation; no automatic claim of current validity |
| Expire the session but retain the research artifact | Snapshots and IR still resolve for their declared retention window |
| Delete a retained snapshot | Explicit evidence-unavailable result; never replace with live content under the old ID |
| Cancel after source acquisition but before IR publication | No completed artifact manifest; retain/reconcile staging according to D3/D5 |
| Render summary from ir-001 and analysis from ir-002 | Cross-revision manifest failure |
| Remove the price question to improve coverage score | Evaluation manifest/denominator mismatch |

## What this settles and leaves open

The example fixes the semantic expectation: reliable research can end in a qualified
partial answer, and every rendering must preserve the same evidence/conflict state.
It does not select a storage engine, graph framework, durable job owner, verifier
model or streaming protocol. D3–D6 must implement those boundaries, including
partial-write recovery and explicit provisional/verified client semantics.
