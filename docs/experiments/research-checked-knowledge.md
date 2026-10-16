# Input-bound knowledge checks and supplied history

Second implementation boundary under [issue #94](https://github.com/magnus919/groktocrawl-x/issues/94)
and accepted [ADR-0075](../adr/experimental/groktocrawl-x/0075-consolidate-research-interchange-contracts.md).
This remains experimental and does not freeze `knowledge-ir/1`.

`knowledge_checks.py` defines strict fixture, tool and model reviewer metadata;
human records are rejected until attestation authority exists. Every check embeds
the full non-result `KnowledgeContext` and declares its subject, policy, exact
ordered evidence closure and nullable freshness context. Structural/conflict checks
cover the whole revision. Support/freshness/assessment checks include evidence for
the subject and its transitive premise closure, including contradictions. Producers
cannot pass a favorable subset as the complete check input.

Input bytes use JCS with `knowledge-check-input-prototype/1` as the digest domain.
Results bind the input ID/digest, verdict, time and reason; assessment results use a
separate outcome enum. No input contains a result or enclosing revision digest.
A freshness pass is rejected for unknown/missing/inapplicable dates, current claims
using historical-snapshot basis, future dates, absent as-of context, or excessive age.
These necessary temporal checks do not prove freshness or semantic support.

`checked_knowledge.py` admits `checked-knowledge-prototype/1`, containing context,
check inputs/results, assessments, per-claim assessment links, introductions and
coverage. It checks exact context equality, one result per input, complete claim
assessment mappings, and coverage derived from required question outcomes.
Unassessed claims explicitly have empty assessment links. Changing an assessment
does not mutate the proposition's claim identity.

## Time and authority

Context creation is the freeze time of acquired knowledge inputs. Check results are
outputs of evaluating that frozen context: their timestamps must be at or after it,
and at or after any freshness evaluation they report. A successor context cannot
predate the parent's context or completed check results. This avoids changing an
input digest retroactively to incorporate the time at which its result arrived.
Source acquisition/as-of chronology remains enforced by context admission.

The trusted caller supplies the expected scope/research/revision, retained prefix,
reviewer catalogue and source resolver. Each input reviewer must exactly match the
catalogue, including model/prompt/configuration metadata. **Catalogue membership is
not proof that an executor performed the check.** No publication eligibility or
human approval is issued here. Later publication must bind results to trusted
execution/attestation and preserve explicit fixture-only labeling.

`admit_checked_history()` validates up to 19 supplied predecessors plus the candidate.
Each parent ID and digest must match the actual previous canonical document.
Entity identities are immutable across the entire prefix, including removals and
reintroductions; new identities require one same-kind predecessor declaration or
explicit null. Check input/result IDs share the entity namespace and cannot alias
revision IDs. All structural/history/catalogue rejection happens before source
resolution; then every retained context resolves its exact sources again.

The function does not prove that a supplied prefix is the current stored prefix,
provide a transaction spanning source reads, or reserve quotas. Caller deadlines,
resolver authorization/lifecycle and commit-time fencing remain required. Permission,
missing-source and cancellation failures propagate. No legacy reader or stored byte
is changed, and model metadata does not authorize provider execution.

## Bounds and confirmation

The whole canonical checked envelope is limited to 1 MiB, with at most 20 revisions
in the supplied history. There are at most 6,000 check inputs, 3,000 verification
results, 3,000 assessments, 1,000 claim mappings, 100 assessment IDs per mapping and
10,000 introductions; the byte limit usually binds first. Existing context/entity
and source limits remain. Repeated complete input contexts may exhaust the bound;
no automatic context pruning or larger buffer is introduced.

`tests/unit/test_checked_knowledge.py` checks fixed canonical/input digest values,
round trips, assessment changes with stable claims, incorrect input/context/reviewer
bindings, forbidden human records, freshness denial, source closure including
contradictory premises, twenty-revision admission, invalid ancestry and identity
reuse after removal. These are structural fixture cases, not semantic-quality scores.

Remaining under #94: publication integration of the execution boundary below and
the separate audited manifest, then the bounded acquisition-to-output journey and actual retained
integration/round trip. Supplied-history validation alone does not implement that
retained integration or finish the research journey.

## Configured execution boundary

`knowledge_execution.py` adds `KnowledgeExecutionLedger`: a trusted controller
registers exact reviewer metadata with an async callback, then dispatches the
complete check input. The ledger creates the result ID, input digest and UTC
completion time itself. `check_bindings()` rejects otherwise valid knowledge when
any result was not returned by this live ledger, or any result field was changed.
It returns whether any bound input uses a fixture reviewer so later publication
can preserve fixture labeling. This return value is not a portable certificate.

Registration and clock are server configuration, not request parameters. Callbacks
are trusted in-process code; this mechanism proves callback occurrence and exact
result binding, not that a source was read or a semantic judgment was correct.
Negative and indeterminate results remain negative or indeterminate. Publication
must separately enforce required checks, assessments, conflict handling, current
source authority and lifecycle constraints. Call `check_bindings()` again at use.

One ledger belongs to one controller/event loop. Defaults are 64 issued operations,
eight concurrent callbacks and 16 KiB reserved per result. Configurable ceilings
are 6,000 operations, eight concurrent callbacks and 1 MiB per result. Retained
result bytes plus worst-case in-flight result reservations cannot exceed 1 MiB;
this is not a bound on total process memory. Admission fails immediately when full;
there is no queue or retry. Failed and cancelled calls consume their input identity
and operation allowance, but release the in-flight slot and byte reservation.
Oversized results fail without truncation. Cancellation cannot record a late pass
even if a child callback suppresses its own cancellation.

Closing the ledger invalidates receipts and rejects late completion; it does not
forcibly terminate callback code. Receipts do not survive process loss. Callers own
deadlines and shutdown, and no provider adapter, persistence, recovery, publication
endpoint or human attestation is introduced. The audited manifest, publication
gate, bounded research journey and retained integration remain under #94.

Binding also requires all inputs issued by this owner for the same frozen context.
Omitting a completed, failed or cancelled check cannot preserve publication eligibility.
A new attempt requires an explicit controller decision and newly executed records.
