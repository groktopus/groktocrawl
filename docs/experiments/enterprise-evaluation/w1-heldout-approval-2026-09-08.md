# W1 held-out isolation approval

Status: **isolation was approved for the incumbent run; post-review exposure now
prevents using this packet for a later candidate comparison**.

On 2026-09-08, Magnus Hedemark reviewed and approved the private packet's
curator, access history, and isolation method. The packet is therefore eligible
to serve as held-out evidence for this study. This approval is limited to
eligibility: it does not authorize a scored comparison, production adoption, or
replacement of mainline GroktoCrawl.

The packet remains outside the repository and tuning workspace in a mode-700
temporary directory. It contains 30 unique cases, six topic categories, six
template families, and six adverse or abstention cases. Structural validation
passed without publishing question or source contents, and no case-ID or
normalized-question overlap with the exposed corpus was found.

The sealed evidence is identified by these digests:

| Evidence | Digest or value |
|---|---|
| Held-out corpus | `sha256:659200d31ea23b17dc497b658c6c44db277005ed91a74fb962b10c6cebbf27c3` |
| Access log after named review | `sha256:41e0d1be2c6ae5a8e35a628c5cb0c8bc7ec7d4dfce875bf98634b9817957d155` |
| Validator status | `candidate_validation_passed` |
| Held-out eligibility | `true` |
| Comparison authorization | `false` |

The incumbent reference run was completed first through the internal local
LiteLLM route with zero external provider spend. It attempted all 30 cases,
returned 29 valid answers and one malformed response, and observed p50 latency
of 6,424 ms, p95 latency of 8,283 ms, and 8,388 reported total tokens. These
are baseline observations only; they are not quality scores or performance
bounds.

The private approval record is retained at the packet location and is not
committed here. The next gate is to freeze the numerical quality, latency,
resource, budget, ordering, and uncertainty rules against this baseline before
running the candidate arms. The fork remains an explicitly experimental,
separate project and makes no mainline replacement claim.

## Post-review exposure — 2026-09-09

After the incumbent run, the Codex implementation agent reviewed every private
case to produce the primary semantic baseline. The access log now records
`implementation_visible=true` with digest
`sha256:d6b31f8e034981e5c6a308b3d79da69a3afcc93e28e000d9ad899f935ad82107`.
The validator consequently returns `candidate_validation_failed` for future
held-out use. This does not invalidate the incumbent observation or its use for
calibration and bound setting. It does mean a candidate implemented after this
review cannot be compared against these cases as if they were still unseen.

Freeze candidate B without case-specific tuning, then curate and seal a fresh
independent packet for the scored A/B comparison. The original approval remains
an accurate record of the pre-run isolation decision at its recorded digest.
