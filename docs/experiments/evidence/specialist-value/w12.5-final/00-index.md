# W12.5 final evidence packet

Decision: retain the generalist as the default and reject generic specialist
fan-out. Preserve the typed handoff boundary for future, narrower experiments.

## Result

- 36 frozen paired reviews were scheduled; 33 completed and 3 remained invalid
  after bounded retries.
- The specialist treatment raised structural evidence coverage by 66.7 percentage
  points on the separable stratum and preserved both seeded contradictions.
- Blinded usefulness improved by only 1.1, 1.2, and 9.1 points across the three
  repetitions, below the preregistered 10-point requirement every time.
- Simple cases were unchanged and total treatment calls stayed within the 50%
  ceiling.
- Three terminal malformed reviewer records and inconsistent 0–100 score use
  failed the hard reliability gate.

## Contents

- `reviews/`: the final record for every case and repetition.
- `failures/`: the initial transport failure and retained malformed-response
  records from bounded retries.
- `summary.json`: terminal completion counts.
- `analysis.json`: preregistered gate calculation.
- `manifest.sha256`: hashes for the 46 evidence files present before the manifest.

Manifest SHA-256:
`2405541b16ee60f09c0a87286871c4503564fbb09d2376e01a0cb008e74540c0`.
