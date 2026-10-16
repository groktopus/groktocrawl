# W11 research log

The [premeasurement operator assessment](w11-operator-assessment.md) consolidates
the observed topology, configuration burden, failure modes, recovery evidence, and
rollback path. Its adoption judgment remains deferred until Family A is scored.

## 2026-09-19 — resumed fixed-control handoff

The original private W10 trial directory disappeared with the temporary
worktree during the authorized pause. No scored W11 measurement had begun. The
committed W10 decision, challenge cases, public accounting, and final evidence
package remain complete and hash-bound.

W10 selected fixed retrieval for every challenge type. Under that policy, each
control plan is exactly the committed initial case query; it contains no
model-generated follow-up query. W11 now builds an explicit recovery handoff
from those public artifacts rather than recreating or claiming possession of
the deleted private trial records. The builder requires the final W10
selection to authorize W11, requires every public accounting gate to pass,
requires zero failed trials, binds all inputs by SHA-256, and emits exactly 36
fixed records for 12 cases and three repetitions. The already frozen
eight-result W10 cap remains unchanged.

The recovered handoff produced 36 records and the counterbalanced W11 work
order produced 72 arm trials. All six challenge types map to fixed retrieval.
Focused handoff, work-order, runner, and freeze tests passed before live
measurement. This recovery is valid only for the all-fixed W10 outcome; the
builder fails closed if any challenge type selects an adaptive policy.

## 2026-09-12 — final W10 selection handoff

The dependency audit found that the prepared W11 freeze consumed W10's primary
summary directly. That summary is intentionally produced before adjudication and
cannot distinguish a supported fixed-policy decision from an inconclusive result
that requires another experiment. Treating both as an empty adaptive selection
could have started W11 without a completed W10 decision.

W10 now emits a separate secret-free policy-selection artifact after primary
analysis, adjudication sensitivity, and accounting are complete. The artifact
binds those inputs, the challenge cases, and ADR-0081 by digest. A sensitivity
that changes the decision can select only the follow-up-experiment outcome and
cannot authorize W11 measurement. The W11 work-order builder and freeze now
consume this final artifact, verify its challenge-type inventory, and fail closed
when W10 does not authorize measurement.

Merging the current W10 branch also exposed two proposed records numbered 0081.
The W10 policy decision retains ADR-0081; the later W11 retrieval-ownership
proposal is ADR-0082. Eighty focused W11 tests passed after the stacked merge,
and the narrower 18-test selection/freeze suite, Ruff, documentation surface
check, and refreshed topology CI all passed after the handoff correction.

## 2026-09-11 — W10 result-limit binding

The readiness audit found that the W11 retrieval runner had an independent
ten-result default even though the valid W10 execution uses its frozen
eight-result cap. That would have changed the candidate pool and made the
transport comparison unfair. No scored W11 run had started.

The work-order builder now derives the cap from the completed W10 challenge
manifest and verifies the exact case digest, five-policy order, three
repetitions, record and completion counts, and absence of failed trials or
failed attempts. Both arms consume that bound from the resulting work order.
The independent runner default has been removed.

## 2026-09-11 — installed workflow-composition surface

The installed source revision also exposes a versioned artifact-composition
contract that was not explicit in the first W11 family matrix. This matters to
the future substrate: a retained search, saved report, selected staged search,
entity group, result, research attempt, or research job can seed a later
bounded workflow without copying evidence through model prose.

W11 now treats composition as a separate seven-case hard-gate family. It
requires explicit lineage, idempotent replay, current-policy revalidation,
destination-owned budgets and grants, bounded manifests, and atomic rejection
before persistence or dispatch. Partial, incomparable, expired, foreign,
truncated, conflicting, and unsupported sources must remain explicit. These
cases establish mechanics and do not contribute to the general research score.

All seven exact upstream composition tests passed at the pinned proof revision.
The complete reference packet now proves 40 cases through 120 local passes and
three deliberate local skips that passed against isolated Valkey. There are no
failed or unresolved references.

The representative live arm enabled only research and receipt/manifest export.
It composed one immutable snapshot into one bounded research attempt, returned
the same job on an identical start replay, exported 19 manifest items, and
preserved the explicit `derived_from` edge and non-verification disclosure. The
live hard gate passed.

Two pre-evidence attempts are excluded. The first MCP container was initially
created with a conflicting default host port despite the private environment;
recreation from the inspected Compose model applied the unique port, after
which the least-grant preflight passed. The first composition input then used
an engine/query combination that returned no evidence. Research completed, but
manifest export refused an empty manifest. That is expected boundary behavior,
not a passing journey; the successful run used a working engine and a new
idempotency identity.

## 2026-09-11 — installed 0.5 baseline and reference contracts

The incumbent host GroktoCrawl deployment was rechecked after the operator reported
the new SlopSearX feature release. Both its HTTP and MCP containers identify as
SlopSearX 0.5.0 and resolve to the image digest already pinned by W11. The
ordinary deployment does not grant the new specialist workflows. This preserves
it as compatibility and discovery evidence; scored workflow experiments remain
isolated and enable only the grant required by each arm.

The 31-case contract manifest was then checked against 30 exact tests from the
pinned SlopSearX source. The local suite produced 111 passes and three deliberate
Valkey skips. Those three tests passed against the isolated W11 Valkey instance.
The generated secret-free summary binds both complete JUnit files by SHA-256 and
maps every case to its proof. Raw JUnit remains private because it contains
machine-local paths. An earlier collection attempt is excluded: six references
omitted their pytest class scopes. The manifest and validator were corrected
before the successful run; no outcome was inferred from the failed collection.

## 2026-09-11 — capability discovery

Question: Does the newly installed SlopSearX release change the experimental
roadmap?

Sources inspected:

- SlopSearX 0.5.0 source at revision
  `edeba9ef9311adf19ce3f1b6060257ef1b54c2f9`.
- `docs/ADAPTIVE_RESEARCH.md`: caller-directed continuation, durable budgets,
  immutable attempts, explicit caller completion, and operational stop reasons.
- `docs/STAGED_SEARCH.md`: precise scope plus disjoint fallback after a fully
  observed clean empty result, with deadline and adapter-call ceilings.
- `docs/RETRIEVAL_HANDOFF.md`: machine-readable discovery identity, downstream
  retrieval ownership, receipt semantics, and the mandatory downstream DNS/SSRF
  boundary.
- `docs/ENTITY_GROUPING.md`: explicit-identifier projection, conservation,
  unresolved singletons, conflicts, and the prohibition on treating a group as
  corroboration.
- `docs/MCP_SERVER.md` and the MCP tool registry: opt-in grants and the
  available research, staged-search, entity, receipt, saved-search, and dossier
  tools.

Observed deployment facts:

- The incumbent host service reports package 0.5.0 and the source revision above.
- An authenticated MCP discovery call exposed 35 tools, including adaptive
  research, staged search, entity reads, receipts/manifests, saved searches,
  artifact lineage, and dependency dossiers. Tool discovery is not proof of
  authorization: disabled tools remain discoverable and must fail closed when
  called.
- The service-status contract reported 51 configured engines, connected
  Valkey, immutable snapshots, one concurrent durable research worker, a
  60-second lease, a 600-second default job deadline, a 20-query operator
  ceiling, a 500-result job ceiling, and a 3,600-second snapshot lifetime.
- The shared deployment enables research and selected domain grants, but does
  not enable staged search, receipts, saved searches, saved-search events, or
  dependency dossiers. Its workflow-health report says the implementations are
  available even where policy denies their use.
- The W10 candidate routes to its own pinned SlopSearX 0.4 container. Its
  experiment is therefore not split across releases.

Decision:

- Preserve W10 as the flat-search baseline.
- Evaluate 0.5 in W11 through an isolated, explicitly granted MCP deployment.
- Separate general research coordination, staged dispatch, provenance/entity
  handling, recovery, saved-search monitoring, and dependency dossiers so one
  feature cannot mask another's failure.

Preflight implementation check:

- The first capture was excluded because its configuration fingerprint
  included the current worker ID, which changes across restarts and is not a
  configuration choice.
- The corrected capture retains stable execution settings and omits that
  identity. Its configuration digest is
  `de343af62cb56eb4f6b0f2bcf32467269c058cae453ea23f4624bba7444cc167`.
- All five declared disabled capability probes returned `tool_disabled` before
  dispatch. The retained packet is under
  `evidence/slopsearx-substrate/2026-09-11-shared-discovery/` and remains
  excluded from scored W11 evidence.

Exclusions and limitations:

- Feature documentation and contract tests establish intended behavior, not
  GroktoCrawl integration value.
- The shared incumbent host deployment will not be used for scored runs because its
  grants and workload are not isolated.
- W11 effect margins and the final control policy remain unset until W10
  finishes. No scored W11 run is authorized by this draft.

## 2026-09-11 — deployment contract refresh

A second authenticated preflight after the incumbent host upgrade reproduced the
same 0.5.0 contract: all 35 expected tools were present, research was enabled,
the other five W11 specialist grants were denied before dispatch, and all five
workflow implementations reported healthy. Apart from the evidence label and
the distinction between the registry manifest digest and local image ID, the
stable preflight fields matched the retained discovery packet.

The experimental Compose contract now pins both the ordinary SlopSearX service
and its optional MCP companion to the observed 0.5.0 registry digest. It also
passes every 0.5 specialist grant as an independent empty-by-default setting.
This is required for isolated W11 arms: enabling staged search must not silently
enable receipts, saved searches, event publication, or dependency dossiers.
Deterministic Compose, MCP-client, preflight, and case-contract tests passed.
The local workstation has no Docker executable. A clean checkout on incumbent host
rendered the Compose model successfully and confirmed the pinned 0.5 registry
digest plus nine empty-by-default specialist grant variables. Live isolated
startup remains a separate pre-measurement gate.

The first isolated startup exposed a Compose interpolation defect in the MCP
wrapper: its shell test used a single dollar sign, so Compose substituted the
operator's unset `MCP_AUTH_TOKEN` before the container received the separately
mapped `SLOPSEARX_MCP_AUTH_TOKEN`. The container therefore failed closed and
restarted continuously. The wrapper now escapes the reference for container
evaluation, and its deterministic test rejects the pre-fix form. This failed
startup produced no search or scored evidence.

After correction, the research-only arm became healthy with its own Compose
network and Valkey volume. The authenticated preflight found all 35 expected
tools, exactly one enabled specialist grant (`research`), eight explicit
pre-dispatch denials, targeted-sensitive access disabled, durable leased
execution, and connected isolated Valkey.
The redacted packet is retained under
`evidence/slopsearx-substrate/2026-09-11-isolated-research-only/` with stable
configuration SHA-256
`9c78149ff6543a77aca6032ae252b0e64cdf96a4849a160da8df0a36214ad93a`.
It is deployment evidence only; no scored research task was run.

The isolated ordinary HTTP service then passed the before-run compatibility
gate across HTML root, JSON GET and form-POST search on both compatible paths,
configuration, health, readiness, and invalid pagination. The redacted
nine-probe packet retains no endpoint, query, or result content and has stable
configuration SHA-256
`e4995ad273449aac341088c3789d164f433cf1d4320265019282bc4342bc5a6b`.

A no-dispatch routing preview resolved the HTTP `general` category to seven
engines, then reproduced the same ordered set through explicit-engine routing.
W11 will freeze that list and provide it to both flat HTTP and recorded
continuation arms. This closes an important confound: an intent-profile routing
difference can no longer be mistaken for a persistence or transport effect.
The public report contains engine names and a query digest, but no endpoint,
credential, search result, or private network detail.

To remove repeated manual secret and grant editing, the harness now prepares
private arm environments from a fixed matrix. It creates files exclusively at
private permissions, never places credentials or ports in the public arm
manifest, rejects missing provider credentials and a within-arm port collision,
and explicitly disables every capability outside the selected slice. The
dossier slice encodes SlopSearX's actual composite requirement for dossier,
research, and security grants rather than assuming one switch is sufficient.

The preflight originally enforced the six new workflow grants but merely
carried the older jobs, science, security, and targeted-sensitive settings as
unexamined service metadata. That was insufficient for an exact arm boundary,
especially because the dossier arm requires security. The preflight now checks
all nine specialist grants, runs denial probes for every disabled grant, and
verifies the separate targeted-sensitive flag.
The original six-grant capture remains in the evidence directory under an
explicit incomplete-boundary filename and is excluded from later freeze input.

## 2026-09-11 — general-comparison harness

The W11 A0/A1 harness now refuses to construct a work order until W10 is
complete. It maps challenge types selected by W10 to the `full` control policy
and all other types to `fixed`, then counterbalances the flat-HTTP and recorded-
continuation arms within every case and repetition. Both arms use the same
frozen W10 query sequence and explicit engine list.

The retrieval runner is resumable and separates raw results from public
accounting. Private checkpoints use restrictive permissions. Public records
contain query, scope, and result-URL hashes rather than query or result text. A
retained private checkpoint can reconstruct a missing public record without
another search; a public record with missing private evidence fails closed.
SlopSearX research jobs use deterministic idempotency keys and cumulative
query, attempt, engine-attempt, and result limits.

Quality grading is a separate blind stage. It uses the corrected W10 keyed
assessment schema, the same acquisition limits and `fixed`/`full` admission
rules, one model assessment per trial, and an arm-independent shuffle seed.
Raw excerpts and the exact model response remain private. The public grade
retains normalized source judgments, gap closure, accounting, and response
hashes.

The primary quality gate is now frozen before outcomes: the lower bound of the
case-bootstrap 95% interval must remain above a two-point closure degradation
and a five-point precision degradation. Repetitions are averaged within cases,
not counted as independent evidence. Passing establishes non-inferiority only;
adoption still requires compatibility, provenance, recovery, and sustainable
operations. No scored W11 retrieval or quality run has begun.

## 2026-09-11 — production baseline alignment

The newly installed SlopSearX containers were compared with the isolated W11
candidate by immutable image identity. Both resolve to the same 0.5.0 registry
digest and exact source revision already named by this protocol. The six
capability families that motivated W11 are present in that source release.
This operational update therefore does not change a frozen input or require a
replacement candidate. Scored work continues only in isolated arms so normal
traffic and operator grants cannot affect the comparison.

The discovery-to-capture harness now exercises the first cross-service
provenance path. It reads a SlopSearX result handoff, lets GroktoCrawl perform
the page capture, submits the capture or failure as an attributed receipt,
replays the receipt to prove idempotency, and checks the exported manifest for
complete result linkage. Raw result records and captured content remain in a
private artifact; the public record retains only hashes, states, and gate
outcomes. The harness also requires SlopSearX to disclose that downstream
receipts are observations rather than verification judgments.

The isolated provenance arm passed its least-grant preflight with all 35 tools
present, only retrieval receipts enabled, and the other eight specialist
grants denied before dispatch. Its first Compose start inherited the normal
deployment's exported MCP port and collided before the MCP service started.
The arm was recreated using only its own private port setting; the failed start
is excluded and produced no search evidence.

The first live chain admitted three results. All three preserved discovery
identity through receipt creation, identical replay, retained receipt read,
and manifest export. GroktoCrawl captured and hashed two pages; one eligible
page returned an HTTP error and was recorded as a failed retrieval. The
provenance hard gate passed because the failure remained faithfully linked and
the manifest disclaimed verification. This result does not count the failed
page as successful acquisition and does not establish research quality.

## 2026-09-11 — staged-search evidence correction

A semantic review found that the passing reference attached to the partial-
coverage case exercised retry accounting after an interrupted attempt. It did
not create the declared mixed outcome of one successful-empty engine and one
failed engine. The case was therefore not proven despite the green aggregate
summary.

The missing SlopSearX regression test constructs that exact mixed initial
scope. It requires the operation to fail explicitly, preserves the two charged
initial adapter calls, leaves the fallback pending, and proves the fallback
adapter received zero calls. W11 now references that exact test. The contract
proof pins the test commit, which merged through SlopSearX PR 372 without
changing the 0.5.0 runtime implementation. The regenerated 31-case summary
contains 111 local passes and three isolated-Valkey passes with no unresolved
references, so the staged family has returned to passing status.

## 2026-09-11 — entity-projection pilot correction

The first two-case live entity pilot is excluded from the entity decision. The
CVE case completed, but ordinary URL deduplication had already combined the two
engine observations into one canonical result. This confirmed that entity
projection cannot restore raw results removed earlier in the merge pipeline;
it did not demonstrate another avoidable fetch. The package case used a
human-style package-and-version phrase where the selected package adapter
expects the exact package name, while its companion repository engine lacked
credentials. It returned no results under partial coverage.

The corrected package case now supplies the exact package name to the package
adapter alone. This is an input-contract correction made before accepting any
entity outcome. The failed pilot remains private; its reason and the changed
case are retained here rather than silently overwritten.

The corrected two-case pilot passed. One CVE result projected to one CVE
entity after ordinary URL deduplication had already combined the engine
observations. One PyPI result projected to separate package-release and GitHub
repository entities with a `candidate_repository` edge. Both projections
conserved every result, retained the flat snapshot byte-for-byte, and used only
the documented relationship vocabulary.

The measured count of potentially avoidable acquisitions was zero in both
cases. Entity projection is therefore useful here as an organizational and
relationship view, but the live evidence does not support using it to suppress
fetches. W11 keeps automatic fetch suppression disabled because shared identity
does not prove URL equivalence, source dependence, ownership, applicability,
or verification.

## 2026-09-11 — recovery-boundary audit

The five-case recovery summary was compared line by line with the frozen W11
protocol. It proved reservation charging, lease reclaim, stale-owner fencing,
continuation replay, and caller completion, but it did not directly inject
termination after an engine response, after snapshot persistence, during
GroktoCrawl retrieval, or after receipt submission. Those claims remain open
until exact tests and live recovery evidence are retained.

Two SlopSearX fault-injection tests now cover the first two missing boundaries.
They use an abrupt-process exception outside ordinary error handling. Recovery
marks the unfinished attempt interrupted, retains its charge, keeps a snapshot
written before job linkage readable and unchanged, and completes from the
search response cache without another engine call. The tests merged through
SlopSearX issue 373 and PR 374. W11 pins merge revision
`ebfdd7d463515cd92b8309706aee1b6a095bb78e`; the expanded 33-case contract
summary has 116 local outcomes and three isolated-Valkey outcomes, with every
case proved and no unresolved reference.

A separate GroktoCrawl harness now checkpoints the search result, expanded
handoff, capture, and receipt independently. Deterministic tests interrupt a
page request and interrupt immediately after receipt submission. Resumption
does not repeat search or result expansion; an ambiguous page request remains
counted and may be retried, while receipt resubmission returns the original
receipt rather than creating a duplicate.

The isolated live receipt-boundary run then stopped deliberately after the
first receipt was accepted. Its first process returned the experiment's
interruption code. The resumed process reused one search, one result read, and
one capture, submitted the same receipt payload again, received the original
receipt identity as a replay, observed exactly one retained receipt, and
exported a linked manifest. The secret-free hard gate passed; raw checkpoint
state remains private.

Two earlier live capture attempts are excluded. They pointed the scraper
client at the isolated SlopSearX HTTP port, where `/v2/scrape` correctly
returned not found; the harness recorded failed-retrieval receipts and never
reached the intended process boundary. A fresh case used the GroktoCrawl API
behind a loopback delay proxy. The capture completed, the proxy withheld its
response, and the harness process was terminated. Recovery reused the retained
search and result handoff, visibly counted two capture attempts, created and
replayed one receipt, retained exactly one receipt, and passed the linked
manifest gate. This closes the live downstream-retrieval interruption case.

## 2026-09-11 — isolated saved-search lifecycle

The saved-search arm started with a dedicated network and Valkey volume. Its
preflight enabled only saved searches and saved-search events; the other seven
specialist grants were denied before dispatch. A live package query produced
one immediate baseline and one scheduled no-change comparison. The comparison
reported no source-change events. Pausing held the report count unchanged for
a full interval, repeated event reads returned the same event identities,
acknowledgement was stable on replay, the post-ack read was empty, and resume
plus deletion succeeded.

The first computed hard gate was false because the harness assumed reports
were oldest-first. Inspection showed the API returns newest-first and every
underlying lifecycle check had passed. The evaluator was corrected to select
reports by explicit status and the same retained evidence was reanalyzed; no
search or scheduled interval was repeated. The corrected hard gate passed.

This live case does not establish change-detection accuracy against an
externally evolving source. Controlled added, changed, not-observed, pause,
event, and retention cases remain covered by the pinned deterministic contract
suite. The live result establishes scheduling, stable no-change behavior,
delivery, acknowledgement, resume, and bounded cleanup.

## 2026-09-11 — isolated dependency dossier

The first Compose invocation for this arm omitted its environment file, used a
default port already owned by another service, and stopped before MCP startup or
research dispatch. It is an excluded setup error. The corrected arm used a
dedicated network and Valkey volume, enabled only the documented composite
grants for dependency dossiers, research, and security, and denied the other six
specialist grants before dispatch.

The live versioned-package case reached an honest `partial` state. Package
metadata resolved, and the observed registry version differed from the requested
version. Repository acquisition failed, while advisory leads were empty. The
dossier named repository records as missing coverage, did not claim that the
caller-supplied repository owned the package, left advisory applicability
unevaluated, retained three explicit limitations, and stayed within its six-call
and 500-result ceilings. Repeating the start request returned the same job, and
the terminal report was byte-stable on reread.

The evaluator was frozen after the live probe and then applied to that retained
evidence; the search was not repeated. Its hard gate passed. This supports the
dossier as a conservative evidence-organizing workflow. It does not prove the
completeness or truth of the underlying dependency investigation, and the result
remains separate from open-domain W11 quality scoring.

## 2026-09-19 — general-comparison retrieval

The isolated research arm was recreated from the frozen SlopSearX 0.5 image on
a dedicated Compose project, network, Valkey volume, credentials, and ports.
Fresh preflight evidence enabled only the research grant, proved all eight
other specialist grants denied dispatch, passed the SearXNG-compatible HTTP
checks, and resolved an identical seven-engine scope for both transports.

Two pre-score setup runs were excluded. The first exposed that the client
expected a structured completion object while SlopSearX returns a plain-text
acknowledgement; it also exposed temporary HTTP 429 responses. The runner was
corrected to accept non-empty text acknowledgements while preserving structured
error checks and to use at most five bounded HTTP attempts. The request ceiling
was expanded before the replacement freeze. A clean run then completed 71 of
72 trials; the one exhausted rate-limit checkpoint was cleared and resumed
under the same manifest. All other checkpoints remained byte-stable.

The final retrieval record contains 36 complete pairs. Every pair preserved the
same query plan, query hash, engine scope, and result URLs, with Jaccard 1.0.
The workflow-accounting hard gate failed in all 36 recorded-continuation runs.
Each job filled its exact eight-result budget and became terminal with
`result_budget_exhausted` before GroktoCrawl could declare completion. The later
completion update was acknowledged but did not change `caller_completed` from
false. This is an ownership-contract failure. Subsequent model grading is
diagnostic evidence only and cannot turn this result into an adoption pass.

The first diagnostic grader was stopped after 16 checkpoints. Three completed,
nine exhausted the `general` route's completion allowance in hidden reasoning
and returned null content, and five returned incomplete candidate or claim
maps. Those records are excluded rather than selectively retried. A
content-free provider probe showed `finish_reason: length` under the original
parameter and a complete strict-JSON response with low reasoning effort and
`max_completion_tokens`. The diagnostic grader now uses low reasoning effort
and an 8,000-token combined ceiling, with a separate post-hard-gate freeze.
