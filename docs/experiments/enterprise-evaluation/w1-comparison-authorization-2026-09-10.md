# W1 A/B comparison authorization — 2026-09-10

Status: **authorized for execution**

On 2026-09-10, Magnus Hedemark authorized the W1 comparison. The authorization
has this exact scope:

- Arm A is the incumbent answer policy at commit
  `34b4975bc7baaf25510ed34029b957f95b59de70`.
- Arm B is the evidence-first policy `real-research-pilot/3` at commit
  `36f999514cf0dc8eb3164c4cac50a7ba0002618f`.
- The study uses the isolation-approved private 30-case replacement packet whose
  identities are recorded in
  [the packet review](w1-replacement-packet-2026-09-10.md).
- The incumbent's broad-cluster-authority recommendation is accepted as a real
  critical failure and remains in the comparison record.
- The quality, latency, resource, ordering, call, and uncertainty limits in
  [the incumbent semantic review](w1-incumbent-semantic-review-2026-09-09.md#proposed-comparison-bounds)
  are accepted.

The private authorization record is bound to the packet and arm identities above.
Its digest is
`sha256:119e8e18338e1ac5a6206646caaa964a2e77d139f3abe04933740fe92596a324`.

This authorizes the paired A/B experiment only. It does not authorize production
adoption, removal of the incumbent, or replacement of mainline GroktoCrawl.

## Call-budget correction

Magnus approved one execution correction after the runner design exposed a mismatch:
30 cases times five trials requires 150 attempts per arm, while Arm B makes two
ordered model calls for every attempt. The corrected ceilings are 150 calls for
Arm A, 300 calls for Arm B, and 450 calls for the complete series. Every other
quality, latency, resource, retry, route, and stop limit remains unchanged.

The private amendment record has digest
`sha256:f0fe18cfca535d3bb1652e5eb21d6d72f57e5140167c97bde4cd1fdc926bf8ec`.
