# W12.2 longitudinal Research Thread outcome

Decision: **reject the tested thread as the default follow-up substrate**

## Frozen gates

| Gate | Result | Evidence |
|---|---|---|
| At least 10 points better in two of three repetitions | Fail | -18.3, -3.3, and -5.3 points |
| At least 25% lower latency | Fail | Treatment was 23.0% slower |
| No false merge, stale-current leak, or lost history | Fail | Two stale-current leaks |
| No material no-change regression | Pass | 0, -1, and -2 points |
| Terminal failures below 10% | Pass | 2 of 54 grades failed (3.7%) |

## Practical result

The conservative analysis assigns zero to the two missing treatment grades, as
frozen before execution, and produces a -9.0-point mean treatment effect. Removing
those two incomplete pairs is a useful sensitivity check: the treatment still
averages 2.8 points worse across the 25 fully observed pairs. The rejection
therefore does not depend on the missing-grade penalty.

Mean model latency rose from 20.7 seconds for control to 25.5 seconds for
treatment. Mean token use rose from 1,492 to 2,256. The explicit thread helped in
some isolated cases, including one later-resolution pair and one syndicated-copy
pair, but the gains did not repeat consistently and did not offset regressions.

The most serious behavior appeared in the live-contradiction case. Two treatment
answers preserved the historical claim but also restated superseded evidence as
current. That is precisely the kind of quiet temporal error the thread was meant
to prevent.

## Architectural implication

Keep investigation roots and source snapshots independently durable. Build an
explicit comparison report when a user asks what changed. Do not inject the
tested accumulated thread representation into every follow-up prompt or treat it
as an authority-bearing knowledge object.

The evidence still supports preserving stable identities, source lineage, and
historical claim status as narrow internal records. A future continuity feature
must have a smaller trigger and must show that it improves the user's answer
without increasing stale-current leakage.

This report is a mechanical rendering of the retained grades. Case-level answers,
receipts, failures, and the sensitivity calculation remain part of the packet and
should be reviewed before the proposed ADR is accepted.
