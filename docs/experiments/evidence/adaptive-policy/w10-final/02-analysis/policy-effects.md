# Policy effects

The challenge comparison separates query generation, gap binding, proposal
gating, marginal-value admission, and deterministic stopping. The table reports
the frozen model grades; it is descriptive across the fixed cases and three
repetitions.

| Policy | Closure | Precision | Trials |
|---|---:|---:|---:|
| `fixed:0` | 69.9% | 55.6% | 12 |
| `fixed:1` | 73.5% | 60.0% | 12 |
| `fixed:2` | 65.1% | 61.7% | 12 |
| `full:0` | 48.2% | 57.6% | 12 |
| `full:1` | 16.9% | 72.2% | 12 |
| `full:2` | 15.7% | 35.2% | 12 |
| `gap:0` | 79.5% | 67.9% | 12 |
| `gap:1` | 75.9% | 51.9% | 12 |
| `gap:2` | 85.5% | 44.4% | 12 |
| `gated:0` | 59.0% | 43.6% | 12 |
| `gated:1` | 54.2% | 49.4% | 12 |
| `gated:2` | 51.8% | 34.6% | 12 |
| `unconstrained:0` | 78.3% | 53.8% | 12 |
| `unconstrained:1` | 79.5% | 59.5% | 12 |
| `unconstrained:2` | 69.9% | 55.7% | 12 |

## Anchor gate

- Weighted-closure change, full versus fixed: -33.3%
- Precision change, full versus fixed: -1.5%
- Frozen non-inferiority gate: **fail**

Passing the anchor means the policy did not exceed the declared degradation
margin on the reused W8 cases. It does not itself prove a benefit.

## SOURCES

- [Machine-readable primary summary](../03-dossiers/method.md)
- [Frozen protocol](../../../../adaptive-policy/w10-frozen-protocol.md)
