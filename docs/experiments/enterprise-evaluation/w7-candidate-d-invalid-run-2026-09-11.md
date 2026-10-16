# W7 Candidate D invalid generation run — 2026-09-11

Status: **operationally invalid; preserved and excluded from grading**

The first authorized Candidate D comparison scheduled all 300 attempts, but a
temporary failure of the home-lab `local` model route made the result unusable for
architecture comparison. After attempt 70, the run recorded 230 consecutive fast
transport failures. Only 28 attempts completed: 25 for Arm A and 3 for Arm D.

Across the complete record, 236 model calls failed in transport. Arm A recorded
25 completed and 125 failed attempts. Arm D recorded 3 completed and 147 failed
attempts. The failures affected both arms and overwhelm any policy-quality signal.
No semantic grading or W7 decision may use this run.

The route advertised and completed a minimal `local` request after the run, which
is consistent with a temporary outage but does not establish a root cause. The
failed run remains private and immutable with these identities:

- schedule: `sha256:0ae9a16bb120423ea00612de94d42f898f37dad43340a077c45dc543225483bc`;
- results: `sha256:c26caa496cfa2a81296aa72ae0482801f8f133e4e24a3766337d456cbe7d8c46`;
- receipts: `sha256:e87a8f989e8a922e47e9740ce7702a060005a81167ed99ad61b88480eda9b470`;
- manifest: `sha256:962a9d9755fd683767cc07daf879b361831bd761428d8269efb68fbb0fe54477`.

Magnus authorized a clean rerun on 2026-09-11. The rerun keeps the same packet,
candidate, arms, seed, trials, model alias and call ceilings. A preflight completion
must resolve to `local`, and five consecutive transport failures stop the whole
series with an `operational_abort` manifest instead of filling the schedule with
meaningless failures. Individual semantic or schema failures still remain ordinary
failed attempts and are never retried.

The private clean-rerun authorization digest is
`sha256:09f1005033afa78218d00d912f7df315354b03e28fb276e6cf365c6b8e790fca`.
