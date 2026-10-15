# Lean successor fixed-source probe — 2026-09-10

Status: **complete development probe; not a quality comparison or candidate freeze**.

This bounded probe exercised the ADR-0080 journey through the home-lab LiteLLM
`local` alias. Its synthetic source contains no private or production data. The
application prepared the exact passage, the model selected source-bound answer
units in one construction call, and deterministic assembly assigned citations and
reported complete coverage. No selective review was needed because both units were
simple source statements.

## Result

- Provider calls: 1.
- Reported tokens: 1,117 total; 802 input and 315 output.
- Resolved model alias: `local`.
- Coverage: complete for both declared questions.
- Citation identity: assigned by application code to the retained passage.
- Terminal result: complete local development artifact; no retained publication.

The failed launch before this run made no provider call because the runner lacked
an explicit `LLM_BASE_URL`. This successful run used the confirmed inference.example.internal LAN
gateway address. No credential is stored here.

## Evidence identities

- `answer.json`: `sha256:db15f6ccb2333236aabd4f47d5677a0fb503899858955c132272639cd8879a09`
- `result.json`: `sha256:54c166bfebf4ed4d0546a5090193127d2d357fc9acf1c4fa1ee9ca66c22566d2`
- `usage.json`: `sha256:27bbfe1e22549942be4d6700c68e785c341e2ce0e70c838c6bb063d253f8d448`

## Limits

The source is synthetic and exposed to the implementation team. The probe did not
exercise selective review, live acquisition, conflicting evidence, storage,
recovery, or comparative quality. It supports only functional viability of the
one-call simple path. A fresh independently curated packet remains required after
the successor is frozen.
