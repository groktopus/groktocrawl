# W7 Candidate D comparison proposal — 2026-09-10

Status: **completed; Candidate D did not pass the adoption gates**

The generation, blinded grading and one-by-one high-consequence review are
complete. See the [comparison outcome](w7-candidate-d-outcome-2026-09-11.md).
Candidate D completed 4 of 150 attempts versus 134 for Arm A and is not adopted.

This experiment asks a narrow question: does the frozen lean successor produce
more dependable grounded answers than the incumbent without repeating Candidate
B's large cost and latency penalty?

## What will happen

The incumbent and Candidate D will answer the same 30 private cases. Each case
will run five times per arm, giving 150 attempts per arm and 300 paired attempts
in total. Arm order will be randomized with a recorded seed. Both arms receive
the same question, required subquestions, as-of date, and exact captured sources.
Every failed attempt remains in the denominator.

- **Arm A — incumbent:** the source-bundle answer policy frozen at commit
  `85f7da0d815a8c24e2da4baafaa0e7e0dd13bce7`. It receives one model call per
  attempt.
- **Arm D — lean successor:** policy `lean-evidence-first/1`, frozen at the same
  commit with the identities and limits in
  [the freeze record](w7-lean-successor-freeze-2026-09-10.md). Application code
  selects exact passages, owns citation identities and coverage, and refuses
  publication when its checks fail.

The study uses the isolation-approved packet recorded in
[the packet review](w7-candidate-d-packet-2026-09-10.md). The requested model is
the `local` alias through the home-lab LiteLLM service on `inference.example.internal`.

## Hard limits

| Limit | Arm A | Arm D |
|---|---:|---:|
| Attempts | 150 | 150 |
| Calls per attempt | exactly 1 | exactly 1 construction, plus at most 1 selective review |
| Total call ceiling | 150 | 300 |
| Timeout per call | 90 seconds | 90 seconds |
| Output ceiling | 2,048 tokens | 1,536 construction; 1,024 review |
| Automatic retries | none | none |

The complete generation series therefore has a ceiling of 450 calls. Missing
provider usage remains unknown rather than being reported as zero. A failed call,
invalid response, failed publication check, or timeout is recorded as a failed
attempt and is not silently retried.

The runner refuses to start if the packet hashes, frozen implementation identity,
model route, output-directory isolation, attempt count, or call ceilings differ.
It stops the whole series if the gateway fails its initial qualification, a write
cannot be made durable, or a call would exceed its arm's ceiling. Ordinary
case-level failures do not stop later cases.

## How the result will be judged

The first decision uses facts produced by the runner:

- completion rate across all scheduled attempts;
- p50 and p95 attempt latency;
- calls and reported tokens per scheduled attempt;
- complete, partial, and insufficient coverage from deterministic output checks;
- invalid citations or references, timeouts, and other failure reasons.

After all outputs are frozen, a blinded grader will assess each completed answer
against the private question and sources. It will score source support, required
subquestion coverage, citation correctness, uncertainty, scope, and
time-awareness. Each flagged high-consequence failure will be reviewed one by one
before it can affect the adoption recommendation. Grading calls are kept in a
separate ledger and cannot alter generation outputs.

Candidate D must:

1. complete at least as many attempts as Arm A;
2. avoid any upheld dangerous unsupported recommendation;
3. improve strict source support and citation correctness without reducing
   full-study subquestion coverage;
4. stay within 1.5 times Arm A's p50 and p95 latency; and
5. use no more than 2 times Arm A's reported tokens per scheduled attempt.

Failure of either safety condition stops adoption. Missing or unusable grading
evidence produces a **revise** result rather than a win. If the quality gains are
small or mixed while operational limits pass, the decision remains **revise**.
Only clear quality improvement within every hard limit supports **continue**.

## What authorization means

Authorization permits this private paired generation and the subsequent blinded,
case-by-case grading. It does not accept proposed ADR-0080, deploy Candidate D,
remove the incumbent, or replace mainline GroktoCrawl. Those decisions require the
completed evidence and a separate maintainer decision.

Magnus Hedemark authorized this exact comparison on 2026-09-10. The private
authorization record is bound to the packet, frozen candidate, arms, model route,
trial count, and 450-call generation ceiling. Its digest is
`sha256:757bbcdf3060a3b751d2e6fa8d0a18b84a83c9b5cac4e9e5c2bff1dcf91e7834`.
