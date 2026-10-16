# Held-out packet contract

Status: **packet isolated and eligible for this experiment; no comparison is authorized**.

The future W1 packet is a separate directory with two files:

- `corpus.json` contains at least 30 unique cases with `split` set to
  `held_out_candidate`, six or more topic categories and template families, at
  least 20% adverse or abstention cases, fixed `as_of` values, source IDs and
  required subquestion IDs. It contains no author expectations or exposed flags.
- `access-log.json` records the curator, sealing time, every access entry,
  `covers_all_cases: true`, and `implementation_visible: false`.

Every source must carry a lineage ID and a SHA-256 digest matching its exact text.
The packet must be checked against the exposed corpus for duplicate case IDs and
normalized question text. The check reports digests for the corpus and access log,
but a digest is only an integrity check; it cannot prove that the packet stayed
isolated.

Run the validator before requesting review:

```sh
python3 scripts/validate_heldout_packet.py \
  --packet /path/to/separate-heldout-packet \
  --known-corpus docs/experiments/enterprise-evaluation/corpus.json \
  --output /tmp/heldout-validation.json
```

The validator returns a candidate validation report and always leaves
`held_out_eligible` false. That is deliberate: a hash and a structural check
cannot prove independence. A separate named-review approval record must bind the
reviewer, access-log digest, corpus digest, and isolation method before the
packet can be marked eligible. The 2026-09-08 approval is summarized in
[w1-heldout-approval-2026-09-08.md](w1-heldout-approval-2026-09-08.md); the raw
approval record and packet remain outside the repository. Eligibility does not
authorize a scored comparison or production adoption.
