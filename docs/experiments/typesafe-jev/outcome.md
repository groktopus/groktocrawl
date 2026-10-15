# TypeSafe Jev #360 research outcome

Decision: **do not ship or enable Jev passage triage from this spike; defer
integration.** This is a decision about the tested GroktoCrawl use and frozen
policy, not a claim that Jev is intrinsically unsuitable or that its separate
SlopSearX engine-routing use lacks value. The adapter remains shadow-only.

The first frozen synthetic batch stopped during calibration after provider-call
failures. It never reached validation, so the original issue closure was
premature. Subsequent diagnostics and a separately frozen synthetic run showed
that the endpoint and adapter could work, though the first failure's exact
cause could not be recovered from its limited ledger. The synthetic-selected
exclusion cutoff was too conservative on first-party GroktoCrawl documents.
Two public-document sets became exploratory calibration material, and a
real-data cutoff was frozen before public-web validation.

That first web packet passed its evidence-selection checks: passages labeled
necessary or premise-challenging were retained while nonrequired passages
were removed. But most removals were obvious cross-topic or unanswerable
material that had been deliberately added to the packet. The harder near
matches remained. The packet was constructed and assistant-labeled, not a
random or independently judged production sample.

The owner then corrected a flaw in the evaluation structure. Injection risk
and evidence usefulness are independent assessments. A safety concern can
veto use of a relevant passage, but a safety-classifier failure cannot erase
the measured relevance result. The original combined gate remains a recorded
failure; it is not retroactively called a pass. A deterministic research-only
harness check confirmed the intended precedence and fallback on frozen
receipts. It also quarantined benign security guidance alongside the explicit
synthetic injection, so Jev's injection score is **not** validated as a
standalone safety gate. Source provenance, existing safety controls, and
content-as-data boundaries remain authoritative.

A final relevance-only comparison used two new GroktoCrawl searches and their
ranked public-page results, without planting unrelated distractors. Passages,
labels, a fixed cutoff, stop conditions, and two repetitions were frozen before
Jev calls. No passage was excluded in either repetition. Useful evidence was
preserved, but related-yet-insufficient passages remained, so the predeclared
practical-improvement gate failed. This supports **no ship for this passage
triage rule**: the earlier apparent gain did not transfer to this more natural
ranked set. It does not justify changing the cutoff on these validation cases
or expanding to reranking or citation checking under #360.

Final-answer and citation quality were not run as a separate generative
comparison: the treatment and control source sets were identical in the final
ranked packet, so this Jev rule created no controlled input difference for
such a comparison. No user-visible answer improvement is claimed. The labels
were best-effort assistant reviews, not independent or blinded; the packet
covered two technical topics and is not a production-traffic estimate.
Provider-specific measurements and raw ledgers remain in owner-only storage
outside the public repository pending publication review.

The keyless API, CLI, and MCP paths remain unchanged, and key presence alone
does not activate Jev. No SlopSearX code or behavior changed. No ADR selecting
Jev is proposed because no Jev architecture was adopted. A future proposal
would need a distinct, prospectively frozen use case and evidence of
user-visible benefit before any implementation decision.
