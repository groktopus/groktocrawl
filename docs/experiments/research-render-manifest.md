# Pinned report manifests

Experimental implementation under [issue #94](https://github.com/magnus919/groktocrawl-x/issues/94)
and accepted [ADR-0075](../adr/experimental/groktocrawl-x/0075-consolidate-research-interchange-contracts.md).
This validates report records and exact output bytes. It does not authorize publication
or claim that an auditor evaluated the reports.

`render_manifest.py` defines strict `render-manifest-prototype/1` and
`render-audit-input-prototype/1`. Every field is required; unknown fields/versions,
coercion, duplicate identities and cross-scope output references fail. One manifest
pins a checked knowledge revision digest and exactly one summary, analysis and dossier.
Each output has an immutable logical reference, original byte digest/length, ordered
nonoverlapping code-point statement mappings, and question/conflict IDs. Renderer
identity, version and configuration digest are explicit.

An audit input contains the complete manifest core, excluding audit inputs/results.
Each audit result binds its input ID and domain-separated canonical digest. Every
input requires exactly one result; results cannot predate the outputs. The final
manifest digest includes the audit records, while the audit input digest does not:
changing a result changes the manifest without creating a circular hash. Reviewer
metadata supports the same fixture/tool/model records as knowledge checks, with no
self-issued human approval.

`manifest_outputs.py` admits canonical bytes, checks independently supplied caller
IDs and reviewer catalogue before I/O, then asks a trusted resolver for the exact
retained revision. It verifies revision identity/digest, completed-check chronology,
coverage and local entity references before resolving report bytes sequentially.
Every layer must declare all questions/conflicts and map each question's reporting
claim. Output bytes must match the pinned reference, length and digest, decode as
UTF-8 and contain every exact mapped span. Original output bodies are not retained
in the admission result. Resolver denial/unavailability/cancellation propagates;
no arbitrary URL is fetched.

The resolver/controller owns authorization, validated retained ancestry, current
versus explicit historical revision selection, source liveness and read consistency.
This reader does not revalidate source bodies or prove that the selected revision
is current. Later atomic publication must recheck lifecycle and quotas. Its result
is an inspection record, not a portable trust certificate. Negative audits and
knowledge without executed checks are intentionally inspectable; publication must
separately require eligible knowledge and actual configured audit execution.

Mappings do not prove that every material assertion or caveat is represented, or
that the referenced evidence supports the words. An applicable auditor must inspect
the actual outputs and pinned knowledge for those semantic questions. A catalogue
match and authored pass cannot substitute for that execution. Fixture evaluations
must remain labeled fixtures. The execution and candidate gate below implement the next boundary; the bounded
research journey and retained transaction integration remain under #94.

## Bounds and validation

Canonical manifests and resolved revision documents each retain the 1 MiB bound.
Each of exactly three output descriptors is capped at 1 MiB; bodies are read one at
a time, with no total-process-memory claim. Each output has 1–100 statement mappings,
1–100 question IDs and at most 100 conflict IDs. A statement has 1–100 claim IDs,
at most 1,000 evidence IDs and 100,000 code points of text. There are 1–32 audit inputs
and matching results; repeating full manifest cores may hit the canonical bound
before the count limit. Encoded bundle/root/scope limits remain independently enforced
by storage; this reader does not reserve them or increase any legacy bound.

`tests/unit/test_render_manifest.py` pins canonical manifest and audit-input digests
and checks round trips, exact UTF-8 spans, malformed/coerced fields, three-layer
completeness, caller/reviewer isolation, chronology, coverage, identity collisions,
negative records, oversized envelopes and cancellation. These are authored fixtures,
not evidence of semantic research quality. Existing format readers and golden bytes
are unchanged.

## Executed audit and publication candidate

`render_execution.py` now dispatches a configured auditor with the exact bounded
manifest input, resolved checked knowledge, and all three validated output byte
strings in manifest order. The owner issues the audit identity and UTC completion
time, binds the canonical input digest, and retains only the exact result. A
reconstructed pass, modified result or another owner's receipt cannot satisfy its
binding check. Both execution owners require every issued input for the same
knowledge context or manifest core to be represented, so omitted unfavorable or
failed executions cannot be silently discarded. Closing the owner invalidates receipts. A callback that suppresses
its own cancellation cannot record a late result. Callback occurrence does not
prove semantic correctness; adapters remain trusted code and no provider is wired.

A render owner permits 32 issued inputs and one active inspection, failing immediately
when busy or exhausted. Failed/cancelled inputs consume their issuance allowance.
Each result is limited to 16 KiB without truncation, bounding retained results to
512 KiB. Inspection holds at most three 1 MiB output bodies, alongside bounded input
and knowledge models; this is not a total-process-memory guarantee. The controller
owns deadlines, cancellation and callback shutdown. These receipts are process-local
and do not survive restart.

`publication_gate.py:prepare_publication()` combines manifest resolution with
complete supplied-history/source revalidation, then requires both live execution
owners. All global structural and conflict/coverage checks must pass. Every mapped
claim must be supported, outside unresolved conflict groups, and have passing
support/freshness checks and only supported assessment records, including records
omitted from the selected assessment links. Statement evidence must equal the checked
support closure. Every render audit must pass. Missing checks, negative/indeterminate
results, changed sources and closed owners prevent a candidate. Fixture provenance
from either executor makes the candidate explicitly `fixture_only`.

The returned `PublicationCandidate` is ephemeral input to a future fenced storage
transaction, not a committed publication, durable authorization or public response.
The caller still establishes current versus explicit historical selection, authentic
retained ancestry, coherent reads, quotas and liveness at commit. No endpoint or
writer consumes this candidate yet. The bounded research journey and retained
transaction integration remain under #94. Authored fixture callbacks demonstrate
control flow and cannot establish independently verified research quality.
