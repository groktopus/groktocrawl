# Consolidated format review record

A separately authorized Hermes one-shot design review was attempted on 2026-09-06
with a 180-second subprocess timeout, `--ignore-rules -t todo -z`, and an instruction
to use no tools. The exact submitted packet is in `prompt.txt`, pinned in
`result.json`. The subprocess timed out without stdout response; no independent
review was completed. Model identity and usage are not established by this record.
This is not a zero-cost claim or a semantic evaluation run. No retry was issued.

The author subsequently tightened the proposal to bind every check to a full,
explicit non-result context; specify the additional freshness field and subject /
evidence rules; distinguish deterministic whole-envelope validation from recorded
structural results; and restrict fixture results to labeled fixture publication.
Those are author-review changes, not Hermes findings. The submitted prompt preserves
the earlier draft exactly; it does not imply that Hermes reviewed the revised draft.

ADR-0075 remains proposed. Merging the proposal, passing documentation checks and
running existing compatibility tests do not constitute independent design approval,
format freeze, human calibration or architecture adoption.
