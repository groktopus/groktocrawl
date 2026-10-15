# Frozen synthesis replay for the Jev retention study

**Study:** GCX-JEV-SYNTH-371. **Protocol status:** the original call sequence was
stopped after the initial unknown-delivery proxy attempt and one later R1
provider response returned `finish_reason=length` for a request with
`max_tokens=1600`. No usable complete answer was
received; no further calls were made. See
[synthesis-attempt-outcome.md](evidence/jev-retention-2026-10-03/synthesis-attempt-outcome.md)
and the separate R1 addendum before considering any further replay. This replays
the existing, partial GroktoCrawl supply-chain search snapshot. It is not a fresh
search and does not validate upstream reranking.

## Frozen inputs and equivalence

The research question is exactly:

> software supply chain provenance SLSA reproducible builds attestations verification

Use the 20 already acquired pages in rank order. The source score/result file
SHA-256 is
`53ae3ab42bf41cb37da89186d61a3a4a58cb8d72724766bcefb175a799ca85a4`; the
pre-Jev label-freeze SHA-256 is
`91fba7f976d681432334baaffdee29848b600fa392dc57b71c81b391dd10c4e9`. The
search-snapshot SHA-256 is
`8c94ecf1e09a5e005fc7692b29bc0519233d64a7a5d057eb246428b025b14904`. The
ordered page/score manifest SHA-256 is
`2709a06d2877d71ce73519925dea5583fd157910fac9891cd4d9c99a6073729b`. Before
each call, validate every page digest, character count, source ID, rank, URL,
and score against those frozen artifacts. Keep full page text and model output
in private, access-restricted storage; publish only aggregate scores and
digests.

Build each source block by instantiating the repository's `SourceArtifact` and
calling `to_document(max_chars=...)`; join blocks in frozen order using the
production delimiter `\n\n---\n\n`. The exact full/no-score context SHA-256 is
`98831a480b0113be86395d2d61316082b46568327e9c5719556c746b7f681727`; the
score-bearing full context SHA-256 is
`e6da0e59a42f0c5af1cf7e07e29c652d846046363f038bb8543594b19141f91a`; the
no-score 8,000-character-per-page projection SHA-256 is
`4451c0b865c356211af45dec40ff71fbe69581f625e15c227036ce0ed44610f9`.
Their exact request payload digests, using model alias `free`, temperature
0.3, and maximum output 1,600 tokens, are respectively:

- Full text, no score metadata: `a7d4310e611e972f35d10cb617e535c0bb195bcd110bfe9096df9491f062d41b`.
- Full text, Jev scores: `d4affc359f8e961b93e9be46cebf4979eab8dd0018961c6b87db917a5d997d94`.
- No-score 8,000-character projection: `281a9663e553dcfbe64600ece08c1364a9d6a86d8c60284fb32d4334054ce189`.

Use the same base research system prompt plus the existing Jev score-interpretation
instruction in every arm, the same question, model alias, temperature, output
limit, and context delimiter. This deliberately holds instructions fixed so
the comparison isolates score metadata and text projection. It is not an exact
replay of the keyless production prompt, which omits the Jev-specific
instruction.

Every one of the 20 existing scores is at least 0.83, above the configured
0.10 threshold. Thus the existing-filter full-text arm and the score-only
full-text arm have the same 20 sources and identical request payload digest.
Represent both by one model call; do not spend a duplicate call on identical
input. The study therefore has three distinct calls: full text without scores,
full text with scores (also the existing-filter arm), and no-score 8,000-
character projection. The filter's source-removal effect is not identifiable
in this batch. This run can compare score metadata and the prefix projection;
it cannot establish general answer uplift or threshold quality.

The full score-bearing source context contains 330,372 characters including
source headers and separators. Prior per-page Jev accounting reported 79,886
input tokens over this same 20-page packet. The free alias metadata declares
1,048,576 input tokens. That metadata is not empirical routed-capacity proof;
the bounded replay is permitted because the exact input is a normal-sized
study packet below that declaration. Do not use or imply a full-system
production route or 34-page packet.

## Call and stop bounds

After independent review of this protocol and the corrected capacity helper,
make at most three distinct sequential calls through the existing private
`free` alias proxy. Each request is capped at 1,600 output tokens, one in
flight, with no retries. Do not print credentials, endpoints, complete source
text, or raw provider errors. Preserve sanitized status/error category,
input digest, returned model metadata, usage, finish reason, elapsed time, and
output digest in a private receipt. If any call errors, is rate-limited,
refused, truncated, or returns no usable text, record that outcome and stop;
do not substitute another alias or silently rerun. Do not initiate calls if
the proxy reports an unexpected alias, unhealthy service, or input integrity
failure.

No search, page reacquisition, Jev call, deployment, feature activation,
configuration change, or threshold change is in scope. No request includes
more than the 20 frozen pages.

## Frozen answer review rubric

Before seeing completions, independently grade each response against these six
facets derived from the frozen contribution labels:

1. What SLSA build provenance is and what assurance it provides.
2. Provenance/attestation structure and the information it records.
3. A concrete verification procedure, including how verification differs
   from signing or generation.
4. SLSA levels/tracks and version or draft-status claims, stated with the
   qualification supported by the provided pages.
5. How reproducible builds relate to provenance and where their guarantees
   differ or stop.
6. Trust limits, caveats, and any material disagreement in the sources.

Score each facet 0 (omitted, materially wrong, or unsupported), 1 (partially
covered with a relevant URL citation), or 2 (specific, qualified, accurate to
the frozen pages, and cited to a directly supporting source); maximum 12.
Separately count factual claims, claims with a correct URL citation, and
unsupported or contradicted claims. For every frozen contribution span, record
whether the answer materially uses that passage and whether the cited URL points
to the supporting page. Track whether material contradictions are surfaced
with both sources and appropriate qualification. Preserve refusals, malformed
answers, and truncation as failures rather than scoring them zero.

These are single-run descriptive comparisons on one partial snapshot. The
frozen passage labels are one reviewer's best-effort labels, not independent
gold. Do not claim statistical significance, broad recall, causal answer
uplift, or adoption readiness from these calls. Parent review of outputs should
be blind to arm identity until the pairwise/facet scoring is frozen.
