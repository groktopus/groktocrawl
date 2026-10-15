# #373 shadow revision-pair results

Run date: 2026-10-03. Frozen input digest: `67781243e22b7b753fb059a61a452216a80cc0aeac0ff168a8177218af1156d5`.

This is a bounded exploratory shadow result on 11 constructed public revision-pair × intent cases. Reference labels were best-effort assistant assessments, not independent gold. The Jev model received only the saved intent and source-verified bounded before/after excerpts; full pages, diffs, and labels remained evaluator-side. Returned model identity was `jev-1.13.0`. No runtime policy, notifications, or deployment were changed. After calls, four published Cosign source copies were redacted to remove unrelated upstream encrypted private-key blocks; original hashes and line-preserving redaction mappings are documented in `publication-redactions.json`. No judged excerpt overlaps a redacted span.

All 11 planned calls completed once and passed response validation. There were 4 calibration and 7 validation responses, with no transport, timeout, schema, or model-identity failures. The calibration split is reported separately and was not used to change wording, threshold, labels, preprocessing, or cases. The one uncertain reference label is excluded from binary validation scoring.

On the seven determinate validation references (five material, two immaterial), the preregistered `p >= 0.50` cut agreed with all seven labels: 7/7 descriptive accuracy, 0/5 false-immaterial recommendations, 0/2 false-material recommendations, and Brier score 0.03739. The five material probabilities averaged 0.846; the two immaterial probabilities averaged 0.20. These denominators describe only this small constructed packet and are not an estimate of production error rates.

The two repeated-diff intent pairs changed in the expected direction under their assistant reference labels. The same Cosign invite-link revision scored 0.83 for contributor onboarding and 0.07 for cryptographic verification (difference +0.76). The same Kubernetes navigation-weight revision scored 0.33 for technical content and 0.71 for navigation curation (difference -0.38). These are paired, source-clustered observations, not independent cases.

Calibration had 4/4 valid typed responses; its score range was 0.14–0.92 and included the intentionally uncertain qualifier case (0.85). These values are shown as a protocol/mechanics record, not a fitted calibration curve. The failed-acquisition synthetic control made zero provider calls, as frozen.

The runner-recorded median latency was 505 ms for validation (mean 508 ms) and 536 ms for calibration (mean 551 ms). Returned usage summed to 8,896 input and 231 output tokens. The provider returned no cost, so cost remains unknown. The response's Jev model identifier does not establish which upstream inference route served the request.

## Casewise result ledger

Probabilities are the validated typed Noul values; the fixed suggestion uses the preregistered 0.50 cut. Labels are constructed assistant references, not independent gold. Token and latency columns are exact sanitized receipt metadata.

| Case | Split | Reference | p(material) | Suggestion | Input tokens | Output tokens | Latency ms |
|---|---|---:|---:|---|---:|---:|---:|
| k8s-status-v121-to-v122 | calibration | material | 0.90 | material | 805 | 21 | 641 |
| k8s-address-types-and-conditions | calibration | material | 0.92 | material | 1588 | 21 | 527 |
| k8s-nonpod-endpoint-qualification | calibration | uncertain | 0.85 | material | 840 | 21 | 492 |
| k8s-private-registry-formatting | calibration | immaterial | 0.14 | immaterial | 1104 | 21 | 545 |
| python-sysmonitoring-lifecycle | validation | material | 0.94 | material | 702 | 21 | 555 |
| python-cmdline-leading-whitespace | validation | material | 0.88 | material | 590 | 21 | 535 |
| cosign-airgap-trust-root | validation | material | 0.87 | material | 927 | 21 | 449 |
| cosign-community-invite-intent | validation | material | 0.83 | material | 654 | 21 | 504 |
| cosign-community-invite-crypto-intent | validation | immaterial | 0.07 | immaterial | 655 | 21 | 505 |
| k8s-nav-weight-content-intent | validation | immaterial | 0.33 | immaterial | 513 | 21 | 466 |
| k8s-nav-weight-navigation-intent | validation | material | 0.71 | material | 518 | 21 | 545 |

The synthetic failed-acquisition control was skipped without a provider call and remains unjudged.


Interpretation: **revise; remain shadow-only**. This run shows that the bounded typed judgment returned quickly and agreed with these constructed labels, including both intent contrasts. It does not establish incremental answer quality, independent label validity, calibration, runtime precedence over deterministic signals, or production benefit. Preserve deterministic page-change reporting as the fallback. A larger independently labeled, production-representative evaluation and an explicit runtime policy review would be required before considering any gate or user-visible behavior.

The casewise typed-probability, exact usage-token, latency, and status ledger is in `casewise-outcome-ledger.json`; it excludes provider free text and raw receipts. Full raw proxy receipts and the append-only attempt/outcome journal remain privately outside the repository.
