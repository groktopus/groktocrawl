# Findings

## Decision

Keep fixed-query retrieval as the default. The tested adaptive policy did not clear every frozen replacement gate.

## What the experiment established

- The complete record contains 540 completed trials and
  0 failed trials.
- The W8 anchor gate did not pass.
- The primary decision selected 0 adaptive challenge type(s).
- Independent adjudication did not change the
  decision in the declared sensitivity analysis. Its result was `retain_fixed_default`.

## Practical consequence

Any adaptive behavior should use the tested trigger, proposal, admission, stop,
and budget rules. Results do not justify unbounded autonomous search or claims
outside the frozen domain and cases.

## SOURCES

- [Policy effects](../02-analysis/policy-effects.md)
- [Boundary and sensitivity analysis](../02-analysis/boundaries-and-sensitivities.md)
- [Study accounting](../03-dossiers/accounting.md)
